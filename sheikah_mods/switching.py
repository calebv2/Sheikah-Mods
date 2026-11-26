"""Switch the active mod pack with reversible file moves."""
import ipaddress
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import socket

from . import core, packs

SERVER_PORT = 1757
PARKED_DIR = "parked"


def target_from_args(args):
    values = [str(value) for value in args]
    lowered = [value.lower() for value in values]

    def value(flag):
        if flag not in lowered:
            return None
        index = lowered.index(flag)
        if index + 1 == len(values):
            raise core.Error("The launch has an incomplete server address.")
        return values[index + 1].strip()

    host = value("/dev_server_ip")
    if host is None and "/join_local_server" in lowered:
        host = "127.0.0.1"
    if host is None:
        return None
    try:
        port = int(value("/dev_server_port") or SERVER_PORT)
        if not host or not 1 <= port <= 65535:
            raise ValueError()
    except ValueError as error:
        raise core.Error("The launch has an invalid server address.") from error
    return packs.normalize_host(host), port


def is_sheikah(target, lookup=None):
    if target is None or target[1] != SERVER_PORT:
        return False
    host = target[0].rstrip(".").casefold()
    if host == core.HOST:
        return True

    lookup = lookup or _addresses
    requested, sheikah = set(lookup(host)), set(lookup(core.HOST))
    if not requested or not sheikah:
        raise core.Error("Couldn't identify the server for mod switching. Check the address or join Sheikah using %s." % core.HOST)
    return bool(requested & sheikah)


def _addresses(host):
    try:
        return {packs.normalize_host(row[4][0]) for row in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except (OSError, UnicodeError):
        return set()


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _read_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def _switch_path(game_dir):
    return Path(game_dir, core.STATE_DIR, "switching.json")


def _selection(raw):
    if not raw:
        return packs.SHEIKAH_ID, False
    try:
        value = json.loads(raw)
        if value.get("version") == 1 and isinstance(value.get("parked"), bool):
            return (packs.REGULAR_ID if value["parked"] else packs.SHEIKAH_ID), False
        if value.get("version") == 2 and isinstance(value["manual"], bool):
            active = value["active"]
            if active != packs.REGULAR_ID:
                packs.state_root(".", active)
            return active, value["manual"]
        raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError, core.Error) as error:
        raise core.Error("The saved pack selection is invalid. No files were moved.") from error


def selection(game_dir):
    return _selection(_read_bytes(_switch_path(game_dir)))


def resume_automatic(game_dir):
    with core._install_lock:
        path = _switch_path(game_dir)
        active, manual = _selection(_read_bytes(path))
        if manual:
            _write_bytes(path, json.dumps({"version": 2, "active": active, "manual": False}).encode("utf-8"))


def is_parked(game_dir, pack=None):
    return selection(game_dir)[0] != packs.pack_id(pack)


def storage_folder(game_dir, pack=None):
    if is_parked(game_dir, pack):
        return packs.state_root(game_dir, packs.pack_id(pack)) / PARKED_DIR
    return Path(game_dir, "Mods")


def needs_lookup(target, available=()):
    return bool(target and target[0] != core.HOST
                and target[0] not in ("localhost", "127.0.0.1", "::1")
                and (_is_ip(target[0]) or target[1] == SERVER_PORT
                     or any(item.port == target[1] for item in available)))


def route(game_dir, target, hint=None, lookup=None):
    available = packs.catalog(game_dir)
    active, manual = selection(game_dir)
    if manual and not packs.locked():
        return next((pack.id for pack in available if pack.id == active), packs.REGULAR_ID)
    lookup = lookup or _addresses
    if target and hint and hint[1] == target[1] and hint != target and _is_ip(target[0]):
        hinted = next((item for item in available if item.target == hint), None)
        if hinted is not None and target[0] in lookup(hinted.host):
            return hinted.id
    exact = next((pack.id for pack in available if pack.target == target), None)
    if exact is not None:
        return exact
    if target and target[0] in ("localhost", "127.0.0.1", "::1"):
        return packs.REGULAR_ID
    candidates = [item for item in available if target and item.port == target[1]]
    if candidates:
        requested = {target[0]} if _is_ip(target[0]) else set(lookup(target[0]))
        if not requested:
            raise core.Error("Couldn't resolve the server address for mod switching.")
        matches = [item.id for item in candidates if requested.intersection(lookup(item.host))]
        if len(matches) > 1:
            raise core.Error("Multiple mod packs match this resolved server IP. Join using its registered hostname or switch manually.")
        return next(iter(matches), packs.REGULAR_ID)
    return packs.REGULAR_ID


def _write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + os.urandom(16).hex() + ".tmp")
    try:
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def remember_manifest(game_dir, mods, pack=None):
    if not mods:
        return
    path = packs.state_root(game_dir, packs.pack_id(pack)) / "manifest.json"
    manifest_url = (pack or packs.SHEIKAH).manifest_url
    data = json.dumps({**core.manifest_data(mods), "source": manifest_url}, sort_keys=True).encode("utf-8")
    if _read_bytes(path) != data:
        _write_bytes(path, data)


def cached_manifest(game_dir, pack=None):
    path = packs.state_root(game_dir, packs.pack_id(pack)) / "manifest.json"
    raw = _read_bytes(path)
    if not raw:
        return ()
    source = (pack or packs.SHEIKAH).manifest_url
    try:
        value = json.loads(raw)
    except ValueError:
        return core.parse_manifest(raw, source)
    if isinstance(value, dict) and "source" in value and value["source"] != source:
        return ()
    return core.parse_manifest(raw, source)


def _dlls(folder):
    if not folder.is_dir():
        return {}
    return {path.name.lower(): path for path in folder.iterdir()
            if path.is_file() and core.safe_name(path.name)}


@dataclass
class LaunchFiles:
    pack: tuple
    regular: tuple
    conflicts: tuple
    pending: tuple
    warnings: tuple


def launch_files(game_dir, target, pack=None):
    """Describe the currently installed DLLs for a launch without changing anything."""
    from . import compatibility, history
    with core._install_lock:
        active, _ = selection(game_dir)
        live = _dlls(Path(game_dir, "Mods"))
        regular = dict(live)
        issues = []
        if active != packs.REGULAR_ID:
            root = packs.state_root(game_dir, active)
            departing = core._read_state(_read_bytes(root / "state.json"))
            if active == packs.SHEIKAH_ID:
                current = cached_manifest(game_dir)
                for mod in current:
                    key = mod.name.lower()
                    if key in live and key not in departing and core.digest(live[key]) == mod.sha256:
                        departing[key] = {"sha256": mod.sha256}
            for key, entry in departing.items():
                path = regular.pop(key, None)
                if path is not None:
                    if active != target and core.digest(path) != entry["sha256"]:
                        issues.append(f"{path.name} has local edits that need review before switching.")
                    if active != target and entry.get("original"):
                        original = root / entry["original"]
                        if not original.is_file() or core.digest(original) != entry["original_sha256"]:
                            issues.append(f"The saved regular copy of {path.name} is missing or has local edits.")
                        else:
                            regular[key] = original
        if target == packs.REGULAR_ID:
            return LaunchFiles((), tuple(sorted(regular)), (), (), tuple(issues))

        raw = core._state_bytes(game_dir, pack)
        saved = history.read(raw)
        known = history.manifest(saved.installed, pack) if saved.pinned else cached_manifest(game_dir, pack)
        if not known:
            issues.append("No cached manifest. Check the pack for its current requirements.")
        installed = _dlls(storage_folder(game_dir, pack))
        owned = core._read_state(raw)
        valid = {}
        for key, entry in owned.items():
            path = installed.get(key)
            if path is None:
                issues.append(f"{key} is missing from the installed pack.")
            elif core.digest(path) == entry["sha256"]:
                valid[key] = (path.name, entry["sha256"])
            else:
                issues.append(f"{path.name} differs from its saved pack copy.")
        if active == target:
            for mod in known:
                key = mod.name.lower()
                path = installed.get(key)
                if key not in valid and key not in owned and path is not None and core.digest(path) == mod.sha256:
                    valid[key] = (path.name, mod.sha256)
                    regular.pop(key, None)
        conflicts = []
        for key, (name, digest) in valid.items():
            path = regular.pop(key, None)
            if path is not None:
                if not path.is_file():
                    issues.append(f"The saved regular copy of {name} is missing.")
                elif core.digest(path) != digest:
                    conflicts.append(name)
        pending = tuple(mod.name for mod in known if valid.get(mod.name.lower(), (None, None))[1] != mod.sha256)
        return LaunchFiles(tuple(name for name, _ in sorted(valid.values())),
                           tuple(sorted(live[key].name if key in live else path.name
                                        for key, path in regular.items())),
                           tuple(sorted(conflicts)), pending,
                           (*issues, *compatibility.warnings(game_dir, known)))


def switch(game_dir, sheikah, mods=None, running=None, progress=None):
    target = packs.SHEIKAH_ID if sheikah else packs.REGULAR_ID
    return switch_pack(game_dir, target, mods, running, progress)


def switch_pack(game_dir, target, mods=None, running=None, progress=None, *, manual=None):
    """Move from one active pack to another in one transaction."""
    from .runtime import game_running

    game_dir = str(Path(game_dir).resolve())
    running = running or game_running
    with core._install_lock:
        if running(game_dir):
            raise core.Error("Close A Township Tale before switching server mods.")
        path = _switch_path(game_dir)
        raw = _read_bytes(path)
        active, was_manual = _selection(raw)
        locked = packs.locked()
        manual = was_manual and not locked if manual is None else manual
        if locked and (target not in (packs.SHEIKAH_ID, packs.REGULAR_ID) or manual):
            raise core.Error("This copy of Sheikah Mods follows the Sheikah Sanctuary server.")
        if target not in (packs.SHEIKAH_ID, packs.REGULAR_ID):
            if not any(pack.id == target for pack in packs.catalog(game_dir)):
                raise core.Error("This mod pack is no longer registered.")
        updated = json.dumps({"version": 2, "active": target, "manual": manual}).encode("utf-8")
        if target == active:
            if manual != was_manual:
                _write_bytes(path, updated)
            return ()
        live_dir = Path(game_dir, "Mods")
        if not live_dir.is_dir():
            if target == packs.REGULAR_ID:
                return ()
            raise core.Error("The game has no Mods folder yet. Install MelonLoader first.")

        work = packs.state_root(game_dir) / "transactions" / os.urandom(16).hex()
        groups = []
        for id in (active, target):
            if id != packs.REGULAR_ID:
                root = packs.state_root(game_dir, id)
                state = root / "state.json"
                before = _read_bytes(state)
                files = {name: dict(entry) for name, entry in core._read_state(before).items()}
                groups.append((id, root, state, before, files))

        def parked_moves(root, files):
            live = _dlls(live_dir)
            stored = _dlls(root / PARKED_DIR)
            if active == packs.SHEIKAH_ID:
                known = tuple(mods) if target == packs.REGULAR_ID and mods is not None else cached_manifest(game_dir)
                if not known and not files and live:
                    raise core.Error("Check the Sheikah mod list once so its existing mods can be identified safely.")
                # Older versions reused matching files without claiming ownership.
                for mod in known:
                    low = mod.name.lower()
                    if low in live and low not in files and core.digest(live[low]) == mod.sha256:
                        files[low] = {"sha256": mod.sha256}
            candidates = []
            for low, entry in files.items():
                src = live.get(low)
                if src is None:
                    continue
                if core.digest(src) != entry["sha256"]:
                    raise core.Error("%s changed outside Sheikah Mods. Review it before switching packs." % src.name)
                if low in stored:
                    raise core.Error("Both Mods and the holding folder contain %s. Check the saved copies before switching." % src.name)
                if entry.get("original") and core.digest(root / entry["original"]) != entry["original_sha256"]:
                    raise core.Error("The saved original copy of %s has changed. No files were moved." % src.name)
                candidates.append((low, src, root / PARKED_DIR / src.name, entry))
            return candidates

        def live_moves(root, files):
            stored = _dlls(root / PARKED_DIR)
            pack = next((item for item in packs.catalog(game_dir) if item.id == target), None)
            known = tuple(mods) if mods is not None else cached_manifest(
                game_dir, None if target == packs.SHEIKAH_ID else pack)
            if known:
                wanted = {mod.name.lower() for mod in known}
                for low in list(files):
                    if low not in stored and low not in wanted:
                        files.pop(low)
            missing = files.keys() - stored.keys()
            if missing:
                raise core.Error("Stored pack mods are missing: %s. Check now to restore them." % ", ".join(sorted(missing)))
            candidates = []
            for low, src in stored.items():
                entry = files.get(low)
                if entry is None:
                    continue  # Retired local edits stay in their pack's holding folder.
                if core.digest(src) != entry["sha256"]:
                    raise core.Error("The stored pack copy of %s is unrecognized or changed. Review it before switching." % src.name)
                candidates.append((low, src, live_dir / src.name, entry))
            return candidates

        outgoing, incoming = [], []
        for id, root, _, _, files in groups:
            if id == active:
                outgoing = parked_moves(root, files)
            else:
                incoming = live_moves(root, files)

        records = [(state, before) for _, _, state, before, _ in groups] + [(path, raw)]
        work.mkdir(parents=True)
        moved, undo = [], []
        saved = []
        preserve_work = False

        def move(src, dst, expected):
            if core.digest(src) != expected:
                raise core.Error("A mod changed while switching packs.")
            if dst.exists():
                raise core.Error("A file appeared at %s while switching packs." % dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            undo.append((dst, src))

        total = len(outgoing) + len(incoming)

        def report_move(src):
            moved.append(src.name)
            if progress:
                progress("Switching mods %d of %d" % (len(moved), total))

        try:
            for id, _, _, before, _ in groups:
                filename = "state.before.json" if id == packs.SHEIKAH_ID else id + ".before.json"
                (work / filename).write_bytes(before)
            (work / "switching.before.json").write_bytes(raw)
            if progress:
                progress("Switching mods 0 of %d" % total)
            if running(game_dir):
                raise core.Error("The game started before its mods could be switched.")
            if any(_read_bytes(record) != before for record, before in records):
                raise core.Error("The saved mod list changed. Try switching again.")

            for _, src, dst, entry in outgoing:
                move(src, dst, entry["sha256"])
                if entry.get("original"):
                    prepared = work / "originals" / dst.name
                    prepared.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(packs.state_root(game_dir, active) / entry["original"], prepared)
                    move(prepared, live_dir / src.name, entry["original_sha256"])
                report_move(src)

            live = _dlls(live_dir)
            root = packs.state_root(game_dir, target) if incoming else None
            for low, src, dst, entry in incoming:
                baseline = live.get(low)
                if baseline is None:
                    entry.pop("original", None)
                    entry.pop("original_sha256", None)
                else:
                    baseline_hash = core.digest(baseline)
                    same_original = (entry.get("original") and baseline_hash == entry["original_sha256"]
                                     and core.digest(root / entry["original"]) == baseline_hash)
                    legacy_duplicate = target == packs.SHEIKAH_ID and baseline_hash == entry["sha256"]
                    if same_original or legacy_duplicate:
                        backup = work / "duplicates" / baseline.name
                    else:
                        relative = "backups/%s/%s" % (os.urandom(16).hex(), baseline.name)
                        backup = root / relative
                        entry.update(original=relative, original_sha256=baseline_hash)
                    move(baseline, backup, baseline_hash)
                move(src, dst, entry["sha256"])
                report_move(src)

            for _, _, state, before, files in groups:
                saved.append((state, before))
                state.parent.mkdir(parents=True, exist_ok=True)
                core._save_state(state, files)
            saved.append((path, raw))
            _write_bytes(path, updated)
        except Exception as error:
            if progress:
                progress("Restoring previous server mods...")
            problems, recovery = [], []
            for src, dst in reversed(undo):
                try:
                    if dst.exists():
                        raise OSError("restore destination is occupied")
                    os.replace(src, dst)
                except OSError:
                    problems.append(dst.name)
                    recovery.append(str(src))
            for record, before in saved:
                try:
                    if before:
                        _write_bytes(record, before)
                    else:
                        record.unlink(missing_ok=True)
                except OSError:
                    problems.append(record.name)
            if problems:
                preserve_work = True
                raise core.Error("Server switching failed and some files could not be restored (%s). Recovery copies: %s. Transaction records: %s"
                                 % (", ".join(problems), ", ".join(recovery) or str(work), work)) from error
            if isinstance(error, core.Error):
                raise
            raise core.Error("Server mods could not be switched (%s). Previous files were restored; the launch was stopped." % error) from error
        finally:
            if not preserve_work:
                shutil.rmtree(work, ignore_errors=True)
        return tuple(moved)
