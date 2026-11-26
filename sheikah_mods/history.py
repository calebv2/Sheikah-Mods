"""Keep one verified previous version per pack and pin a restored version."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote

from . import core, packs


@dataclass
class History:
    installed: dict | None = None
    previous: str | None = None
    pinned: bool = False

    def record(self):
        return {"installed": self.installed, "previous": self.previous, "pinned": self.pinned}


def read(raw):
    if not raw:
        return History()
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise core.Error("Sheikah Mods' saved file list is invalid. No files were changed.") from error
    try:
        value = document.get("history", {})
        installed = value.get("installed")
        previous = value.get("previous")
        pinned = value.get("pinned", False)
        if (not isinstance(pinned, bool) or (installed is not None and not isinstance(installed, dict))
                or (previous is not None and (not isinstance(previous, str) or not re.fullmatch(r"[a-f0-9]{32}", previous)))
                or (pinned and not installed)):
            raise ValueError()
    except (ValueError, AttributeError) as error:
        raise core.Error("The saved pack version history is invalid.") from error
    return History(installed, previous, pinned)


def directory(game_dir, pack):
    return packs.state_root(game_dir, packs.pack_id(pack)) / "versions"


def manifest(data, pack):
    source = (pack or packs.SHEIKAH).manifest_url
    return core.parse_manifest(json.dumps(data).encode("utf-8"), source)


def describe(mods):
    data = core.manifest_data(mods)
    for entry in data["files"]:
        # Version snapshots use verified local files and survive publisher URL changes.
        entry["url"] = "./" + quote(entry["filename"])
    return data


def snapshot(game_dir, pack, saved):
    if saved.previous is None:
        raise core.Error("There is no previous version saved for this pack.")
    folder = directory(game_dir, pack) / saved.previous
    try:
        with (folder / "manifest.json").open("rb") as stream:
            data = stream.read(2 * core.MIB + 1)
        if len(data) > 2 * core.MIB:
            raise ValueError()
        return folder, core.parse_manifest(data, (pack or packs.SHEIKAH).manifest_url)
    except (OSError, ValueError, core.Error) as error:
        raise core.Error("The saved version could not be read. No mods were changed.") from error


def capture(plan, folder, saved, old_files):
    if saved.installed is not None:
        mods = manifest(saved.installed, plan.pack)
    elif old_files:  # v1.3 and earlier have an ownership list, but no manifest in the state record.
        present = {path.name.casefold(): path.name for path in folder.iterdir() if path.is_file()}
        source = (plan.pack or packs.SHEIKAH).manifest_url
        mods = tuple(core.Mod(present.get(name.casefold(), name), entry["sha256"], source)
                     for name, entry in old_files.items())
    else:
        return None
    if ({mod.name.lower(): mod.sha256 for mod in mods}
            == {mod.name.lower(): mod.sha256 for mod in plan.mods}):
        return None
    present = {path.name.casefold(): path for path in folder.iterdir() if path.is_file()}
    files = [(mod, present.get(mod.name.casefold())) for mod in mods]
    if any(path is None or core.digest(path) != mod.sha256 for mod, path in files):
        return None
    if sum(path.stat().st_size for _, path in files) > core.MAX_TOTAL:
        return None
    version_id = os.urandom(16).hex()
    destination = directory(plan.game_dir, plan.pack) / version_id
    destination.mkdir(parents=True)
    try:
        for mod, path in files:
            copy = destination / mod.name
            shutil.copyfile(path, copy)
            if core.digest(copy) != mod.sha256:
                raise core.Error("A mod changed while saving the previous version. Check again.")
        (destination / "manifest.json").write_text(json.dumps(describe(mods)), encoding="utf-8")
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return version_id


def available(game_dir, pack):
    saved = read(core._state_bytes(game_dir, pack))
    return saved.previous is not None, saved.pinned


def resume(game_dir, pack):
    from .switching import _write_bytes
    with core._install_lock:
        raw = core._state_bytes(game_dir, pack)
        saved = read(raw)
        if saved.pinned:
            data = json.loads(raw)
            data["history"]["pinned"] = False
            _write_bytes(core.state_path(game_dir, pack), json.dumps(data, indent=2).encode("utf-8"))
