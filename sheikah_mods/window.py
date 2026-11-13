"""Focused Sheikah Mods windows, with no pack-management controls."""
import os
from pathlib import Path
import threading
import tkinter as tk

import tavern_shared.theme as _theme
import tavern_shared.window_chrome as _chrome

from . import core, melon, switching, ui_state

def background(widget, work, done, progress=None):
    box = []
    latest = []
    lock = threading.Lock()

    def emit(text):
        with lock:
            latest[:] = [text]

    def run():
        try:
            box.append((work(emit) if progress else work(), None))
        except Exception as error:
            box.append((None, error))

    threading.Thread(target=run, daemon=True).start()

    def poll():
        try:
            with lock:
                update = latest.pop() if latest else None
            if update is not None and progress:
                progress(update)
            if box:
                done(*box[0])
            else:
                widget.after(150, poll)
        except tk.TclError:
            pass

    widget.after(150, poll)


def wait_for(root, ready, text):
    """Keep Tk responsive while a launch waits for an update operation."""
    if ready():
        return
    finished = tk.BooleanVar(master=root, value=False)
    dialog = tk.Toplevel(root)
    dialog.withdraw()
    dialog.title(core.NAME)
    dialog.transient(root)
    dialog.configure(bg=_theme.BG)
    dialog.message = tk.Label(dialog, text=text, padx=24, pady=20, wraplength=400,
                              bg=_theme.BG, fg=_theme.PARCH, font=("Segoe UI", 10))
    dialog.message.pack()
    dialog.update_idletasks()
    x = root.winfo_rootx() + max(0, (root.winfo_width() - dialog.winfo_reqwidth()) // 2)
    dialog.geometry("+%d+%d" % (x, root.winfo_rooty() + 60))
    cancelled = []
    root._sheikah_wait_dialog = dialog

    def cancel():
        cancelled.append(True)
        finished.set(True)

    def destroyed(event):
        if event.widget is dialog and not finished.get():
            cancel()

    def show():
        if dialog.state() != "withdrawn":
            return
        dialog.deiconify()
        dialog.grab_set()

    def poll():
        nonlocal poll_id
        if ready():
            finished.set(True)
        else:
            poll_id = dialog.after(50, poll)

    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.bind("<Destroy>", destroyed)
    show_id = dialog.after(250, show)
    poll_id = dialog.after(50, poll)
    try:
        root.wait_variable(finished)
    finally:
        root._sheikah_wait_dialog = None
        try:
            dialog.after_cancel(show_id)
            dialog.after_cancel(poll_id)
            dialog.destroy()
        except tk.TclError:
            pass
    if cancelled:
        raise core.Cancelled("Launch cancelled. The Sheikah Mods check or update will finish in the background.")


def run_waiting(root, work, text, progress=None):
    if root is None:
        return work(progress) if progress else work()
    result = []
    root._sheikah_pending = True

    def done(value, error):
        root._sheikah_pending = False
        result.append((value, error))

    if progress:
        background(root, work, done, progress=progress)
    else:
        background(root, work, done)
    wait_for(root, lambda: bool(result), text)
    value, error = result[0]
    if error:
        raise error
    return value


class ScrollList(tk.Frame):
    def __init__(self, parent, height):
        t = _theme
        super().__init__(parent, bg=t.SURF, highlightbackground=t.BORDER, highlightthickness=1)
        self.height = height
        self.canvas = tk.Canvas(self, bg=t.SURF, highlightthickness=0, bd=0, height=height)
        self.scroll = t._mk_scrollbar(self, self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=t.SURF)
        self.window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)

    def wheel(self, event):
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def contains(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False


def sections(plan, include_ready=False):
    groups = []
    for actions, title, color in ((('update', 'replace'), 'To update', 'AMBER'),
                                  (('install',), 'To install', 'GREEN'),
                                  (('remove', 'restore'), 'No longer on the list', 'RED'),
                                  (('keep',), 'Your files kept', 'MUTED')):
        rows = []
        for change in plan.changes:
            if change.action in actions:
                rows.append(mod_row(plan.game_dir, change.name, change.action.upper(), change.note))
        if rows:
            groups.append((title, color, rows))
    if include_ready and plan.ready:
        groups.append(('Ready', 'GREEN', [mod_row(plan.game_dir, name, 'READY') for name in plan.ready]))
    return groups


def mod_row(game_dir, name, badge, note=''):
    info = melon.read_info(str(switching.storage_folder(game_dir) / name)) or {}
    detail = name + ('  ·  Installed ' + info['version'] if info.get('version') else '')
    return (info.get('name') or name[:-4], detail + ('\n' + note if note else ''), badge)


class ModList(ScrollList):
    def fill(self, plan, include_ready=False):
        self.clear()
        t = _theme
        for title, color, rows in sections(plan, include_ready):
            color = getattr(t, color)
            card = tk.Frame(self.inner, bg=t.SURF)
            card.pack(fill='x', padx=16, pady=(14, 2))
            tk.Label(card, text='%s  ·  %d' % (title.upper(), len(rows)), bg=t.SURF, fg=color,
                     font=('Segoe UI', 9, 'bold'), anchor='w').pack(fill='x', pady=(0, 10))
            for name, detail, badge in rows:
                row = tk.Frame(card, bg=t.SURF)
                row.pack(fill='x', pady=(0, 12))
                tk.Label(row, text=badge, width=8, padx=6, pady=3, bg=t.BG, fg=color,
                         font=('Segoe UI', 8, 'bold')).pack(side='right', anchor='n', padx=(8, 0))
                content = tk.Frame(row, bg=t.SURF)
                content.pack(side='left', fill='x', expand=True)
                for text, fg, bold in ((name, t.PARCH, True), (detail, t.MUTED, False)):
                    label = tk.Label(content, text=text, bg=t.SURF, fg=fg, anchor='w', justify='left',
                                     wraplength=430, font=('Segoe UI', 9, 'bold' if bold else 'normal'))
                    label.pack(fill='x', pady=(0, 2))
                    content.bind('<Configure>', lambda event, label=label: label.configure(
                        wraplength=max(100, event.width)), add='+')


def _header(parent, subtitle):
    t = _theme
    header = tk.Frame(parent, bg=t.BG)
    header.pack(fill='x', padx=24, pady=(20, 16))
    tk.Label(header, text='SHEIKAH SANCTUARY', bg=t.BG, fg=t.AMBER,
             font=('Segoe UI', 9, 'bold'), anchor='w').pack(fill='x')
    tk.Label(header, text=core.NAME, bg=t.BG, fg=t.PARCH,
             font=('Georgia', 24, 'bold'), anchor='w').pack(fill='x', pady=(6, 6))
    label = tk.Label(header, text=subtitle, bg=t.BG, fg=t.MUTED, font=('Segoe UI', 10),
                     anchor='w', justify='left', wraplength=620)
    label.pack(fill='x')
    header.bind('<Configure>', lambda event: label.configure(wraplength=max(200, event.width)))
    return label


def _size(widget, parent):
    widget.update_idletasks()
    width, height = min(740, widget.winfo_screenwidth() - 60), min(720, widget.winfo_screenheight() - 100)
    x = max(0, (widget.winfo_screenwidth() - width) // 2)
    y = max(0, (widget.winfo_screenheight() - height) // 2)
    widget.geometry('%dx%d+%d+%d' % (width, height, x, y))
    widget.minsize(min(600, width), min(480, height))
    if parent is not None and parent.winfo_viewable():
        widget.transient(parent)
    _chrome._finish_dark_window(widget)
    widget.lift()


class ReviewDialog(tk.Toplevel):
    def __init__(self, parent, plan, launching=False):
        super().__init__(parent)
        _chrome._start_hidden(self)
        t = _theme
        self.configure(bg=t.BG)
        self.title(core.NAME + ' — Review changes')
        self.choice = False
        installs = sum(c.action == 'install' for c in plan.changes)
        updates = sum(c.action in ('update', 'replace') for c in plan.changes)
        removals = sum(c.action in ('remove', 'restore') for c in plan.changes)
        _header(self, '%d to install  ·  %d to update  ·  %d leaving the list\n%s' % (
            installs, updates, removals, core.HOST))
        footer = tk.Frame(self, bg=t.BG)
        footer.pack(side='bottom', fill='x')
        tk.Frame(footer, bg=t.BORDER, height=1).pack(fill='x')
        tk.Label(footer, text='Skipped changes stay skipped for this session. Check now lets you reconsider.',
                 bg=t.BG, fg=t.MUTED, wraplength=600, justify='left', anchor='w',
                 font=('Segoe UI', 9)).pack(fill='x', padx=24, pady=(12, 8))
        actions = tk.Frame(footer, bg=t.BG)
        actions.pack(fill='x', padx=24, pady=(0, 18))
        self.apply_button = t._btn(actions, 'Apply & launch' if launching else ui_state.action(plan)[0],
                                   lambda: self.choose(True), 'primary', padx=16, pady=8,
                                   font=('Segoe UI', 10, 'bold'))
        self.apply_button.pack(side='right')
        self.skip_button = t._btn(actions, 'Launch without updating' if launching else 'Skip for now',
                                  lambda: self.choose(False), padx=12, pady=8, font=('Segoe UI', 9))
        self.skip_button.pack(side='right', padx=8)
        self.list = ModList(self, 350)
        self.list.pack(fill='both', expand=True, padx=24, pady=(0, 16))
        self.list.fill(plan)
        self.bind('<MouseWheel>', self.list.wheel)
        self.bind('<Escape>', lambda event: self.choose(False))
        self.protocol('WM_DELETE_WINDOW', lambda: self.choose(False))
        _size(self, parent)
        self.skip_button.focus_set()
        self.grab_set()

    def choose(self, apply):
        self.choice = apply
        self.destroy()


def ask_updates(parent, plan, launching=False):
    previous = parent.grab_current() if parent is not None else None
    dialog = ReviewDialog(parent, plan, launching)
    try:
        dialog.wait_window()
        return dialog.choice
    finally:
        if previous is not None:
            try:
                if previous.winfo_viewable():
                    previous.grab_set()
            except tk.TclError:
                pass


class SheikahWindow(tk.Toplevel):
    def __init__(self, root, game_dir, check, apply):
        super().__init__(root)
        _chrome._start_hidden(self)
        self.game_dir = game_dir
        self._rendered_plan = None
        self.configure(bg=_theme.BG)
        self.title(core.NAME)
        self.subtitle = _header(self, 'The Sheikah Sanctuary client mod list, ready to install and keep up to date.')
        footer = tk.Frame(self, bg=_theme.BG)
        footer.pack(side='bottom', fill='x', padx=24, pady=(10, 18))
        self.status = tk.Label(footer, text=core.HOST, bg=_theme.BG, fg=_theme.MUTED,
                               anchor='w', justify='left', wraplength=620, font=('Segoe UI', 9))
        self.status.pack(fill='x', pady=(0, 10))
        tk.Label(footer, text='Automatic: Sheikah mods load only for att.aservertown.fans:1757.\n'
                 'Other servers and Main Menu use your regular mods.', bg=_theme.BG, fg=_theme.MUTED,
                 anchor='w', justify='left', wraplength=620,
                 font=('Segoe UI', 9)).pack(fill='x', pady=(0, 10))
        buttons = tk.Frame(footer, bg=_theme.BG)
        buttons.pack(fill='x')
        self.check_button = _theme._btn(buttons, 'Check now', check, padx=12, pady=8, font=('Segoe UI', 9))
        self.check_button.pack(side='left')
        folder_button = _theme._btn(buttons, 'Open Mods folder', lambda: os.startfile(str(Path(game_dir, 'Mods'))),
                                    padx=12, pady=8, font=('Segoe UI', 9))
        folder_button.pack(side='left', padx=8)
        if not Path(game_dir, 'Mods').is_dir():
            folder_button.configure(state='disabled')
        self.apply_button = _theme._btn(buttons, 'Check mods', apply, 'primary',
                                        padx=16, pady=8, font=('Segoe UI', 10, 'bold'))
        self.apply_button.pack(side='right')
        self.apply_button.configure(state='disabled')
        self.list = ModList(self, 350)
        self.list.pack(fill='both', expand=True, padx=24)
        self.bind('<MouseWheel>', self.list.wheel)
        _size(self, root)

    def show(self, plan=None, status=None, busy=False, issue=None):
        if plan is not None:
            location = 'Parked for other servers.' if switching.is_parked(plan.game_dir) else 'In Mods for Sheikah.'
            self.subtitle.configure(text='%d of %d listed mods match the Sheikah list.\n%s' % (len(plan.ready), len(plan.mods), location))
            if plan is not self._rendered_plan:
                self.list.fill(plan, include_ready=True)
                self._rendered_plan = plan
        elif not busy:
            self.list.clear()
            self._rendered_plan = None
            self.subtitle.configure(text='Choose Check mods to inspect the selected game.')
        self.check_button.configure(state='disabled' if busy else 'normal')
        label, enabled = ui_state.action(plan, busy, issue)
        self.apply_button.configure(text=label, state='normal' if enabled else 'disabled')
        if status is not None:
            self.status.configure(text=status, fg=_theme.AMBER if issue is not None or (plan is not None and plan.offline) else _theme.MUTED)
