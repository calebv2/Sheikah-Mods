"""TavernLauncher integration for the standalone Sheikah Mods addon."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox

import tavern_shared.theme as _theme

from . import core, history, packs, runtime, switching, ui_state, window

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
        if plan is not None and (plan.game_dir != opened.game_dir or plan.pack != _selected_pack(root)):
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
    opened = getattr(root, "_sheikah_window", None)
    if opened is not None and opened.winfo_exists():
        route = getattr(root, "_sheikah_next_pack_name", None)
        if getattr(root, "_sheikah_route_error", False):
            opened.show_next("Next Join Server · Check the server address")
        else:
            opened.show_next(f"Next Join Server · {route}" if route else "Next Join Server · Checking address")
    label = getattr(root, "_sheikah_next_status", None)
    if label is None:
        return
    progress = getattr(root, "_sheikah_progress", None) if getattr(root, "_sheikah_pending", False) else None
    plan = getattr(root, "_sheikah_plan", None)
    issue = getattr(root, "_sheikah_issue", None)
    if _selected_pack(root) is not None or (plan is not None and plan.pack is not None):
        plan, issue = None, None
    text, color = ui_state.next_launch(plan,
        getattr(root, "_sheikah_target_match", None), progress,
        issue, getattr(root, "_sheikah_route_error", False))
    target = getattr(root, "_sheikah_target_pack", None)
    if not progress and target is not None:
        mode = 'Manual pack' if getattr(root, '_sheikah_manual', False) else 'Pack on launch'
        text, color = f"{mode} · {target.name}", 'MUTED'
    label.configure(text="Sheikah Mods · " + text, fg=getattr(_theme, color))


def _game_changed(root, *unused):
    _destination_changed(root)
    plan = getattr(root, '_sheikah_plan', None)
    game_dir = _game_dir(root)
    if plan is not None and plan.game_dir == game_dir:
        return
    opened = getattr(root, "_sheikah_window", None)
    if opened is not None and opened.winfo_exists() and opened.game_dir != game_dir:
        opened.destroy()
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
    root._sheikah_target_pack = None
    root._sheikah_next_pack_name = None
    root._sheikah_manual = False
    try:
        host, port = _launcher_target(root)
    except AttributeError:
        root._sheikah_target_match = None
        _render_next_status(root)
        return
    except ValueError:
        root._sheikah_route_error = True
        _render_next_status(root)
        return
    game_dir = _game_dir(root)
    try:
        available = packs.catalog(game_dir) if game_dir is not None else (packs.SHEIKAH,)
        active, manual = switching.selection(game_dir) if game_dir is not None else (packs.SHEIKAH_ID, False)
        root._sheikah_manual = manual and not packs.locked()
    except core.Error:
        root._sheikah_route_error = True
        _render_next_status(root)
        return
    if root._sheikah_manual:
        exact = next((item for item in available if item.id == active), None)
    else:
        exact = next((item for item in available if item.target == (host, port)), None)
    if root._sheikah_manual or exact or not switching.needs_lookup((host, port), available):
        root._sheikah_target_match = exact == packs.SHEIKAH
        root._sheikah_next_pack_name = exact.name if exact else "Regular mods"
        if exact is not None and (exact != packs.SHEIKAH or root._sheikah_manual):
            root._sheikah_target_pack = exact
        _render_next_status(root)
        return
    root._sheikah_target_match = None
    _render_next_status(root)

    def done(pack_id, error):
        if generation != root._sheikah_route_generation:
            return
        item = next((item for item in available if item.id == pack_id), None)
        root._sheikah_target_match = item == packs.SHEIKAH
        root._sheikah_target_pack = item if item != packs.SHEIKAH else None
        root._sheikah_next_pack_name = item.name if item else "Regular mods"
        root._sheikah_route_error = error is not None
        _render_next_status(root)

    def resolve():
        root._sheikah_route_timer = None
        window.background(root, lambda: switching.route(game_dir, (host, port)) if game_dir is not None
                          else packs.SHEIKAH_ID if switching.is_sheikah((host, port)) else packs.REGULAR_ID, done)

    root._sheikah_route_timer = root.after(250, resolve)


def _launcher_target(root):
    host = packs.normalize_host(root.v_ip.get()) or '127.0.0.1'
    port = int(str(root.v_port.get()).strip() or switching.SERVER_PORT)
    if not 1 <= port <= 65535:
        raise ValueError("The launcher has an invalid server port.")
    return host, port


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


def _selected_pack(root):
    opened = getattr(root, "_sheikah_window", None)
    return opened.selected_pack() if opened is not None and opened.winfo_exists() else None


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
        if _game_dir(root) != result.game_dir or result.pack != _selected_pack(root):
            _show(root, status="The game path changed. Choose Check now for the selected game.")
            return
        root._sheikah_plan = result
        root._sheikah_issue = core.Offline('offline') if result.offline else None
        root._sheikah_retry = 'check'
        _show(root, result, ui_state.offline_message(result) if result.offline else "%d mods ready; %d changes available." % (len(result.ready), len(result.changes)))
        if result.offline:
            _log(ui_state.offline_message(result), 'warn')
        elif result.pinned:
            _show(root, result, "Restored version is pinned. Use Pack options → Resume updates to switch back to the publisher's releases.")
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
    pack = _selected_pack(root)
    _set_progress(root, f"Checking the {(pack or packs.SHEIKAH).name} mod list...")
    window.background(root, lambda: core.inspect(game_dir, force=force, pack=pack),
                      lambda result, error: _checked(root, result, error, prompt, force))


def _installed(root, result, error, restoring=False):
    root._sheikah_pending = False
    recheck = getattr(root, '_sheikah_recheck_dir', None)
    root._sheikah_recheck_dir = None
    if recheck is not None and recheck == _game_dir(root):
        root._sheikah_plan = None
        check(root, recheck, prompt=False)
        return
    if error:
        if restoring:
            _show(root, getattr(root, "_sheikah_plan", None), "Could not restore the saved version.")
            opened = getattr(root, "_sheikah_window", None)
            parent = opened if opened is not None and opened.winfo_exists() else root
            messagebox.showerror(core.NAME, str(error), parent=parent)
            return
        root._sheikah_issue = error
        root._sheikah_retry = 'check' if any(word in str(error) for word in ('changed', 'invalid', 'Recovery copies', 'SHA-256')) else 'install'
        plan = getattr(root, "_sheikah_plan", None)
        _show(root, plan, ui_state.friendly_error(error, plan))
        _log(str(error), "err")
        return
    root._sheikah_plan = result
    root._sheikah_issue = None
    status = ("Restored the saved version and paused updates for this pack." if result.pinned
              else "All %d pack mods are ready for the next game launch." % len(result.mods))
    _show(root, result, status)
    _log("All %d listed mods are ready." % len(result.mods), "ok")


def _install(root, plan, restoring=False):
    root._sheikah_pending = True
    root._sheikah_issue = None
    _set_progress(root, "Preparing saved version..." if restoring else "Preparing pack mods...")
    window.background(root, lambda emit: core.install(plan, progress=emit, restoring=restoring),
                      lambda result, error: _installed(root, result, error, restoring),
                      progress=lambda text: _set_progress(root, text))


def _apply_click(root):
    plan = getattr(root, "_sheikah_plan", None)
    if getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    game_dir = root._sheikah_window.game_dir
    if plan is not None and (plan.game_dir != game_dir or plan.pack != _selected_pack(root)):
        plan = None
    if plan is not None and plan.pinned:
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
    try:
        root._sheikah_window = window.SheikahWindow(root, game_dir,
            lambda: check(root, game_dir, force=True), lambda: _apply_click(root),
            on_select=lambda: _pack_selected(root, game_dir),
            on_switch=lambda: _manual_switch(root, game_dir),
            on_automatic=lambda: _resume_automatic(root, game_dir),
            on_add=lambda: _add_pack(root, game_dir),
            on_preview=lambda: _preview_launch(root, game_dir),
            options=(
                ('Copy share link', lambda: _copy_link(root)),
                ('Edit pack', lambda: _add_pack(root, game_dir, _selected_pack(root))),
                ('Remove pack', lambda: _remove_pack(root, game_dir)),
                ('Restore previous version', lambda: _restore_version(root, game_dir)),
                ('Resume updates', lambda: _resume_updates(root, game_dir)),
            ))
    except core.Error as error:
        messagebox.showerror(core.NAME, str(error), parent=root)
        return
    plan = getattr(root, "_sheikah_plan", None)
    if plan is not None and plan.game_dir != game_dir:
        plan = None
    _show(root, plan, "Checking the mod list..."
          if getattr(root, "_sheikah_pending", False) else None)
    check(root, game_dir, prompt=False)


def _pack_selected(root, game_dir):
    root._sheikah_plan = None
    root._sheikah_issue = None
    _show(root, status="Checking the selected pack...")
    check(root, game_dir, prompt=False)


def _add_pack(root, game_dir, original=None):
    if getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    dialog = window.AddPackDialog(root._sheikah_window, game_dir, original)
    if dialog.result is not None:
        root._sheikah_window.refresh_packs(dialog.result.id)
        _destination_changed(root)
        _pack_selected(root, game_dir)


def _copy_link(root):
    opened = root._sheikah_window
    link = packs.share_link(_selected_pack(root) or packs.SHEIKAH)
    opened.clipboard_clear()
    opened.clipboard_append(link)
    _show(root, getattr(root, "_sheikah_plan", None), "Share link copied. Players can paste it in Add pack → Import share link.")


def _remove_pack(root, game_dir):
    pack = _selected_pack(root)
    if pack is None or getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    opened = root._sheikah_window
    try:
        active, _ = switching.selection(game_dir)
        files = core._read_state(core._state_bytes(game_dir, pack))
        details = [f"{pack.name} will be removed from the pack list and automatic matching.",
                   "Downloaded files and version history stay available in the pack's holding folder.",
                   "Add the same server address and manifest URL later to reuse them."]
        if active == pack.id:
            originals = sorted(name for name, entry in files.items() if entry.get("original"))
            details += ["", "The active pack will be parked and regular mods will become active.",
                        f"{len(originals)} original DLLs will be restored.", *originals]
        if not window.review_text(opened, "Remove pack", details, "Remove pack"):
            return
    except (core.Error, OSError) as error:
        messagebox.showerror(core.NAME, str(error), parent=opened)
        return
    root._sheikah_pending = True
    _set_progress(root, f"Removing {pack.name}...")

    def done(result, error):
        root._sheikah_pending = False
        viewer = getattr(root, "_sheikah_window", None)
        if viewer is not opened or not opened.winfo_exists():
            _destination_changed(root)
            return
        if error:
            messagebox.showerror(core.NAME, str(error), parent=opened)
            _show(root, getattr(root, "_sheikah_plan", None), "The pack could not be removed.")
        else:
            opened.refresh_packs(packs.SHEIKAH_ID)
            _pack_selected(root, game_dir)
        _destination_changed(root)

    window.background(root, lambda emit: packs.remove(game_dir, pack, progress=emit), done,
                      progress=lambda text: _set_progress(root, text))


def _launch_details(game_dir, target):
    available = {item.id: item for item in packs.catalog(game_dir)}
    active, manual = switching.selection(game_dir)
    if packs.locked():
        manual = False
    details = [f"Active files: {available[active].name if active in available else 'Regular mods'}",
               "Mode: Manual selection" if manual else "Mode: Follow server"]
    projected = {}
    for label, address in (("Join Server", target), ("Main Menu", None)):
        details.append("")
        if label == "Join Server" and address is None:
            details.append("Join Server · Set a valid server address in TavernLauncher.")
            continue
        try:
            pack_id = switching.route(game_dir, address)
            item = available.get(pack_id)
            details.append(f"{label} · {item.name if item else 'Regular mods'}")
            if pack_id in projected:
                details.append(f"Same files as {projected[pack_id]}.")
                continue
            projected[pack_id] = label
            files = switching.launch_files(game_dir, pack_id, None if item == packs.SHEIKAH else item)
        except (core.Error, OSError) as error:
            details.append(str(error))
            continue
        for title, values in (("Pack DLLs ready", files.pack), ("Regular DLLs also loaded", files.regular),
                              ("Regular DLLs temporarily replaced", files.conflicts),
                              ("Missing or out of date in the saved manifest", files.pending),
                              ("Needs review", files.warnings)):
            if values or title == "Pack DLLs ready":
                details.append(f"{title} ({len(values)})")
                details.extend(f"  {value}" for value in values)
    return details


def _preview_launch(root, game_dir):
    if getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    try:
        target = _launcher_target(root)
    except (AttributeError, ValueError):
        target = None
    opened = root._sheikah_window
    try:
        details = window.run_waiting(root, lambda: _launch_details(game_dir, target), "Preparing launch preview...")
        if opened.winfo_exists():
            window.review_text(opened, "Launch preview", details)
    except core.Cancelled:
        pass
    except (core.Error, OSError) as error:
        messagebox.showerror(core.NAME, str(error), parent=opened)


def _restore_version(root, game_dir):
    if getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    pack = _selected_pack(root)
    opened = root._sheikah_window
    try:
        if runtime.game_running(game_dir):
            raise core.Error("Close A Township Tale before restoring a saved version.")
        plan = window.run_waiting(root, lambda: core.restore_plan(game_dir, pack), "Reading saved version...")
        if window.ask_updates(opened, plan, restoring=True):
            _install(root, plan, restoring=True)
    except core.Cancelled:
        pass
    except (core.Error, OSError) as error:
        messagebox.showerror(core.NAME, str(error), parent=opened)


def _resume_updates(root, game_dir):
    if getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    try:
        history.resume(game_dir, _selected_pack(root))
        check(root, game_dir, force=True, prompt=False)
    except (core.Error, OSError) as error:
        messagebox.showerror(core.NAME, str(error), parent=root._sheikah_window)


def _manual_switch(root, game_dir):
    if getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    pack = _selected_pack(root)
    root._sheikah_launching = True
    try:
        if runtime.game_running(game_dir):
            raise core.Error("Close A Township Tale before switching server mods.")
        plan = _prepare_pack(root, game_dir, True, pack, manual=True)
        _switch_profile(root, game_dir, pack is None, True, plan.mods, pack=pack, manual=True)
    except core.Cancelled:
        pass
    except (OSError, core.Error) as error:
        messagebox.showerror(core.NAME, str(error), parent=root._sheikah_window)
    finally:
        root._sheikah_launching = False
        _show(root, getattr(root, "_sheikah_plan", None))
        _destination_changed(root)


def _resume_automatic(root, game_dir):
    if getattr(root, "_sheikah_pending", False) or getattr(root, "_sheikah_launching", False):
        return
    try:
        switching.resume_automatic(game_dir)
        _show(root, getattr(root, "_sheikah_plan", None), "The next launch will use the pack assigned to its server.")
    except (OSError, core.Error) as error:
        messagebox.showerror(core.NAME, str(error), parent=root._sheikah_window)
    _destination_changed(root)


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


def _switch_profile(root, game_dir, sheikah, main, mods=None, *, pack=None, manual=None):
    previous = getattr(root, '_sheikah_plan', None)

    def work(emit=None):
        if pack is None and manual is None:
            moved = switching.switch(game_dir, sheikah, mods, progress=emit)
        else:
            moved = switching.switch_pack(game_dir, packs.pack_id(pack), mods, progress=emit, manual=manual)
        known = mods if mods is not None else switching.cached_manifest(game_dir, pack)
        plan = core.inspect(game_dir, mods=known, pack=pack) if known else None
        if plan is not None and previous is not None and previous.offline and previous.pack == plan.pack and previous.mods == plan.mods:
            plan.offline = True
        return moved, plan

    if main:
        root._sheikah_pending = True
        _set_progress(root, "Switching mods...")
        moved, plan = window.run_waiting(root, work, "Switching mods...", progress=lambda text: _set_progress(root, text))
    else:
        moved, plan = work()
    message = (f"{(pack or packs.SHEIKAH).name} is active ({len(moved)} files moved)."
               if sheikah or pack is not None else f"Regular mods are active ({len(moved)} files moved).")
    _log(message, "info")
    if main:
        root._sheikah_plan = plan
        _show(root, plan, ui_state.offline_message(plan) if plan is not None and plan.offline else message)


def _prepare_pack(root, game_dir, main, pack=None, *, manual=False):
    name = (pack or packs.SHEIKAH).name

    def inspect():
        return core.inspect(game_dir) if pack is None else core.inspect(game_dir, pack=pack)

    try:
        plan = window.run_waiting(root, inspect, f"Checking {name}...") if main else inspect()
    except core.Cancelled:
        raise
    except core.Error as error:
        if pack is not None or manual or isinstance(error, core.Offline):
            raise
        _log("Couldn't check before launching; using installed mods. " + str(error), "warn")
        return None
    if main:
        root._sheikah_plan = plan
        root._sheikah_issue = core.Offline('offline') if plan.offline else None
        _show(root, plan, ui_state.offline_message(plan) if plan.offline else None)
    if plan.offline and not ui_state.usable(plan):
        raise core.Offline("Required mods do not match the saved list.")
    if plan.pinned and not ui_state.usable(plan):
        raise core.Error("The restored version is missing or has edited files. Restore another saved version or choose Resume updates.")
    if plan.warnings:
        if not main:
            raise core.Error("Open Sheikah Mods in TavernLauncher to review compatibility before launching.")
        if not window.review_text(_prompt_parent(root), f"{name} compatibility", plan.warnings,
                                  "Switch anyway" if manual else "Launch anyway"):
            raise core.Cancelled("Pack switch cancelled." if manual else "Launch cancelled.")
    if not plan.pinned and not plan.offline and plan.changes and (manual or not core.deferred(plan)):
        if not main:
            raise core.Error("Open Sheikah Mods in the launcher to review the available changes first.")
        if window.ask_updates(_prompt_parent(root), plan, launching=not manual):
            return window.run_waiting(root, lambda emit: core.install(plan, progress=emit),
                                      f"Preparing {name}...", progress=lambda text: _set_progress(root, text))
        if manual:
            raise core.Cancelled("Pack switch cancelled.")
        core.defer(plan)
        _log("Updates skipped; launching with installed mods.", "warn")
    return plan


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
        try:
            hint = _launcher_target(root) if main else None
        except (AttributeError, ValueError):
            hint = None

        def resolve():
            return switching.route(game_dir, target, hint=hint)

        if main and switching.needs_lookup(target, packs.catalog(game_dir)):
            id = window.run_waiting(root, resolve, "Identifying the server's mods...")
        else:
            id = resolve()
        if id == packs.REGULAR_ID:
            _switch_profile(root, game_dir, False, main)
            return
        pack = None if id == packs.SHEIKAH_ID else next(item for item in packs.catalog(game_dir) if item.id == id)
        plan = _prepare_pack(root, game_dir, main, pack)
        _switch_profile(root, game_dir, pack is None, main, plan.mods if plan is not None else None, pack=pack)
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
            if main:
                _destination_changed(root)


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
        try:
            prompt = len(packs.catalog(game_dir)) == 1
        except core.Error as error:
            _log(str(error), 'err')
            return
        check(root, game_dir, prompt=prompt)


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
