"""Game-process detection for safe updates."""
import ctypes
from ctypes import wintypes
import os

GAME_EXE = "A Township Tale.exe"

def same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = (ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    return kernel32


def _process_path(kernel32, pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def game_processes(exe_name=GAME_EXE):
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError("process list unavailable")
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == exe_name.lower():
                found.append((entry.th32ProcessID, _process_path(kernel32, entry.th32ProcessID)))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def game_running(game_dir, exe_name=GAME_EXE):
    try:
        processes = game_processes(exe_name)
    except (OSError, AttributeError, ValueError) as error:
        from .core import Error
        raise Error("Could not confirm that the game is closed. Try again before updating.") from error
    for pid, path in processes:
        if path is None or same_path(os.path.dirname(path), game_dir):
            return True
    return False
