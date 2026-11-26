"""Manifest checks and transactional installation for each mod pack."""
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

NAME = "Sheikah Mods"
VERSION = "1.4.0"
MANIFEST_URL = "https://att.aservertown.fans/manifest.json"
HOST = "att.aservertown.fans"
STATE_DIR = "SheikahMods"
GAME_EXE = "A Township Tale.exe"
MIB = 1024 * 1024
MAX_FILE = 512 * MIB
MAX_TOTAL = 512 * MIB
_RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}


class Error(Exception):
    pass


class Cancelled(Error):
    pass


class Offline(Error):
    pass


class CertificateError(Offline):
    pass


class DownloadTooLarge(Error):
    pass


def safe_name(name):
    return (isinstance(name, str) and name.lower().endswith(".dll")
            and not re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            and not name.startswith((".", " ")) and name == name.strip()
            and name.split(".")[0].lower() not in _RESERVED)


def safe_url(url, origin=HOST):
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname
                 and (origin is None or parsed.hostname == origin)
                 and parsed.port in (None, 443) and parsed.username is None
                 and parsed.password is None and not parsed.fragment
                 and not re.search(r"[\s\\\x00-\x1f\x7f]", url))
    except ValueError:
        valid = False
    if not valid:
        expected = " from " + origin if origin else ""
        raise Error("Enter a valid HTTPS download URL" + expected + ".")
    return url


class _SameSiteRedirects(HTTPRedirectHandler):
    def __init__(self, origin):
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl, self.origin)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_context():
    # Supplying cafile avoids loading stale intermediates from machine stores.
    # Keep this local to the addon; other launcher connections use their own TLS.
    return ssl.create_default_context(cafile=str(Path(__file__).with_name("cacert.pem")))


@contextmanager
def _open_download(url, timeout=20, origin=HOST):
    safe_url(url, origin)
    request = Request(url, headers={"User-Agent": "SheikahMods/" + VERSION,
                                   "Cache-Control": "no-cache, no-store", "Pragma": "no-cache"})
    try:
        context = _download_context()
        response = build_opener(_SameSiteRedirects(origin), HTTPSHandler(context=context)).open(request, timeout=timeout)
    except Error:
        raise
    except Exception as error:
        reason = getattr(error, "reason", None) or error
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise CertificateError(
                "Couldn't verify the mod pack site's HTTPS certificate. "
                "Check your PC's date and time and install the latest Sheikah Mods addon "
                "(%s; %s; %s)." % (reason, ssl.OPENSSL_VERSION, time.strftime("%Y-%m-%d %H:%M:%S %z"))
            ) from error
        raise Offline("Couldn't reach the mod pack site (%s)." % reason) from error
    with response:
        safe_url(response.geturl(), origin)
        yield response


def _read_download(response, size):
    try:
        return response.read(size)
    except Exception as error:
        raise Offline("The Sheikah mod download was interrupted (%s)." % error) from error


def fetch(url, limit, timeout=20, origin=HOST):
    with _open_download(url, timeout, origin) as response:
        data = _read_download(response, limit + 1)
    if len(data) > limit:
        raise DownloadTooLarge("The Sheikah mod list exceeds the download size limit.")
    return data


@dataclass(frozen=True)
class Mod:
    name: str
    sha256: str
    url: str


class Manifest(tuple):
    def __new__(cls, mods, release="", compatibility=None):
        manifest = super().__new__(cls, mods)
        manifest.release = release
        manifest.compatibility = compatibility or {}
        return manifest


def manifest_data(mods):
    data = {"files": [{"filename": mod.name, "sha256": mod.sha256, "url": mod.url} for mod in mods]}
    for field in ("release", "compatibility"):
        value = getattr(mods, field, None)
        if value:
            data[field] = value
    return data


def read_manifest(fetcher=None, manifest_url=MANIFEST_URL):
    parts = urlsplit(safe_url(manifest_url, None))
    query = "&".join(filter(None, (parts.query, "_sheikah_check=" + str(time.time_ns()))))
    url = urlunsplit(parts._replace(query=query))
    if fetcher is not None:
        data = fetcher(url, 2 * MIB)
    elif manifest_url == MANIFEST_URL:
        data = fetch(url, 2 * MIB)
    else:
        data = fetch(url, 2 * MIB, origin=parts.hostname)
    return parse_manifest(data, manifest_url)


def parse_manifest(data, manifest_url=MANIFEST_URL):
    from . import compatibility
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as error:
        raise Error("The pack site didn't return a valid mod list.") from error
    entries = value.get("files") if isinstance(value, dict) else None
    if not isinstance(entries, list) or not entries or len(entries) > 300:
        raise Error("The mod list is empty or invalid. Installed mods were left alone.")
    release = value.get("release", "")
    if not isinstance(release, str) or len(release) > 60 or (release and not release.isprintable()):
        raise Error("The manifest has an invalid release label.")
    requirements = compatibility.normalize(value.get("compatibility", {}))
    manifest_host = urlsplit(safe_url(manifest_url, None)).hostname
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
        mods.append(Mod(name, digest.upper(), safe_url(urljoin(manifest_url, url), manifest_host)))
        seen.add(name.lower())
    return Manifest(sorted(mods, key=lambda mod: mod.name.casefold()), release, requirements)


class ManifestChecks:
    def __init__(self, manifest_url=MANIFEST_URL):
        self.manifest_url = manifest_url
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
                mods = read_manifest() if self.manifest_url == MANIFEST_URL else read_manifest(manifest_url=self.manifest_url)
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
_pack_checks = {}
_install_lock = threading.RLock()
_deferred = {}


def digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest().upper()


def state_path(game_dir, pack=None):
    from . import packs
    return packs.state_root(game_dir, packs.pack_id(pack)) / "state.json"


def _state_bytes(game_dir, pack=None):
    try:
        return state_path(game_dir, pack).read_bytes()
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
    pack: object = None
    pinned: bool = False
    warnings: tuple = ()

    @property
    def fingerprint(self):
        return tuple((c.name, c.action, c.current, c.mod.sha256 if c.mod else "") for c in self.changes)


def inspect(game_dir, mods=None, force=False, pack=None):
    from . import history, packs, switching
    if pack is not None and packs.locked():
        raise Error("This copy of Sheikah Mods is locked to Sheikah Sanctuary.")
    game_dir = str(Path(game_dir).resolve())
    if not Path(game_dir, "Mods").is_dir():
        raise Error("Install MelonLoader with TavernLauncher's Mods button, then start the game once to create its Mods folder.")
    with _install_lock:
        saved = history.read(_state_bytes(game_dir, pack))
        if saved.pinned:
            return _inspect_files(game_dir, history.manifest(saved.installed, pack), False, pack, pinned=True)
    offline = False
    if mods is not None:
        if not isinstance(mods, tuple):
            mods = tuple(mods)
    else:
        try:
            with _install_lock:
                source = checks if pack is None else _pack_checks.setdefault(pack, ManifestChecks(pack.manifest_url))
            mods = source.get(force)
        except Offline:
            offline = True
    with _install_lock:
        saved = history.read(_state_bytes(game_dir, pack))
        if saved.pinned:
            return _inspect_files(game_dir, history.manifest(saved.installed, pack), False, pack, pinned=True)
        if offline:
            mods = switching.cached_manifest(game_dir, pack)
            if not mods:
                raise Offline("No saved manifest is available for this mod pack.")
        else:
            switching.remember_manifest(game_dir, mods, pack)
        return _inspect_files(game_dir, mods, offline, pack)


def _inspect_files(game_dir, mods, offline, pack, pinned=False):
    from . import compatibility, switching
    folder = switching.storage_folder(game_dir, pack)
    raw = _state_bytes(game_dir, pack)
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
                str(folder.relative_to(Path(game_dir))), offline, pack, pinned,
                compatibility.warnings(game_dir, mods))


def restore_plan(game_dir, pack=None):
    from . import history
    game_dir = str(Path(game_dir).resolve())
    with _install_lock:
        saved = history.read(_state_bytes(game_dir, pack))
        _, mods = history.snapshot(game_dir, pack, saved)
        return _inspect_files(game_dir, mods, False, pack)


def defer(plan):
    _deferred[plan.game_dir, plan.pack] = plan.fingerprint


def deferred(plan):
    return _deferred.get((plan.game_dir, plan.pack)) == plan.fingerprint


def _unchanged(plan):
    from . import switching
    if _state_bytes(plan.game_dir, plan.pack) != plan.state_raw:
        return False
    folder = Path(plan.game_dir, plan.storage)
    if switching.storage_folder(plan.game_dir, plan.pack) != folder:
        return False
    return all((digest(folder / name) if (folder / name).is_file() else None) == expected
               for name, expected in plan.observed.items())


def _save_state(path, files, history_record=None):
    if history_record is None and path.is_file():
        history_record = json.loads(path.read_bytes()).get("history")
    data = {"version": 1, "files": files, "checked": time.strftime("%Y-%m-%d %H:%M")}
    if history_record is not None:
        data["history"] = history_record
    temp = path.with_name("state-" + os.urandom(16).hex() + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _stage_download(mod, destination, reserve_bytes, failed, fetcher, pack=None, local=None):
    if local is not None:
        source = (local / mod.name).open("rb")
    elif fetcher is not None:
        source = BytesIO(fetcher(mod.url, MAX_FILE))
    elif pack is None:
        source = _open_download(mod.url)
    else:
        source = _open_download(mod.url, origin=urlsplit(pack.manifest_url).hostname)
    checksum = hashlib.sha256()
    size = 0
    with source as response, destination.open("wb") as output:
        while not failed.is_set():
            chunk = response.read(MIB) if local is not None else _read_download(response, MIB)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE:
                raise DownloadTooLarge(f"{mod.name} exceeds the {MAX_FILE / MIB:g} MiB per-file download limit.")
            reserve_bytes(len(chunk))
            checksum.update(chunk)
            output.write(chunk)
    if not failed.is_set() and checksum.hexdigest().upper() != mod.sha256:
        raise Error(mod.name + " failed its SHA-256 check. Nothing was installed.")


def install(plan, fetcher=None, running=None, progress=None, *, restoring=False):
    from . import history, packs
    from .runtime import game_running
    with _install_lock:
        if plan.pack is not None and packs.locked():
            raise Error("This copy of Sheikah Mods is locked to Sheikah Sanctuary.")
        if plan.pack is not None and plan.pack not in packs.catalog(plan.game_dir):
            raise Error("The registered pack changed. Check it again before applying updates.")
        if (running or game_running)(plan.game_dir):
            raise Error("Close A Township Tale before installing or updating Sheikah Mods.")
        if not _unchanged(plan):
            raise Error("Your mods changed after the check. Check again before applying updates.")
        saved = history.read(plan.state_raw)
        local = None
        if restoring:
            local, expected = history.snapshot(plan.game_dir, plan.pack, saved)
            if history.describe(expected) != history.describe(plan.mods):
                raise Error("The saved version changed. Review it again before restoring.")
        elif saved.pinned:
            raise Error("Choose Resume updates before installing a newer version.")
        folder = Path(plan.game_dir, plan.storage)
        folder.mkdir(parents=True, exist_ok=True)
        root = packs.state_root(plan.game_dir, packs.pack_id(plan.pack))
        work = root / "transactions" / os.urandom(16).hex()
        work.mkdir(parents=True)
        previous = work / "previous"
        previous.mkdir()
        old_files = _read_state(plan.state_raw)
        files = {name: dict(entry) for name, entry in old_files.items()}
        staged, placed, original_backups = [], [], []
        preserve_work = False
        captured = None
        try:
            downloads = [change for change in plan.changes if change.mod is not None]
            budget_lock = threading.Lock()
            failed = threading.Event()
            failure = None
            downloaded_bytes = 0
            completed = 0
            operation = "Reading saved files" if restoring else "Downloading"
            if progress and downloads:
                progress(f"{operation} 0 of {len(downloads)}")

            def reserve_bytes(size):
                nonlocal downloaded_bytes
                with budget_lock:
                    if downloaded_bytes + size > MAX_TOTAL:
                        raise DownloadTooLarge(f"The update exceeds the {MAX_TOTAL / MIB:g} MiB download limit. Nothing was installed.")
                    downloaded_bytes += size

            def download(change):
                nonlocal completed, failure
                if failed.is_set():
                    return
                try:
                    _stage_download(change.mod, work / change.name, reserve_bytes, failed, fetcher, plan.pack, local)
                    with budget_lock:
                        if failed.is_set():
                            return
                        completed += 1
                        if progress:
                            progress(f"{operation} {completed} of {len(downloads)}")
                except Exception as error:
                    with budget_lock:
                        if failure is None:
                            failure = error
                            failed.set()

            if downloads:
                with ThreadPoolExecutor(max_workers=min(4, len(downloads))) as pool:
                    list(pool.map(download, downloads))
                if failure is not None:
                    raise failure
            for change in plan.changes:
                if change.action == "restore":
                    entry = old_files[change.name.lower()]
                    backup = root / entry["original"]
                    if digest(backup) != entry["original_sha256"]:
                        raise Error("The saved original copy of %s has changed. Nothing was installed." % change.name)
                    shutil.copyfile(backup, work / change.name)
            if (running or game_running)(plan.game_dir) or not _unchanged(plan):
                raise Error("The game started or your mods changed during the download. Nothing was installed; check again.")
            captured = history.capture(plan, folder, saved, old_files)
            if captured is not None and ((running or game_running)(plan.game_dir) or not _unchanged(plan)):
                raise Error("The game started or your mods changed while saving the previous version.")
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
                    if plan.storage == "Mods" and (not restoring or not entry.get("original")):
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
            previous_id = captured or (saved.previous if not restoring else None)
            updated = history.History(history.describe(plan.mods), previous_id, restoring)
            _save_state(state_path(plan.game_dir, plan.pack), files, updated.record())
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
            if captured is not None:
                shutil.rmtree(history.directory(plan.game_dir, plan.pack) / captured, ignore_errors=True)
            if isinstance(error, Error):
                raise
            raise Error("The update couldn't be saved (%s). Previous files were restored." % error) from error
        finally:
            if not preserve_work:
                shutil.rmtree(work, ignore_errors=True)
        _deferred.pop((plan.game_dir, plan.pack), None)
        if saved.previous and saved.previous != previous_id:
            shutil.rmtree(history.directory(plan.game_dir, plan.pack) / saved.previous, ignore_errors=True)
        return _inspect_files(plan.game_dir, plan.mods, False, plan.pack, pinned=restoring)
