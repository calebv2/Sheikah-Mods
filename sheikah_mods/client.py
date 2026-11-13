"""TavernLauncher integration for the standalone Sheikah Mods addon."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox

import tavern_shared.theme as _theme

from . import core, runtime, switching, ui_state, window

BUTTON_TEXT = "Sheikah Mods"
AFTER_TEXTS = ("\U0001f4c1 Mods Folder", "☑ Toggle Mods")


def _app():
    return getattr(tk, "_default_root", None)


def _log(text, tag="info"):
    root = _app()
    if root is not None:
        try:
            root._print(core.NAME + ": " + text, tag)
        except (AttributeError, tk.TclError):
            pass


def _show(root, plan=None, status=None):
    button = getattr(root, "_sheikah_button", None)
    if button is not None:
        button.configure(text=BUTTON_TEXT)
    opened = getattr(root, "_sheikah_window", None)
    if opened is not None and opened.winfo_exists():
        if plan is not None and plan.game_dir != opened.game_dir:
            plan = None
        opened.show(plan, status, getattr(root, "_sheikah_pending", False), getattr(root, "_sheikah_issue", None))
    _render_next_status(root)


def _set_progress(root, text):
    root._sheikah_progress = text
    waiting = getattr(root, "_sheikah_wait_dialog", None)
    if waiting is not None and waiting.winfo_exists():
        waiting.message.configure(text=text)
    _show(root, getattr(root, "_sheikah_plan", None), text)


def _render_next_status(root):
    label = getattr(root, "_sheikah_next_status", None)
    if label is None:
        return
    progress = getattr(root, "_sheikah_progress", None) if getattr(root, "_sheikah_pending", False) else None
    text, color = ui_state.next_launch(getattr(root, "_sheikah_plan", None),
        getattr(root, "_sheikah_target_match", None), progress,
        getattr(root, "_sheikah_issue", None), getattr(root, "_sheikah_route_error", False))
    label.configure(text="Sheikah Mods · " + text, fg=getattr(_theme, color))


def _game_changed(root, *unused):
    plan = getattr(root, '_sheikah_plan', None)
    game_dir = _game_dir(root)
    if plan is not None and plan.game_dir == game_dir:
        return
    root._sheikah_plan = None
    root._sheikah_issue = None
    _show(root, status='Check mods for the selected game.')
    if game_dir is not None:
        if getattr(root, '_sheikah_pending', False):
            root._sheikah_recheck_dir = game_dir
        else:
            check(root, game_dir, prompt=False)


def _destination_changed(root, *unused):
    generation = getattr(root, "_sheikah_route_generation", 0) + 1
    root._sheikah_route_generation = generation
    pending = getattr(root, "_sheikah_route_timer", None)
    if pending is not None:
        root.after_cancel(pending)
    root._sheikah_route_timer = None
    root._sheikah_route_error = False
    try:
        host = root.v_ip.get().strip().rstrip('.').casefold() or '127.0.0.1'
        port = int(str(root.v_port.get()).strip() or switching.SERVER_PORT)
        if not 1 <= port <= 65535:
            raise ValueError()
    except AttributeError:
        root._sheikah_target_match = None
        _render_next_status(root)
        return
    except ValueError:
        root._sheikah_route_error = True
        _render_next_status(root)
        return
    if host == core.HOST or port != switching.SERVER_PORT or host in ('127.0.0.1', 'localhost', '::1'):
        root._sheikah_target_match = host == core.HOST and port == switching.SERVER_PORT
        _render_next_status(root)
        return
    root._sheikah_target_match = None
    _render_next_status(root)

    def done(match, error):
        if generation != root._sheikah_route_generation:
            return
        root._sheikah_target_match = match
        root._sheikah_route_error = error is not None
        _render_next_status(root)

    def resolve():
        root._sheikah_route_timer = None
        window.background(root, lambda: switching.is_sheikah((host, port)), done)

    root._sheikah_route_timer = root.after(250, resolve)


def _ensure_status(root):
    existing = getattr(root, "_sheikah_next_status", None)
    if existing is not None and existing.winfo_exists():
        return
    action = getattr(root, "_action_btn", None)
    if action is None or action.master.winfo_manager() != 'pack':
        return
    row = action.master
    strip = tk.Frame(row.master, bg=_theme.BG)
    strip.pack(fill='x', before=row, padx=row.pack_info().get('padx', 0), pady=(4, 6))
    label = tk.Label(strip, bg=_theme.BG, fg=_theme.MUTED, anchor='w', justify='left',
                     font=('Segoe UI', 9), wraplength=600)
    label.pack(fill='x')
    strip.bind('<Configure>', lambda event: label.configure(wraplength=max(180, event.width)))
    root._sheikah_next_status = label
    for name in ('v_ip', 'v_port'):
        variable = getattr(root, name, None)
        if variable is not None:
            variable.trace_add('write', lambda *args: _destination_changed(root))
    variable = getattr(root, 'v_exe', None)
    if variable is not None:
        variable.trace_add('write', lambda *args: _game_changed(root))

    def cleanup(event):
        if event.widget is root:
            timer = getattr(root, '_sheikah_route_timer', None)
            if timer is not None:
                root.after_cancel(timer)

    root.bind('<Destroy>', cleanup, add='+')
    _destination_changed(root)


def _game_dir(root):
    try:
        exe = root.v_exe.get().strip()
    except Exception:
        return None
    return str(Path(exe).resolve().parent) if exe and Path(exe).is_file() else None


def _prompt_parent(root):
    waiting = getattr(root, "_sheikah_wait_dialog", None)
    if waiting is not None:
        waiting.deiconify()
        return waiting
    return root


def _checked(root, result, error, prompt, force):
    updating = False
    try:
        if error:
            root._sheikah_issue = error
            root._sheikah_retry = 'check'
            _log(str(error), "warn")
            _show(root, getattr(root, "_sheikah_plan", None), ui_state.friendly_error(error, getattr(root, "_sheikah_plan", None)))
            return
        if _game_dir(root) != result.game_dir:
            _show(root, status="The game path changed. Choose Check now for the selected game.")
            return
        root._sheikah_plan = result
        root._sheikah_issue = core.Offline('offline') if result.offline else None
        root._sheikah_retry = 'check'
        _show(root, result, ui_state.offline_message(result) if result.offline else "%d mods ready; %d changes available." % (len(result.ready), len(result.changes)))
        if result.offline:
            _log(ui_state.offline_message(result), 'warn')
        elif result.changes and prompt and (force or not core.deferred(result)):
            if window.ask_updates(_prompt_parent(root), result):
                _install(root, result)
                updating = True
            else:
                core.defer(result)
                _show(root, result, "Updates skipped for this session. Check now lets you reconsider.")
        elif not result.changes:
            _log("All %d listed mods are up to date." % len(result.mods), "ok")
    finally:
        if not updating:
            root._sheikah_pending = False
            _show(root, getattr(root, "_sheikah_plan", None))
            recheck = getattr(root, '_sheikah_recheck_dir', None)
            root._sheikah_recheck_dir = None
            if recheck is not None and recheck == _game_dir(root):
                check(root, recheck, prompt=False)


def check(root, game_dir, force=False, prompt=True):
    if getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    root._sheikah_pending = True
    root._sheikah_issue = None
    _set_progress(root, "Checking the Sheikah Sanctuary mod list...")
    window.background(root, lambda: core.inspect(game_dir, force=force),
                      lambda result, error: _checked(root, result, error, prompt, force))


def _installed(root, result, error):
    root._sheikah_pending = False
    recheck = getattr(root, '_sheikah_recheck_dir', None)
    root._sheikah_recheck_dir = None
    if recheck is not None and recheck == _game_dir(root):
        root._sheikah_plan = None
        check(root, recheck, prompt=False)
        return
    if error:
        root._sheikah_issue = error
        root._sheikah_retry = 'check' if any(word in str(error) for word in ('changed', 'invalid', 'Recovery copies', 'SHA-256')) else 'install'
        plan = getattr(root, "_sheikah_plan", None)
        _show(root, plan, ui_state.friendly_error(error, plan))
        _log(str(error), "err")
        return
    root._sheikah_plan = result
    root._sheikah_issue = None
    _show(root, result, "All %d Sheikah mods are ready. Changes apply when the game starts." % len(result.mods))
    _log("All %d listed mods are ready." % len(result.mods), "ok")


def _install(root, plan):
    root._sheikah_pending = True
    root._sheikah_issue = None
    _set_progress(root, "Preparing Sheikah mods...")
    window.background(root, lambda emit: core.install(plan, progress=emit),
                      lambda result, error: _installed(root, result, error),
                      progress=lambda text: _set_progress(root, text))


def _apply_click(root):
    plan = getattr(root, "_sheikah_plan", None)
    if getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    game_dir = root._sheikah_window.game_dir
    if plan is not None and plan.game_dir != game_dir:
        return
    if getattr(root, "_sheikah_issue", None) is not None or plan is None or plan.offline:
        if plan is not None and getattr(root, "_sheikah_retry", 'check') == 'install':
            _install(root, plan)
        else:
            check(root, game_dir, force=True, prompt=False)
        return
    if window.ask_updates(root._sheikah_window, plan):
        _install(root, plan)
    else:
        core.defer(plan)


def _open(root):
    game_dir = _game_dir(root)
    if game_dir is None:
        messagebox.showerror(core.NAME, "Set the game executable in TavernLauncher first.", parent=root)
        return
    opened = getattr(root, "_sheikah_window", None)
    if opened is not None and opened.winfo_exists():
        if opened.game_dir == game_dir:
            opened.lift()
            return
        opened.destroy()
    root._sheikah_window = window.SheikahWindow(root, game_dir,
        lambda: check(root, game_dir, force=True), lambda: _apply_click(root))
    plan = getattr(root, "_sheikah_plan", None)
    if plan is not None and plan.game_dir != game_dir:
        plan = None
    _show(root, plan, "Checking the Sheikah Sanctuary mod list..."
          if getattr(root, "_sheikah_pending", False) else None)
    check(root, game_dir, prompt=False)


def _is_game(args):
    if isinstance(args, (str, bytes)) or not args:
        return False
    try:
        path = os.fspath(args[0])
        if not isinstance(path, str):
            return False
        if os.path.basename(path).lower() == core.GAME_EXE.lower():
            return True
        exe = _app().v_exe.get().strip()
        return bool(exe) and runtime.same_path(path, exe)
    except (AttributeError, TypeError):
        return False


def _switch_profile(root, game_dir, sheikah, main, mods=None):
    previous = getattr(root, '_sheikah_plan', None)

    def work(emit=None):
        moved = switching.switch(game_dir, sheikah, mods, progress=emit)
        known = tuple(mods) if mods is not None else switching.cached_manifest(game_dir)
        plan = core.inspect(game_dir, mods=known) if known else None
        if plan is not None and previous is not None and previous.offline and previous.mods == plan.mods:
            plan.offline = True
        return moved, plan

    if main:
        root._sheikah_pending = True
        _set_progress(root, "Switching mods...")
        moved, plan = window.run_waiting(root, work, "Switching mods...", progress=lambda text: _set_progress(root, text))
    else:
        moved, plan = work()
    message = ("Sheikah mods loaded for %s:%d (%d moved)." % (core.HOST, switching.SERVER_PORT, len(moved))
               if sheikah else "Sheikah mods parked for this launch (%d moved)." % len(moved))
    _log(message, "info")
    if main:
        root._sheikah_plan = plan
        _show(root, plan, ui_state.offline_message(plan) if plan is not None and plan.offline else message)


def before_launch(args):
    if not _is_game(args):
        return
    root = _app()
    game_dir = str(Path(os.fspath(args[0])).resolve().parent)
    main = threading.current_thread() is threading.main_thread() and root is not None
    if getattr(root, "_sheikah_launching", False):
        raise core.Error("A launch is already waiting for Sheikah Mods.")
    if root is not None:
        root._sheikah_launching = True
    try:
        if runtime.game_running(game_dir):
            raise core.Error("Close A Township Tale before launching again so its server mods can be switched safely.")
        if main:
            window.wait_for(root, lambda: not getattr(root, "_sheikah_pending", False),
                            "Finishing Sheikah Mods before launching...")
        elif getattr(root, "_sheikah_pending", False):
            raise core.Error("Sheikah Mods is still working. Launch again when it finishes.")
        target = switching.target_from_args(args)
        if target is not None and target[0] != core.HOST and target[1] == switching.SERVER_PORT and main:
            sheikah = window.run_waiting(root, lambda: switching.is_sheikah(target), "Identifying the server's mods...")
        else:
            sheikah = switching.is_sheikah(target)
        if not sheikah:
            _switch_profile(root, game_dir, False, main)
            return
        plan = None
        try:
            plan = window.run_waiting(root, lambda: core.inspect(game_dir), "Checking Sheikah Mods...") if main else core.inspect(game_dir)
        except core.Cancelled:
            raise
        except core.Error as error:
            if isinstance(error, core.Offline):
                raise
            _log("Couldn't check before launching; using installed mods. " + str(error), "warn")
        if main and plan is not None:
            root._sheikah_plan = plan
            root._sheikah_issue = core.Offline('offline') if plan.offline else None
            _show(root, plan, ui_state.offline_message(plan) if plan.offline else None)
        if plan is not None and plan.offline and not ui_state.usable(plan):
            raise core.Offline("Required mods do not match the saved list.")
        if plan is not None and not plan.offline and plan.changes and not core.deferred(plan):
            if not main:
                raise core.Error("Open Sheikah Mods in the launcher to review the available changes first.")
            if window.ask_updates(root, plan, launching=True):
                plan = window.run_waiting(root, lambda emit: core.install(plan, progress=emit),
                                          "Preparing Sheikah mods...", progress=lambda text: _set_progress(root, text))
            else:
                core.defer(plan)
                _log("Updates skipped; launching with installed mods.", "warn")
        _switch_profile(root, game_dir, True, main, plan.mods if plan is not None else None)
    except Exception as error:
        message = str(error) if isinstance(error, core.Cancelled) else ui_state.friendly_error(error, getattr(root, '_sheikah_plan', None))
        if main:
            if not isinstance(error, core.Cancelled):
                root._sheikah_issue = error
                root._sheikah_retry = 'check'
                _show(root, getattr(root, '_sheikah_plan', None), message)
            messagebox.showerror(core.NAME, message, parent=root)
        _log(str(error), 'err')
        if isinstance(error, core.Error):
            raise
        raise core.Error(message) from error
    finally:
        if root is not None:
            root._sheikah_launching = False
            _render_next_status(root)


class _Popen(subprocess.Popen):
    _sheikah_mods = True

    def __init__(self, args, *rest, **kwargs):
        before_launch(args)
        super().__init__(args, *rest, **kwargs)


if not getattr(subprocess.Popen, "_sheikah_mods", False):
    subprocess.Popen = _Popen


def _startup(root):
    game_dir = _game_dir(root)
    if game_dir is not None:
        check(root, game_dir)


def _add_button(anchor):
    root = anchor.winfo_toplevel()
    if not hasattr(root, "v_exe"):
        return
    for child in anchor.master.winfo_children():
        if child is getattr(root, "_sheikah_button", None):
            return
    after = anchor
    for child in anchor.master.pack_slaves():
        try:
            if child.cget("text") in AFTER_TEXTS:
                after = child
        except tk.TclError:
            pass
    button = _theme._btn(anchor.master, BUTTON_TEXT, lambda: _open(root), font=("Segoe UI", 9), pady=5, padx=10)
    button.pack(side="left", padx=(6, 0), after=after)
    root._sheikah_button = button
    if not getattr(root, "_sheikah_started", False):
        root._sheikah_started = True
        root.after_idle(lambda: _startup(root))


def _is_mods_button(text):
    # Launcher versions differ in their icon and spacing; the label is stable.
    return "".join(character for character in str(text) if character.isalpha()).casefold() == "mods"


def _ensure_button(root):
    if not hasattr(root, "v_exe"):
        return
    _ensure_status(root)
    existing = getattr(root, "_sheikah_button", None)
    if existing is not None and existing.winfo_exists():
        return
    pending = list(root.winfo_children())
    while pending:
        child = pending.pop(0)
        if isinstance(child, (tk.Button, tk.Menubutton)):
            if _is_mods_button(child.cget("text")):
                _add_button(child)
                return
        pending.extend(child.winfo_children())
    # Keep a visible entry point even if the launcher reorganizes its Mods row.
    action = getattr(root, "_action_btn", None)
    if action is not None:
        _add_button(action)


def _wrap(original):
    def button(parent, text, cmd, style="normal", **kwargs):
        result = original(parent, text, cmd, style, **kwargs)
        if _is_mods_button(text):
            result.after_idle(lambda: result.after_idle(lambda: _add_button(result)))
        return result
    return button


def _install_ui():
    launcher_module = sys.modules.get("client.core.launcher_window")
    launcher_class = getattr(launcher_module, "ClientLauncher", None)
    build = getattr(launcher_class, "_build_ui", None)
    if callable(build):
        if not getattr(build, "_sheikah_button_hook", False):
            def build_with_sheikah(self, *args, **kwargs):
                result = build(self, *args, **kwargs)
                self.after_idle(lambda: _ensure_button(self))
                return result
            build_with_sheikah._sheikah_button_hook = True
            launcher_class._build_ui = build_with_sheikah
    else:
        # Support launchers that create the Mods row after loading addons.
        _theme._btn = _wrap(_theme._btn)
        if launcher_module is not None and callable(getattr(launcher_module, "_btn", None)):
            launcher_module._btn = _wrap(launcher_module._btn)
    root = _app()
    if root is not None:
        root.after_idle(lambda: _ensure_button(root))


_install_ui()
