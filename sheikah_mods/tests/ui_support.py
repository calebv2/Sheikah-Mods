"""Launcher stand-ins for independent UI tests and a sample preview."""
from contextlib import contextmanager
import importlib
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from types import ModuleType
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
shared = ModuleType('tavern_shared')
shared.__path__ = []
theme = ModuleType('tavern_shared.theme')
theme._btn = Mock()
chrome = ModuleType('tavern_shared.window_chrome')
with patch.dict(sys.modules, {'tavern_shared': shared, 'tavern_shared.theme': theme,
                             'tavern_shared.window_chrome': chrome}), \
        patch.object(subprocess.Popen, '_sheikah_mods', True, create=True):
    client = importlib.import_module('sheikah_mods.client')
window = client.window
core = client.core


@contextmanager
def dialog_style():
    # Preview the original launcher's dark surfaces and amber accents.
    # The installed addon uses tavern_shared.theme directly.
    colors = dict(BG='#171513', SURF='#211e1a', BORDER='#40392f', PARCH='#eee3cf',
                  MUTED='#ada496', AMBER='#e5b96f', GREEN='#9ac59a', RED='#e49a8d')

    def button(parent, text, cmd, style='normal', **kwargs):
        return tk.Button(parent, text=text, command=cmd, relief='flat', bd=0,
                         bg=colors['AMBER'] if style == 'primary' else colors['SURF'],
                         fg=colors['BG'] if style == 'primary' else colors['PARCH'], **kwargs)

    colors.update(_btn=button, _mk_scrollbar=lambda parent, command: tk.Scrollbar(parent, command=command))
    with patch.dict(theme.__dict__, colors), patch.dict(chrome.__dict__, {
            '_start_hidden': lambda widget: widget.withdraw(),
            '_finish_dark_window': lambda widget: widget.deiconify()}):
        yield


def sample_plan(game_dir):
    mods = tuple(core.Mod(name, 'A' * 64, 'https://' + core.HOST + '/' + name) for name in (
        'AllPouchGatherer.dll', 'Ambrosia.dll', 'DyeingAPI.dll', 'FullSpectrumDyes.dll',
        'TownFarming.Client.dll', 'TownFishing.Client.dll'))
    changes = tuple(core.Change(mod.name, 'install', None, mod) for mod in mods[:4])
    changes += (core.Change(mods[4].name, 'update', 'B' * 64, mods[4]),)
    return core.Plan(game_dir, mods, changes, ('TownFishing.Client.dll',), b'', {})
