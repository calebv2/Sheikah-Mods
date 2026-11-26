"""Publisher-declared game and MelonLoader compatibility."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import re

LABELS = {"game_builds": "Game build", "melonloader_versions": "MelonLoader"}


def normalize(value):
    from .core import Error
    if not isinstance(value, dict) or value.keys() - LABELS.keys():
        raise Error("The manifest has invalid compatibility fields.")
    result = {}
    for key, versions in value.items():
        if (not isinstance(versions, list) or not 1 <= len(versions) <= 30
                or any(not isinstance(item, str) or not re.fullmatch(r"[\x21-\x7e]{1,128}", item) for item in versions)):
            raise Error(f"The manifest has an invalid {LABELS[key]} version list.")
        if key == "melonloader_versions" and any(not re.fullmatch(r"\d{1,5}(?:\.\d{1,5}){2,3}", item) for item in versions):
            raise Error("Use numeric MelonLoader versions such as 0.7.3.")
        result[key] = list(dict.fromkeys(versions))
    return result


def game_build(game_dir):
    try:
        with Path(game_dir, "version").open("r", encoding="utf-8-sig") as stream:
            build = stream.read(512).strip()
        return build if re.fullmatch(r"[\x21-\x7e]{1,128}", build) else None
    except (OSError, UnicodeError):
        return None


def _file_version(path):
    try:
        version = ctypes.WinDLL("version.dll")
        size_of = version.GetFileVersionInfoSizeW
        size_of.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
        size_of.restype = wintypes.DWORD
        read = version.GetFileVersionInfoW
        read.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
        read.restype = wintypes.BOOL
        query = version.VerQueryValueW
        query.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                          ctypes.POINTER(wintypes.UINT))
        query.restype = wintypes.BOOL
        size = size_of(str(path), None)
        if not size or size > 1024 * 1024:
            return None
        data = ctypes.create_string_buffer(size)
        address, length = ctypes.c_void_p(), wintypes.UINT()
        if not read(str(path), 0, size, data) or not query(data, "\\", ctypes.byref(address), ctypes.byref(length)):
            return None
        if length.value < 16:
            return None
        signature, _, high, low = ctypes.cast(address, ctypes.POINTER(wintypes.DWORD * 4)).contents
        if signature != 0xFEEF04BD:
            return None
        return ".".join(map(str, (high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF)))
    except (AttributeError, OSError):
        return None


def loader_version(game_dir):
    root = Path(game_dir, "MelonLoader")
    paths = [root / folder / "MelonLoader.dll" for folder in ("", "net35", "net472", "net6")]
    versions = {_file_version(path) for path in paths if path.is_file()}
    if len(versions) == 1:
        version, = versions
        if version:
            return version.removesuffix(".0")
    return None


def warnings(game_dir, manifest):
    values = getattr(manifest, "compatibility", {})
    messages = []
    for key, detector in (("game_builds", game_build), ("melonloader_versions", loader_version)):
        if key not in values:
            continue
        actual = detector(game_dir)
        allowed = values[key]
        normalize_version = lambda item: item.removesuffix(".0") if key == "melonloader_versions" and item.count(".") == 3 else item
        if actual is None:
            messages.append(f"{LABELS[key]} could not be detected. Publisher supports {', '.join(allowed)}.")
        elif normalize_version(actual) not in {normalize_version(item) for item in allowed}:
            messages.append(f"{LABELS[key]} {actual} is outside the publisher's supported versions ({', '.join(allowed)}).")
    return tuple(messages)
