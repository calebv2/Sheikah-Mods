"""Sheikah Mods windows for reviewing, adding, and switching packs."""
import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import urlsplit

import tavern_shared.theme as _theme
import tavern_shared.window_chrome as _chrome

from . import compatibility, core, history, melon, packs, switching, ui_state

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
        pack = plan.pack or packs.SHEIKAH
        source = urlsplit(pack.manifest_url).hostname
        release = getattr(plan.mods, 'release', '')
        name = pack.name + (f' {release}' if release else '')
        _header(self, '%s · %s\n%d to install  ·  %d to update  ·  %d leaving the list' % (
            name, source, installs, updates, removals))
        footer = tk.Frame(self, bg=t.BG)
        footer.pack(side='bottom', fill='x')
        tk.Frame(footer, bg=t.BORDER, height=1).pack(fill='x')
        if plan.warnings:
            _theme._btn(footer, 'Compatibility details',
                        lambda: review_text(self, 'Compatibility details', plan.warnings),
                        padx=12, pady=4).pack(anchor='w', padx=24, pady=(8, 0))
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


def ask_updates(parent, plan, launching=False, restoring=False):
    previous = parent.grab_current() if parent is not None else None
    dialog = ReviewDialog(parent, plan, launching)
    if restoring:
        dialog.apply_button.configure(text='Restore saved version')
        dialog.skip_button.configure(text='Cancel')
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


def review_text(parent, title, lines, confirm=None):
    dialog = tk.Toplevel(parent)
    _chrome._start_hidden(dialog)
    dialog.configure(bg=_theme.BG)
    dialog.title(title)
    choice = False
    previous = parent.grab_current()
    _header(dialog, title)
    footer = tk.Frame(dialog, bg=_theme.BG)
    footer.pack(side='bottom', fill='x', padx=24, pady=18)

    def accept():
        nonlocal choice
        choice = True
        dialog.destroy()

    if confirm:
        _theme._btn(footer, confirm, accept, 'primary', padx=12, pady=7).pack(side='right')
    close = _theme._btn(footer, 'Cancel' if confirm else 'Close', dialog.destroy, padx=12, pady=7)
    close.pack(side='right', padx=(0, 8))
    body = tk.Frame(dialog, bg=_theme.BG)
    body.pack(fill='both', expand=True, padx=24)
    text = tk.Text(body, bg=_theme.SURF, fg=_theme.PARCH, wrap='word',
                   font=('Segoe UI', 10), padx=12, pady=10, relief='flat')
    scrollbar = _theme._mk_scrollbar(body, text.yview)
    scrollbar.pack(side='right', fill='y')
    text.configure(yscrollcommand=scrollbar.set)
    text.pack(side='left', fill='both', expand=True)
    text.insert('1.0', '\n'.join(lines))
    text.configure(state='disabled')
    dialog.bind('<Escape>', lambda event: dialog.destroy())
    _size(dialog, parent)
    close.focus_set()
    dialog.grab_set()
    try:
        dialog.wait_window()
    finally:
        if previous is not None:
            try:
                if previous.winfo_viewable():
                    previous.grab_set()
            except tk.TclError:
                pass
    return choice


class ManifestDetailsDialog(simpledialog.Dialog):
    def __init__(self, parent, game_dir):
        self.game_dir = game_dir
        super().__init__(parent, title="Manifest details")

    def body(self, master):
        self.entries = {}
        for row, (key, label) in enumerate((
            ("release", "Release label"),
            ("game_builds", "Supported game builds"),
            ("melonloader_versions", "Supported MelonLoader versions"),
        )):
            ttk.Label(master, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=5)
            entry = ttk.Entry(master, width=45)
            entry.grid(row=row, column=1, sticky="ew", padx=8, pady=5)
            self.entries[key] = entry
        ttk.Label(master, text="All fields are optional. Separate multiple versions with commas.").grid(
            row=3, columnspan=2, sticky="w", padx=8)
        ttk.Button(master, text="Use this installation's versions", command=self._use_local).grid(
            row=4, columnspan=2, sticky="w", padx=8, pady=8)
        return self.entries["release"]

    def _use_local(self):
        for key, value in (("game_builds", compatibility.game_build(self.game_dir)),
                           ("melonloader_versions", compatibility.loader_version(self.game_dir))):
            if value:
                self.entries[key].delete(0, "end")
                self.entries[key].insert(0, value)

    def validate(self):
        release = self.entries["release"].get().strip()
        requirements = {}
        for key in compatibility.LABELS:
            value = self.entries[key].get().strip()
            if value:
                requirements[key] = [item.strip() for item in value.split(",")]
        try:
            self.result = release, compatibility.normalize(requirements)
        except core.Error as error:
            messagebox.showerror(core.NAME, str(error), parent=self)
            return False
        return True


class AddPackDialog(simpledialog.Dialog):
    def __init__(self, parent, game_dir, original=None):
        self.game_dir = game_dir
        self.original = original
        self.importing = False
        super().__init__(parent, title="Edit mod pack" if original else "Add mod pack")

    def body(self, master):
        master.columnconfigure(1, weight=1)
        self.entries = []
        for row, (label, default) in enumerate((
            ("Pack name", self.original.name if self.original else ""),
            ("Server host or IP", self.original.host if self.original else ""),
            ("Server port", str(self.original.port if self.original else packs.DEFAULT_PORT)),
            ("HTTPS manifest URL", self.original.manifest_url if self.original else ""),
        )):
            ttk.Label(master, text=label).grid(row=row, column=0, sticky="w", padx=(12, 8), pady=6)
            entry = ttk.Entry(master, width=42)
            entry.insert(0, default)
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=6)
            self.entries.append(entry)
        ttk.Label(master, text="Only add packs from publishers you trust. Mods run as game code.",
                  wraplength=380).grid(row=len(self.entries), columnspan=2, sticky="w", padx=12, pady=6)
        if self.original is None:
            actions = ttk.Frame(master)
            actions.grid(row=len(self.entries) + 1, columnspan=2, sticky="w", padx=12, pady=6)
            self.create_button = ttk.Button(actions, text="Create manifest from DLLs", command=self.create_manifest)
            self.create_button.pack(side="left")
            self.import_button = ttk.Button(actions, text="Import share link", command=self.import_link)
            self.import_button.pack(side="left", padx=(8, 0))
        return self.entries[0]

    def import_link(self):
        link = simpledialog.askstring("Import share link", "Paste the link provided by the pack owner.", parent=self)
        if not link:
            return
        try:
            pack = packs.parse_link(link)
        except core.Error as error:
            messagebox.showerror(core.NAME, str(error), parent=self)
            return
        self.importing = True
        self.import_button.configure(state="disabled", text="Reading manifest...")

        def done(mods, error):
            self.importing = False
            self.import_button.configure(state="normal", text="Import share link")
            if error:
                messagebox.showerror(core.NAME, str(error), parent=self)
                return
            version = getattr(mods, "release", "")
            details = [pack.name + (f" · {version}" if version else ""),
                       f"Server {pack.host}:{pack.port}", f"Publisher {urlsplit(pack.manifest_url).hostname}",
                       pack.manifest_url, "", *compatibility.warnings(self.game_dir, mods), "",
                       f"{len(mods)} client DLLs", *(mod.name for mod in mods), "",
                       "Only add packs from publishers you trust. Adding the pack leaves installation for later."]
            if review_text(self, "Review shared pack", details, "Add pack"):
                try:
                    self.result = packs.import_shared(self.game_dir, pack)
                except (core.Error, OSError) as error:
                    messagebox.showerror(core.NAME, str(error), parent=self)
                    return
                self.cancel()

        background(self, lambda: core.read_manifest(manifest_url=pack.manifest_url), done)

    def create_manifest(self):
        folder = filedialog.askdirectory(parent=self, title="Choose the folder containing the pack's client DLLs")
        if not folder:
            return
        path = Path(folder, "manifest.json")
        replace = path.exists()
        if replace and not messagebox.askyesno(core.NAME, f"Replace {path}?", parent=self):
            return
        details = ManifestDetailsDialog(self, self.game_dir).result
        if details is None:
            return
        release, requirements = details
        self.create_button.configure(state="disabled", text="Creating manifest...")

        def done(result, error):
            self.create_button.configure(state="normal", text="Create manifest from DLLs")
            if error:
                messagebox.showerror(core.NAME, str(error), parent=self)
            else:
                messagebox.showinfo(core.NAME, "Saved %s\n\nUpload manifest.json and its DLLs to the same HTTPS folder, "
                                    "then paste the manifest URL here." % result, parent=self)

        background(self, lambda: packs.create_manifest(folder, replace=replace,
                   release=release, requirements=requirements), done)

    def validate(self):
        if self.importing:
            return False
        try:
            values = tuple(entry.get() for entry in self.entries)
            if self.original is None:
                self.result = packs.add(self.game_dir, *values)
            else:
                self.result = packs.edit(self.game_dir, self.original, *values)
        except (core.Error, OSError) as error:
            messagebox.showerror(core.NAME, str(error), parent=self)
            return False
        return True


class SheikahWindow(tk.Toplevel):
    def __init__(self, root, game_dir, check, apply, *, on_select=None, on_switch=None,
                 on_automatic=None, on_add=None, on_preview=None, options=None):
        self.locked = packs.locked()
        available = packs.catalog(game_dir)
        active, _ = switching.selection(game_dir)
        super().__init__(root)
        _chrome._start_hidden(self)
        self.game_dir = game_dir
        self._rendered_plan = None
        self.configure(bg=_theme.BG)
        self.title(core.NAME)
        self.subtitle = _header(self, 'Choose a pack to review its mods.')
        self.available = available
        self.pack_menu = None
        if not self.locked and on_select is not None:
            controls = tk.Frame(self, bg=_theme.BG)
            controls.pack(fill='x', padx=24, pady=(0, 8))
            self.pack_menu = ttk.Combobox(controls, state='readonly', width=24)
            self.pack_menu.pack(side='left', fill='x', expand=True)
            self.pack_menu.bind('<<ComboboxSelected>>', lambda event: on_select())
            self.switch_button = _theme._btn(controls, 'Switch now', on_switch, padx=10, pady=5)
            self.switch_button.pack(side='left', padx=(8, 0))
            self.add_button = _theme._btn(controls, 'Add pack', on_add, padx=10, pady=5)
            self.add_button.pack(side='left', padx=(8, 0))
            self.options_button = None
            if options:
                theme = _theme
                self.options_button = tk.Menubutton(controls, text='Pack options ▾', bg=theme.SURF,
                                                    fg=theme.PARCH, padx=10, pady=5, relief='flat')
                self.options_menu = tk.Menu(self.options_button, tearoff=False)
                for label, action in options:
                    self.options_menu.add_command(label=label, command=action)
                self.options_button.configure(menu=self.options_menu)
                self.options_button.pack(side='left', padx=(8, 0))
            mode = tk.Frame(self, bg=_theme.BG)
            mode.pack(fill='x', padx=24, pady=(0, 10))
            self.mode_label = tk.Label(mode, bg=_theme.BG, fg=_theme.MUTED, anchor='w',
                                       font=('Segoe UI', 9))
            self.mode_label.pack(side='left', fill='x', expand=True)
            self.auto_button = _theme._btn(mode, 'Follow server', on_automatic, padx=10, pady=5)
            self.auto_button.pack(side='right')
            if on_preview:
                self.preview_button = _theme._btn(mode, 'Preview launch', on_preview, padx=10, pady=5)
                self.preview_button.pack(side='right', padx=(8, 8))
                self.next_label = tk.Label(self, bg=_theme.BG, fg=_theme.MUTED, anchor='w',
                                           font=('Segoe UI', 9))
                self.next_label.pack(fill='x', padx=24, pady=(0, 8))
        self._select(active)
        footer = tk.Frame(self, bg=_theme.BG)
        footer.pack(side='bottom', fill='x', padx=24, pady=(10, 18))
        self.status = tk.Label(footer, text=core.HOST, bg=_theme.BG, fg=_theme.MUTED,
                               anchor='w', justify='left', wraplength=620, font=('Segoe UI', 9))
        self.status.pack(fill='x', pady=(0, 10))
        if self.locked:
            tk.Label(footer, text='Sheikah mods follow att.aservertown.fans:1757.',
                     bg=_theme.BG, fg=_theme.MUTED, anchor='w',
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

    def _select(self, id):
        pack = next((item for item in self.available if item.id == id), packs.SHEIKAH)
        if self.pack_menu is not None:
            self.pack_menu.configure(values=[item.name for item in self.available])
            self.pack_menu.set(pack.name)

    def refresh_packs(self, id):
        self.available = packs.catalog(self.game_dir)
        self._select(id)

    def selected_pack(self):
        if self.pack_menu is not None:
            index = self.pack_menu.current()
            if index > 0:
                return self.available[index]
        return None

    def show_next(self, text):
        if hasattr(self, 'next_label'):
            self.next_label.configure(text=text)

    def show(self, plan=None, status=None, busy=False, issue=None):
        pack = self.selected_pack() or packs.SHEIKAH
        active, manual = switching.selection(self.game_dir)
        if self.pack_menu is not None:
            active_name = next((item.name for item in self.available if item.id == active), 'Regular mods')
            mode = 'Manual' if manual else 'Automatic'
            self.mode_label.configure(text=f'{mode} · Active files: {active_name}')
            self.pack_menu.configure(state='disabled' if busy else 'readonly')
            self.add_button.configure(state='disabled' if busy else 'normal')
            self.switch_button.configure(state='disabled' if busy or (manual and active == pack.id) else 'normal')
            self.auto_button.configure(state='normal' if manual and not busy else 'disabled')
            if hasattr(self, 'preview_button'):
                self.preview_button.configure(state='disabled' if busy else 'normal')
            if self.options_button is not None:
                self.options_button.configure(state='disabled' if busy else 'normal')
                editable = 'normal' if pack != packs.SHEIKAH else 'disabled'
                self.options_menu.entryconfigure('Edit pack', state=editable)
                self.options_menu.entryconfigure('Remove pack', state=editable)
                try:
                    available, pinned = history.available(self.game_dir, self.selected_pack())
                except core.Error:
                    available, pinned = False, False
                self.options_menu.entryconfigure('Restore previous version', state='normal' if available else 'disabled')
                self.options_menu.entryconfigure('Resume updates', state='normal' if pinned else 'disabled')
        server = f'{pack.host}:{pack.port}'
        if plan is not None:
            location = 'Active in Mods' if active == pack.id else 'Inactive'
            tags = [server, location]
            if release := getattr(plan.mods, 'release', ''):
                tags.append(release)
            if plan.pinned:
                tags.append('Restored version')
            if plan.warnings:
                tags.append('Compatibility notice')
            self.subtitle.configure(text=' · '.join(tags) + f'\n{len(plan.ready)} of {len(plan.mods)} listed mods match.')
            if plan is not self._rendered_plan:
                self.list.fill(plan, include_ready=True)
                self._rendered_plan = plan
        else:
            self.list.clear()
            self._rendered_plan = None
            self.subtitle.configure(text=f'{server} · {urlsplit(pack.manifest_url).hostname}')
        self.check_button.configure(state='disabled' if busy else 'normal')
        label, enabled = ui_state.action(plan, busy, issue)
        self.apply_button.configure(text=label, state='normal' if enabled else 'disabled')
        if status is not None:
            self.status.configure(text=status, fg=_theme.AMBER if issue is not None or (plan is not None and plan.offline) else _theme.MUTED)
