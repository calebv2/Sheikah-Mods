"""Activate Sheikah's DLLs only for its server, with reversible file moves."""
import json
import os
from pathlib import Path
import shutil
import socket

from . import core

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
    return host.rstrip(".").casefold(), port


def is_sheikah(target, lookup=None):
    if target is None or target[1] != SERVER_PORT:
        return False
    host = target[0].rstrip(".").casefold()
    if host == core.HOST:
        return True

    def addresses(name):
        try:
            return {row[4][0] for row in socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)}
        except (OSError, UnicodeError):
            return set()

    lookup = lookup or addresses
    requested, sheikah = set(lookup(host)), set(lookup(core.HOST))
    if not requested or not sheikah:
        raise core.Error("Couldn't identify the server for mod switching. Check the address or join Sheikah using %s." % core.HOST)
    return bool(requested & sheikah)


def _read_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def _switch_path(game_dir):
    return Path(game_dir, core.STATE_DIR, "switching.json")


def _parked(raw):
    if not raw:
        return False
    try:
        value = json.loads(raw)
        if value.get("version") != 1 or not isinstance(value.get("parked"), bool):
            raise ValueError()
        return value["parked"]
    except (ValueError, TypeError, AttributeError) as error:
        raise core.Error("Sheikah Mods' switching record is invalid. No files were moved.") from error


def is_parked(game_dir):
    return _parked(_read_bytes(_switch_path(game_dir)))


def storage_folder(game_dir):
    return Path(game_dir, core.STATE_DIR, PARKED_DIR) if is_parked(game_dir) else Path(game_dir, "Mods")


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


def remember_manifest(game_dir, mods):
    if not mods:
        return
    path = Path(game_dir, core.STATE_DIR, "manifest.json")
    data = json.dumps({"files": [{"filename": mod.name, "sha256": mod.sha256, "url": mod.url}
                                  for mod in mods]}, sort_keys=True).encode("utf-8")
    if _read_bytes(path) != data:
        _write_bytes(path, data)


def cached_manifest(game_dir):
    raw = _read_bytes(Path(game_dir, core.STATE_DIR, "manifest.json"))
    return core.parse_manifest(raw) if raw else ()


def _dlls(folder):
    if not folder.is_dir():
        return {}
    return {path.name.lower(): path for path in folder.iterdir()
            if path.is_file() and core.safe_name(path.name)}


def switch(game_dir, sheikah, mods=None, running=None, progress=None):
    """Return moved filenames. Never guess ownership from a filename alone."""
    from .runtime import game_running
    game_dir = str(Path(game_dir).resolve())
    with core._install_lock:
        if (running or game_running)(game_dir):
            raise core.Error("Close A Township Tale before switching server mods.")
        switch_path = _switch_path(game_dir)
        switch_raw = _read_bytes(switch_path)
        parked = _parked(switch_raw)
        if parked == (not sheikah):
            return ()
        state_raw = core._state_bytes(game_dir)
        files = {name: dict(entry) for name, entry in core._read_state(state_raw).items()}
        root = Path(game_dir, core.STATE_DIR)
        live_dir = Path(game_dir, "Mods")
        holding = root / PARKED_DIR
        if not live_dir.is_dir():
            if not sheikah:
                return ()
            raise core.Error("The game has no Mods folder yet. Install MelonLoader first.")
        known = tuple(mods) if mods is not None else cached_manifest(game_dir)
        live = _dlls(live_dir)
        stored = _dlls(holding)
        candidates = []
        if not sheikah:
            if not known and not files:
                raise core.Error("Check the Sheikah mod list once before joining another server, so its mods can be identified safely.")
            # Older versions reused matching files without owning them. Adopt
            # only verified matches so those installs switch correctly too.
            for mod in known:
                path = live.get(mod.name.lower())
                if path is not None and mod.name.lower() not in files and core.digest(path) == mod.sha256:
                    files[mod.name.lower()] = {"sha256": mod.sha256}
            for low, entry in files.items():
                path = live.get(low)
                if path is None:
                    continue
                if core.digest(path) != entry["sha256"]:
                    raise core.Error("%s changed outside Sheikah Mods. Check and review it before switching servers." % path.name)
                if low in stored:
                    raise core.Error("Both Mods and the holding folder contain %s. Check the saved copies before switching." % path.name)
                if entry.get("original") and core.digest(root / entry["original"]) != entry["original_sha256"]:
                    raise core.Error("The saved original copy of %s has changed. No files were moved." % path.name)
                candidates.append((low, path, holding / path.name, entry))
        else:
            wanted = {mod.name.lower() for mod in known}
            if known:
                for low in list(files):
                    if low not in stored and low not in wanted:
                        # A missing, retired server file must not block a complete
                        # current set or claim a regular local copy in Mods.
                        files.pop(low)
            for low, path in stored.items():
                entry = files.get(low)
                if entry is None:
                    continue  # Retired local edits stay parked, never deployed or deleted.
                if core.digest(path) != entry["sha256"]:
                    raise core.Error("The stored Sheikah copy of %s is unrecognized or changed. Check and review it before launching." % path.name)
                candidates.append((low, path, live_dir / path.name, entry))
            missing = [low for low in files if low not in stored]
            if missing:
                raise core.Error("Stored Sheikah mods are missing: %s. Check now to restore them before launching." % ", ".join(missing))
        work = root / "transactions" / os.urandom(16).hex()
        work.mkdir(parents=True)
        (work / "state.before.json").write_bytes(state_raw)
        (work / "switching.before.json").write_bytes(switch_raw)
        moved, undo = [], []
        preserve_work = False

        def move(src, dst, expected):
            if core.digest(src) != expected:
                raise core.Error("A mod changed while switching servers. The launch was stopped.")
            if dst.exists():
                raise core.Error("A file appeared at %s while switching. The launch was stopped." % dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            undo.append((dst, src))

        try:
            if progress:
                progress("Switching mods 0 of %d" % len(candidates))
            if (running or game_running)(game_dir):
                raise core.Error("The game started before its mods could be switched.")
            if core._state_bytes(game_dir) != state_raw or _read_bytes(switch_path) != switch_raw:
                raise core.Error("The saved mod list changed. Try launching again.")
            for low, src, dst, entry in candidates:
                if sheikah and low in live:
                    baseline = live[low]
                    baseline_hash = core.digest(baseline)
                    same_server_copy = baseline_hash == entry["sha256"]
                    same_original = (entry.get("original") and baseline_hash == entry["original_sha256"]
                                     and core.digest(root / entry["original"]) == baseline_hash)
                    if same_server_copy or same_original:
                        backup = work / "duplicates" / baseline.name
                    else:
                        relative = "backups/%s/%s" % (os.urandom(16).hex(), baseline.name)
                        backup = root / relative
                        entry.update(original=relative, original_sha256=baseline_hash)
                    move(baseline, backup, baseline_hash)
                move(src, dst, entry["sha256"])
                moved.append(src.name)
                if not sheikah and entry.get("original"):
                    prepared = work / "originals" / dst.name
                    prepared.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(root / entry["original"], prepared)
                    move(prepared, live_dir / src.name, entry["original_sha256"])
                if progress:
                    progress("Switching mods %d of %d" % (len(moved), len(candidates)))
            core._save_state(core.state_path(game_dir), files)
            _write_bytes(switch_path, json.dumps({"version": 1, "parked": not sheikah}).encode("utf-8"))
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
            for path, raw in ((core.state_path(game_dir), state_raw), (switch_path, switch_raw)):
                try:
                    if raw:
                        _write_bytes(path, raw)
                    else:
                        path.unlink(missing_ok=True)
                except OSError:
                    problems.append(path.name)
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
