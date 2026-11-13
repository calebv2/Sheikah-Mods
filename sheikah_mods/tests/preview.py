"""Show sample Sheikah Mods windows without contacting the server or the game."""
import tempfile
import tkinter as tk
import sys
from pathlib import Path
from unittest.mock import patch

from ui_support import client, core, dialog_style, sample_plan, theme, window


if __name__ == '__main__':
    root = tk.Tk()
    root.withdraw()
    with tempfile.TemporaryDirectory() as game, dialog_style():
        plan = sample_plan(game)
        if '--launcher' in sys.argv:
            exe = Path(game, core.GAME_EXE)
            exe.write_bytes(b'preview')
            root.v_exe = tk.StringVar(master=root, value=str(exe))
            root._sheikah_plan = plan
            root.configure(bg=theme.BG)
            root.title('Sheikah Mods — launcher button preview')
            root.geometry('740x190')
            tk.Label(root, text='Sheikah Mods launcher button', bg=theme.BG, fg=theme.PARCH,
                     font=('Georgia', 18, 'bold')).pack(anchor='w', padx=24, pady=(24, 8))
            tk.Label(root, text='Open the mod list and available updates. Sample data only.', bg=theme.BG,
                     fg=theme.MUTED, font=('Segoe UI', 10)).pack(anchor='w', padx=24)
            row = tk.Frame(root, bg=theme.BG)
            row.pack(fill='x', padx=24, pady=18)
            theme._btn(row, '🧪 Mods', lambda: None, padx=12, pady=5).pack(side='left')
            with patch.object(client, '_startup'), patch.object(client, 'check'):
                client._ensure_button(root)
                root.deiconify()
                root.mainloop()
        else:
            dialog = window.SheikahWindow(root, game, lambda: None, lambda: None)
            dialog.title('Sheikah Mods — sample preview')
            dialog.show(plan, 'Sample preview — no game files are changed.')
            dialog.wait_window()
    try:
        root.destroy()
    except tk.TclError:
        pass
