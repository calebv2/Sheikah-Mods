"""Fixed-manifest checks and transactional installation; no Modpacks dependency."""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

NAME = "Sheikah Mods"
VERSION = "1.2.1"
MANIFEST_URL = "https://att.aservertown.fans/manifest.json"
HOST = "att.aservertown.fans"
STATE_DIR = "SheikahMods"
GAME_EXE = "A Township Tale.exe"
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
_RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}


class Error(Exception):
    pass


class Cancelled(Error):
    pass


class Offline(Error):
    pass


def safe_name(name):
    return (isinstance(name, str) and name.lower().endswith(".dll")
            and not re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            and not name.startswith((".", " ")) and name == name.strip()
            and name.split(".")[0].lower() not in _RESERVED)


def safe_url(url):
    parsed = urlparse(url)
    try:
        valid = (parsed.scheme == "https" and parsed.hostname == HOST
                 and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except ValueError:
        valid = False
    if not valid:
        raise Error("Sheikah Mods only downloads over HTTPS from " + HOST + ".")
    return url


class _SameSiteRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit, timeout=20):
    safe_url(url)
    request = Request(url, headers={"User-Agent": "SheikahMods/" + VERSION,
                                   "Cache-Control": "no-cache, no-store", "Pragma": "no-cache"})
    try:
        with build_opener(_SameSiteRedirects()).open(request, timeout=timeout) as response:
            safe_url(response.geturl())
            data = response.read(limit + 1)
    except Error:
        raise
    except Exception as error:
        raise Offline("Couldn't reach the Sheikah mod site (%s)." % (getattr(error, "reason", None) or error)) from error
    if len(data) > limit:
        raise Error("The downloaded file is larger than allowed.")
    return data


@dataclass(frozen=True)
class Mod:
    name: str
    sha256: str
    url: str


def read_manifest(fetcher=None):
    # The URL is fixed; only the site's list of files changes between releases.
    url = MANIFEST_URL + "?_sheikah_check=" + str(time.time_ns())
    data = (fetcher or fetch)(url, 2 * 1024 * 1024)
    return parse_manifest(data)


def parse_manifest(data):
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as error:
        raise Error("The Sheikah site didn't return a valid mod list.") from error
    entries = value.get("files") if isinstance(value, dict) else None
    if not isinstance(entries, list) or not entries or len(entries) > 300:
        raise Error("The Sheikah mod list is empty or invalid. Installed mods were left alone.")
    mods, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or not safe_name(entry.get("filename")):
            raise Error("The mod list contains an unsafe file name.")
        name, digest, url = entry["filename"], entry.get("sha256"), entry.get("url")
        if name.lower().endswith(".server.dll") or name.lower().startswith("circuitsvoicechat"):
            raise Error("The list contains a server-only or launcher-managed mod: " + name)
        if name.lower() in seen:
            raise Error("The mod list contains %s more than once." % name)
        if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise Error("The mod list has an invalid SHA-256 for " + name)
        if not isinstance(url, str):
            raise Error("The mod list has no download URL for " + name)
        mods.append(Mod(name, digest.upper(), safe_url(urljoin(MANIFEST_URL, url))))
        seen.add(name.lower())
    return tuple(sorted(mods, key=lambda mod: mod.name.casefold()))


class ManifestChecks:
    def __init__(self):
        self.lock = threading.Lock()
        self.result = None
        self.expires = 0

    def get(self, force=False):
        with self.lock:
            owner = self.result is None or (self.result.done() and (force or time.monotonic() >= self.expires))
            if owner:
                self.result = Future()
                self.expires = float("inf")
            future = self.result
        if owner:
            try:
                mods = read_manifest()
            except Exception as error:
                lifetime = 10
                future.set_exception(error)
            else:
                lifetime = 120
                future.set_result(mods)
            with self.lock:
                self.expires = time.monotonic() + lifetime
        return future.result()


checks = ManifestChecks()
_install_lock = threading.Lock()
_deferred = {}


def digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest().upper()


def state_path(game_dir):
    return Path(game_dir, STATE_DIR, "state.json")


def _state_bytes(game_dir):
    try:
        return state_path(game_dir).read_bytes()
    except FileNotFoundError:
        return b""


def _read_state(raw):
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        files = value["files"]
        if value.get("version") != 1 or not isinstance(files, dict):
            raise ValueError("invalid format")
        for name, entry in files.items():
            if (not safe_name(name) or not isinstance(entry, dict)
                    or not re.fullmatch(r"[A-Fa-f0-9]{64}", str(entry.get("sha256", "")))):
                raise ValueError("invalid managed file")
            if entry.get("original"):
                backup = entry["original"]
                if (not isinstance(backup, str) or not re.fullmatch(r"backups/[0-9a-f]{32}/[^/\\]+", backup)
                        or not safe_name(backup.rsplit("/", 1)[-1])
                        or not re.fullmatch(r"[A-Fa-f0-9]{64}", str(entry.get("original_sha256", "")))):
                    raise ValueError("invalid original copy")
        return files
    except (ValueError, KeyError, TypeError) as error:
        raise Error("Sheikah Mods' saved file list is invalid. No files were changed.") from error


@dataclass(frozen=True)
class Change:
    name: str
    action: str
    current: str | None
    mod: Mod | None = None
    note: str = ""


@dataclass
class Plan:
    game_dir: str
    mods: tuple
    changes: tuple
    ready: tuple
    state_raw: bytes
    observed: dict
    storage: str = "Mods"
    offline: bool = False

    @property
    def fingerprint(self):
        return tuple((c.name, c.action, c.current, c.mod.sha256 if c.mod else "") for c in self.changes)


def inspect(game_dir, mods=None, force=False):
    from . import switching
    game_dir = str(Path(game_dir).resolve())
    if not Path(game_dir, "Mods").is_dir():
        raise Error("Install MelonLoader with TavernLauncher's Mods button, then start the game once to create its Mods folder.")
    offline = False
    if mods is not None:
        mods = tuple(mods)
    else:
        try:
            mods = checks.get(force)
        except Offline:
            mods = switching.cached_manifest(game_dir)
            if not mods:
                raise
            offline = True
    switching.remember_manifest(game_dir, mods)
    folder = switching.storage_folder(game_dir)
    raw = _state_bytes(game_dir)
    managed = _read_state(raw)
    present = {path.name.lower(): path.name for path in folder.iterdir() if path.is_file()} if folder.is_dir() else {}
    observed, changes, ready = {}, [], []
    wanted = {mod.name.lower() for mod in mods}
    for mod in mods:
        name = present.get(mod.name.lower(), mod.name)
        current = digest(folder / name) if mod.name.lower() in present else None
        observed[name] = current
        owned = managed.get(mod.name.lower())
        if current == mod.sha256:
            ready.append(name)
        elif current is None:
            changes.append(Change(name, "install", current, mod))
        elif owned and current == owned["sha256"]:
            changes.append(Change(name, "update", current, mod))
        else:
            changes.append(Change(name, "replace", current, mod, "Your existing copy will be backed up before replacement."))
    for low, entry in managed.items():
        if low in wanted:
            continue
        name = present.get(low, low)
        current = digest(folder / name) if low in present else None
        observed[name] = current
        if current is None or current != entry["sha256"]:
            changes.append(Change(name, "keep", current, note="No longer listed. Your local copy is left alone."))
        elif entry.get("original") and folder == Path(game_dir, "Mods"):
            changes.append(Change(name, "restore", current, note="No longer listed. Your original local copy will be restored."))
        else:
            changes.append(Change(name, "remove", current, note="No longer listed. Only the unchanged copy installed by Sheikah Mods is removed."))
    return Plan(game_dir, mods, tuple(changes), tuple(ready), raw, observed,
                str(folder.relative_to(Path(game_dir))), offline)


def defer(plan):
    _deferred[plan.game_dir] = plan.fingerprint


def deferred(plan):
    return _deferred.get(plan.game_dir) == plan.fingerprint


def _unchanged(plan):
    from . import switching
    if _state_bytes(plan.game_dir) != plan.state_raw:
        return False
    folder = Path(plan.game_dir, plan.storage)
    if switching.storage_folder(plan.game_dir) != folder:
        return False
    return all((digest(folder / name) if (folder / name).is_file() else None) == expected
               for name, expected in plan.observed.items())


def _save_state(path, files):
    temp = path.with_name("state-" + os.urandom(16).hex() + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump({"version": 1, "files": files, "checked": time.strftime("%Y-%m-%d %H:%M")}, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def install(plan, fetcher=None, running=None, progress=None):
    from .runtime import game_running
    with _install_lock:
        if (running or game_running)(plan.game_dir):
            raise Error("Close A Township Tale before installing or updating Sheikah Mods.")
        if not _unchanged(plan):
            raise Error("Your mods changed after the check. Check again before applying updates.")
        folder = Path(plan.game_dir, plan.storage)
        folder.mkdir(parents=True, exist_ok=True)
        root = Path(plan.game_dir, STATE_DIR)
        work = root / "transactions" / os.urandom(16).hex()
        work.mkdir(parents=True)
        previous = work / "previous"
        previous.mkdir()
        old_files = _read_state(plan.state_raw)
        files = {name: dict(entry) for name, entry in old_files.items()}
        staged, placed, original_backups = [], [], []
        preserve_work = False
        try:
            downloads = [change for change in plan.changes if change.mod is not None]
            budget_lock = threading.Lock()
            failed = threading.Event()
            downloaded_bytes = 0
            completed = 0
            if progress and downloads:
                progress("Downloading 0 of %d" % len(downloads))

            def download(change):
                nonlocal downloaded_bytes, completed
                try:
                    if failed.is_set():
                        raise Error("The update download was stopped.")
                    data = (fetcher or fetch)(change.mod.url, MAX_FILE)
                    if len(data) > MAX_FILE or hashlib.sha256(data).hexdigest().upper() != change.mod.sha256:
                        raise Error(change.name + " failed its SHA-256 check. Nothing was installed.")
                    with budget_lock:
                        downloaded_bytes += len(data)
                        if downloaded_bytes > MAX_TOTAL:
                            raise Error("The update is too large. Nothing was installed.")
                    (work / change.name).write_bytes(data)
                    with budget_lock:
                        completed += 1
                        if progress:
                            progress("Downloading %d of %d" % (completed, len(downloads)))
                except Exception:
                    failed.set()
                    raise

            if downloads:
                with ThreadPoolExecutor(max_workers=min(4, len(downloads))) as pool:
                    list(pool.map(download, downloads))
            for change in plan.changes:
                if change.action == "restore":
                    entry = old_files[change.name.lower()]
                    backup = root / entry["original"]
                    if digest(backup) != entry["original_sha256"]:
                        raise Error("The saved original copy of %s has changed. Nothing was installed." % change.name)
                    shutil.copyfile(backup, work / change.name)
            if (running or game_running)(plan.game_dir) or not _unchanged(plan):
                raise Error("The game started or your mods changed during the download. Nothing was installed; check again.")
            if progress:
                progress("Installing verified mods...")
            for change in plan.changes:
                if change.action == "keep":
                    files.pop(change.name.lower(), None)
                    continue
                live = folder / change.name
                entry = dict(old_files.get(change.name.lower(), {}))
                if change.action == "replace":
                    relative = "backups/%s/%s" % (os.urandom(16).hex(), change.name)
                    backup = root / relative
                    backup.parent.mkdir(parents=True)
                    shutil.copyfile(live, backup)
                    if digest(backup) != change.current:
                        raise Error("A local mod changed while making its backup. Check again.")
                    original_backups.append(backup)
                    if plan.storage == "Mods":
                        entry.update(original=relative, original_sha256=change.current)
                if change.current is not None:
                    os.replace(live, previous / change.name)
                    staged.append((live, previous / change.name))
                if change.action in ("install", "update", "replace", "restore"):
                    os.replace(work / change.name, live)
                    placed.append(live)
                if change.mod is not None:
                    entry["sha256"] = change.mod.sha256
                    files[change.name.lower()] = entry
                else:
                    files.pop(change.name.lower(), None)
            _save_state(state_path(plan.game_dir), files)
        except Exception as error:
            if progress:
                progress("Restoring previous files...")
            problems = []
            for live in reversed(placed):
                try:
                    live.unlink(missing_ok=True)
                except OSError:
                    problems.append(live.name)
            for live, backup in reversed(staged):
                try:
                    os.replace(backup, live)
                except OSError:
                    problems.append(live.name)
            if problems:
                preserve_work = True
                raise Error("The update failed and some files could not be restored (%s). Recovery copies: %s"
                            % (", ".join(problems), work)) from error
            for backup in original_backups:
                backup.unlink(missing_ok=True)
            if isinstance(error, Error):
                raise
            raise Error("The update couldn't be saved (%s). Previous files were restored." % error) from error
        finally:
            if not preserve_work:
                shutil.rmtree(work, ignore_errors=True)
        _deferred.pop(plan.game_dir, None)
        return inspect(plan.game_dir, plan.mods)
