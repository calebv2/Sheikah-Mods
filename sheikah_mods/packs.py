"""Pack definitions and server matching for each game installation."""
from dataclasses import asdict, dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from . import core

SHEIKAH_ID = "sheikah"
REGULAR_ID = ""
DEFAULT_PORT = 1757
POLICY_PATH = Path(__file__).with_name("pack-policy.json")
SHARE_SCHEME = "sheikah-mods"


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    host: str
    port: int
    manifest_url: str

    @property
    def target(self):
        return self.host, self.port


SHEIKAH = Pack(SHEIKAH_ID, "Sheikah Sanctuary", core.HOST, DEFAULT_PORT, core.MANIFEST_URL)


def locked():
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as error:
        raise core.Error("Could not read pack-policy.json.") from error
    if not isinstance(policy, dict) or not isinstance(policy.get("sheikah_only"), bool):
        raise core.Error("pack-policy.json must set sheikah_only to true or false.")
    return policy["sheikah_only"]


def pack_id(pack):
    return pack.id if pack is not None else SHEIKAH_ID


def state_root(game_dir, id=SHEIKAH_ID):
    root = Path(game_dir, core.STATE_DIR)
    if id == SHEIKAH_ID:
        return root
    if not isinstance(id, str) or not re.fullmatch(r"[0-9a-f]{32}", id):
        raise core.Error("The saved mod pack ID is invalid.")
    return root / "packs" / id


def normalize_host(host):
    host = host.strip().rstrip(".").casefold()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def _definition(id, name, host, port, manifest_url):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or not name.isprintable():
        raise core.Error("Enter a pack name between 1 and 60 characters.")
    if not isinstance(host, str) or not isinstance(manifest_url, str):
        raise core.Error("Enter a server address and an HTTPS manifest URL.")
    host = normalize_host(host)
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*", host):
            raise core.Error("Enter the server hostname or IP address in its own field.")
    try:
        if not isinstance(port, (int, str)) or isinstance(port, bool) or not 1 <= int(port) <= 65535:
            raise ValueError()
    except ValueError as error:
        raise core.Error("Enter a server port between 1 and 65535.") from error
    if len(manifest_url) > 2048:
        raise core.Error("The manifest URL is too long.")
    manifest_url = core.safe_url(manifest_url.strip(), origin=None)
    return Pack(id, name.strip(), host, int(port), manifest_url)


def _catalog_path(game_dir):
    return state_root(game_dir) / "packs.json"


def _catalog(game_dir):
    try:
        data = json.loads(_catalog_path(game_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (SHEIKAH,), ()
    except (OSError, ValueError) as error:
        raise core.Error("The saved mod pack list is invalid.") from error
    try:
        if isinstance(data, list):  # v1.3
            data = {"version": 2, "packs": data, "removed": []}
        if data["version"] != 2 or not all(isinstance(data[key], list) for key in ("packs", "removed")):
            raise ValueError()
        result, ids = [SHEIKAH], {SHEIKAH_ID}
        active = tuple(_definition(**entry) for entry in data["packs"])
        removed = tuple(_definition(**entry) for entry in data["removed"])
        for pack in (*active, *removed):
            if pack.id in ids:
                raise ValueError()
            ids.add(pack.id)
            state_root(game_dir, pack.id)
        for pack in active:
            _check_unique(result, pack)
            result.append(pack)
    except (TypeError, KeyError, ValueError, core.Error) as error:
        raise core.Error("The saved mod pack list is invalid.") from error
    return tuple(result), removed


def catalog(game_dir):
    return (SHEIKAH,) if locked() else _catalog(game_dir)[0]


def _save_catalog(game_dir, active, removed):
    from .switching import _write_bytes
    data = {"version": 2, "packs": [asdict(item) for item in active if item.id != SHEIKAH_ID],
            "removed": [asdict(item) for item in removed]}
    _write_bytes(_catalog_path(game_dir), json.dumps(data, indent=2).encode("utf-8"))


def _check_unique(existing, pack):
    if any(item.id == pack.id for item in existing):
        raise core.Error("That mod pack ID is already in use.")
    if pack.name.casefold() == "regular mods" or any(
        item.name.casefold() == pack.name.casefold() for item in existing
    ):
        raise core.Error("That mod pack name is already in use.")
    if any(item.target == pack.target for item in existing):
        raise core.Error("That server already has a mod pack.")


def _editable():
    if locked():
        raise core.Error("This copy of Sheikah Mods is locked to Sheikah Sanctuary.")


def add(game_dir, name, host, port, manifest_url):
    _editable()
    pack = _definition(os.urandom(16).hex(), name, host, port, manifest_url)
    with core._install_lock:
        existing, removed = _catalog(game_dir)
        saved = next((item for item in reversed(removed)
                      if item.target == pack.target and item.manifest_url == pack.manifest_url), None)
        if saved is not None:
            pack = _definition(saved.id, name, host, port, manifest_url)
            removed = tuple(item for item in removed if item != saved)
        _check_unique(existing, pack)
        _save_catalog(game_dir, (*existing, pack), removed)
    return pack


def edit(game_dir, original, name, host, port, manifest_url):
    from . import switching
    _editable()
    pack = _definition(original.id, name, host, port, manifest_url)
    with core._install_lock:
        existing, removed = _catalog(game_dir)
        if original == SHEIKAH or original not in existing:
            raise core.Error("This pack cannot be edited. Refresh the pack list.")
        _check_unique([item for item in existing if item != original], pack)
        if pack.manifest_url != original.manifest_url:
            try:
                cached = switching.cached_manifest(game_dir, original)
            except core.Error:
                cached = ()
            if cached:
                switching.remember_manifest(game_dir, cached, original)
        _save_catalog(game_dir, [pack if item == original else item for item in existing], removed)
    return pack


def remove(game_dir, pack, running=None, progress=None):
    from . import switching
    _editable()
    with core._install_lock:
        existing, removed = _catalog(game_dir)
        if pack == SHEIKAH or pack not in existing:
            raise core.Error("This pack cannot be removed. Refresh the pack list.")
        active, manual = switching.selection(game_dir)
        if active == pack.id:
            switching.switch_pack(game_dir, REGULAR_ID, running=running, progress=progress, manual=False)
        try:
            _save_catalog(game_dir, [item for item in existing if item != pack], (*removed, pack))
        except OSError as error:
            if active == pack.id:
                try:
                    switching.switch_pack(game_dir, pack.id, running=running, progress=progress, manual=manual)
                except (core.Error, OSError) as recovery:
                    raise core.Error(f"The pack is still registered and its files are parked. Could not reapply it: {recovery}") from error
            raise


def share_link(pack):
    data = asdict(pack)
    del data["id"]
    return f"{SHARE_SCHEME}://import?" + urlencode(data)


def parse_link(link):
    try:
        link = link.strip()
        if len(link) > 8192 or not link.isprintable():
            raise ValueError()
        parts = urlsplit(link)
        if (parts.scheme, parts.netloc, parts.path, parts.fragment) != (SHARE_SCHEME, "import", "", ""):
            raise ValueError()
        fields = parse_qsl(parts.query, strict_parsing=True, errors="strict", max_num_fields=4)
        data = dict(fields)
        if len(fields) != 4 or set(data) != {"name", "host", "port", "manifest_url"}:
            raise ValueError()
    except (AttributeError, ValueError) as error:
        raise core.Error("Paste a Sheikah Mods share link created by Copy share link.") from error
    return _definition("", **data)


def import_shared(game_dir, pack):
    _editable()
    with core._install_lock:
        existing = next((item for item in catalog(game_dir)
                         if item.target == pack.target and item.manifest_url == pack.manifest_url), None)
        return existing or add(game_dir, pack.name, pack.host, pack.port, pack.manifest_url)


def create_manifest(folder, *, replace=False, release="", requirements=None):
    """Generate a manifest for DLLs published next to it on the pack's site."""
    folder = Path(folder)
    path = folder / "manifest.json"
    dlls = sorted((file for file in folder.iterdir()
                   if file.is_file() and file.suffix.lower() == ".dll"),
                  key=lambda file: file.name.casefold())
    if not 1 <= len(dlls) <= 300:
        raise core.Error("Choose a folder with between 1 and 300 client DLLs.")
    if sum(file.stat().st_size for file in dlls) > core.MAX_TOTAL:
        raise core.Error(f"The pack exceeds the {core.MAX_TOTAL / core.MIB:g} MiB download limit.")
    files = [{"filename": file.name, "sha256": core.digest(file), "url": quote(file.name)}
             for file in dlls]
    manifest = {"files": files}
    if release:
        manifest["release"] = release
    if requirements:
        manifest["compatibility"] = requirements
    data = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    core.parse_manifest(data)
    if replace:
        from .switching import _write_bytes
        _write_bytes(path, data)
    else:
        with path.open("xb") as stream:
            stream.write(data)
    return path
