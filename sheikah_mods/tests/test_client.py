from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ui_support import client, core, dialog_style, sample_plan, window


def run_inline(root, work, text, progress=None):
    root._sheikah_pending = True
    try:
        return work(progress) if progress else work()
    finally:
        root._sheikah_pending = False


class LaunchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = temp.name
        self.exe = Path(self.game, core.GAME_EXE)
        self.exe.write_bytes(b'test')
        self.args = [str(self.exe), '/dev_server_ip', core.HOST, '/dev_server_port', '1757']
        Path(self.game, 'Mods').mkdir()
        self.root = SimpleNamespace(_print=Mock(), v_exe=SimpleNamespace(get=lambda: str(self.exe)))
        self.plan = sample_plan(self.game)
        for p in (patch.object(client, '_app', return_value=self.root),
                  patch.object(client.runtime, 'game_running', return_value=False),
                  patch.object(client, '_switch_profile'),
                  patch.object(core, '_deferred', {})):
            p.start()
            self.addCleanup(p.stop)

    def test_skipped_startup_changes_do_not_prompt_again_at_launch(self):
        with patch.object(window, 'ask_updates', return_value=False) as ask:
            client._checked(self.root, self.plan, None, prompt=True, force=False)
            with patch.object(window, 'wait_for'), \
                    patch.object(window, 'run_waiting', return_value=self.plan):
                client.before_launch(self.args)
        ask.assert_called_once()
        self.assertFalse(self.root._sheikah_launching)

    def test_manual_check_can_reconsider_skipped_changes(self):
        core.defer(self.plan)
        with patch.object(window, 'ask_updates', return_value=False) as ask:
            client._checked(self.root, self.plan, None, prompt=True, force=True)
        ask.assert_called_once()

    def test_launch_waits_for_startup_update_before_its_check(self):
        self.root._sheikah_pending = True

        def wait(root, ready, text):
            self.assertFalse(ready())
            root._sheikah_pending = False
            self.assertTrue(ready())

        ready_plan = core.Plan(self.game, (), (), (), b'', {})
        with patch.object(window, 'wait_for', side_effect=wait) as waiting, \
                patch.object(window, 'run_waiting', return_value=ready_plan):
            client.before_launch(self.args)
        waiting.assert_called_once()

    def test_cancelling_check_stops_launch(self):
        with patch.object(window, 'wait_for'), \
                patch.object(window, 'run_waiting', side_effect=core.Cancelled('Launch cancelled')), \
                patch.object(client.messagebox, 'showerror'):
            with self.assertRaises(core.Cancelled):
                client.before_launch(self.args)
        self.assertFalse(self.root._sheikah_launching)

    def test_check_error_releases_busy_state_and_uses_installed_files(self):
        self.root._sheikah_pending = True
        client._checked(self.root, None, core.Error('offline'), True, False)
        self.assertFalse(self.root._sheikah_pending)
        with patch.object(window, 'wait_for'), \
                patch.object(window, 'run_waiting', side_effect=core.Error('offline')):
            client.before_launch(self.args)

    def test_game_path_change_discards_old_check(self):
        self.root.v_exe = SimpleNamespace(get=lambda: '')
        with patch.object(window, 'ask_updates') as ask:
            client._checked(self.root, self.plan, None, True, False)
        ask.assert_not_called()
        self.assertFalse(self.root._sheikah_pending)


class RoutedLaunchTests(unittest.TestCase):
    def setUp(self):
        import hashlib
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = temp.name
        self.mods = Path(self.game, 'Mods')
        self.mods.mkdir()
        self.exe = Path(self.game, core.GAME_EXE)
        self.exe.write_bytes(b'test')
        self.root = SimpleNamespace(_print=Mock())
        self.mod = core.Mod('A.dll', hashlib.sha256(b'sheikah').hexdigest().upper(), 'https://' + core.HOST + '/A.dll')
        self.sheikah = [str(self.exe), '/dev_server_ip', core.HOST]
        self.other = [str(self.exe), '/dev_server_ip', 'other.test', '/dev_server_port', '2000']
        core.install(core.inspect(self.game, (self.mod,)), lambda url, limit: b'sheikah', running=lambda _: False)
        for p in (patch.object(client, '_app', return_value=self.root),
                  patch.object(client.runtime, 'game_running', return_value=False),
                  patch.object(window, 'wait_for'),
                  patch.object(window, 'run_waiting', side_effect=run_inline),
                  patch.object(client.messagebox, 'showerror'),
                  patch.object(core, '_deferred', {})):
            p.start()
            self.addCleanup(p.stop)

    def test_other_server_and_main_menu_park_without_checking_for_updates(self):
        with patch.object(core.checks, 'get') as request, patch.object(window, 'ask_updates') as ask:
            client.before_launch(self.other)
            self.assertFalse((self.mods / 'A.dll').exists())
            self.assertTrue(client.switching.is_parked(self.game))
            client.before_launch([str(self.exe)])
        request.assert_not_called()
        ask.assert_not_called()

    def test_return_to_sheikah_restores_parked_mods(self):
        client.before_launch(self.other)
        with patch.object(core.checks, 'get', return_value=(self.mod,)):
            client.before_launch(self.sheikah)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah')
        self.assertFalse(client.switching.is_parked(self.game))

    def test_declined_update_still_restores_installed_sheikah_mods(self):
        import hashlib
        client.before_launch(self.other)
        newer = core.Mod('A.dll', hashlib.sha256(b'new').hexdigest().upper(), self.mod.url)
        with patch.object(core.checks, 'get', return_value=(newer,)), \
                patch.object(window, 'ask_updates', return_value=False) as ask:
            client.before_launch(self.sheikah)
            client.before_launch(self.sheikah)
        ask.assert_called_once()
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah')

    def test_accepted_parked_update_loads_new_version_for_sheikah(self):
        import hashlib
        client.before_launch(self.other)
        newer = core.Mod('A.dll', hashlib.sha256(b'new').hexdigest().upper(), self.mod.url)
        with patch.object(core.checks, 'get', return_value=(newer,)), \
                patch.object(window, 'ask_updates', return_value=True), \
                patch.object(core, 'fetch', return_value=b'new'):
            client.before_launch(self.sheikah)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'new')

    def test_offline_return_to_sheikah_restores_existing_mods(self):
        client.before_launch(self.other)
        with patch.object(core.checks, 'get', side_effect=core.Error('offline')):
            client.before_launch(self.sheikah)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah')

    def test_failed_switch_stops_launch_and_preserves_files(self):
        with patch.object(client.switching, 'switch', side_effect=core.Error('locked')):
            with self.assertRaisesRegex(core.Error, 'locked'):
                client.before_launch(self.other)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah')
        self.assertFalse(self.root._sheikah_launching)

    def test_running_game_blocks_new_launch_without_moving_files(self):
        with patch.object(client.runtime, 'game_running', return_value=True):
            with self.assertRaisesRegex(core.Error, 'Close A Township Tale'):
                client.before_launch(self.other)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah')


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        style = dialog_style()
        style.__enter__()
        self.addCleanup(style.__exit__, None, None, None)

    def test_long_review_scrolls_and_decision_buttons_stay_visible(self):
        plan = sample_plan(self.temp.name)
        plan.changes *= 15
        dialog = window.ReviewDialog(self.root, plan, launching=True)
        self.addCleanup(dialog.destroy)
        dialog.geometry('600x500')
        dialog.update()
        self.assertTrue(dialog.apply_button.winfo_viewable())
        self.assertTrue(dialog.skip_button.winfo_viewable())
        self.assertGreater(dialog.list.inner.winfo_reqheight(), dialog.list.canvas.winfo_height())
        self.assertEqual(dialog.apply_button.cget('text'), 'Apply & launch')

    def test_focused_window_has_only_fixed_list_actions(self):
        dialog = window.SheikahWindow(self.root, self.temp.name, lambda: None, lambda: None)
        self.addCleanup(dialog.destroy)
        plan = sample_plan(self.temp.name)
        dialog.show(plan)
        dialog.update()
        self.assertEqual(dialog.check_button.cget('text'), 'Check now')
        self.assertEqual(dialog.apply_button.cget('state'), 'normal')
        dialog.show(plan, busy=True)
        self.assertEqual(dialog.apply_button.cget('state'), 'disabled')
        self.assertEqual(dialog.check_button.cget('state'), 'disabled')

    def test_cancelled_wait_keeps_operation_pending(self):
        import threading
        release = threading.Event()
        self.addCleanup(release.set)

        def cancel():
            dialog = self.root._sheikah_wait_dialog
            self.root.tk.call(dialog.protocol('WM_DELETE_WINDOW'))

        self.root.after(70, cancel)
        with self.assertRaises(core.Cancelled):
            window.run_waiting(self.root, lambda: release.wait(3), 'Updating...')
        self.assertTrue(self.root._sheikah_pending)
        release.set()
        window.wait_for(self.root, lambda: not self.root._sheikah_pending, 'Finishing...')

    def test_launcher_button_opens_mod_viewer_and_reuses_it(self):
        exe = Path(self.temp.name, core.GAME_EXE)
        exe.write_bytes(b'test')
        self.root.v_exe = tk.StringVar(master=self.root, value=str(exe))
        self.root._sheikah_plan = sample_plan(self.temp.name)
        self.root._sheikah_pending = True
        row = tk.Frame(self.root)
        row.pack()
        # Different icon spacing must not make the addon button disappear.
        tk.Button(row, text='🧪  Mods').pack(side='left')
        with patch.object(client, '_startup'), patch.object(client, 'check') as check:
            client._ensure_button(self.root)
            self.root.update_idletasks()
            button = self.root._sheikah_button
            client._ensure_button(self.root)
            self.assertIs(self.root._sheikah_button, button)
            self.assertEqual(button.winfo_manager(), 'pack')
            button.invoke()
            viewer = self.root._sheikah_window
            self.assertEqual(viewer.title(), 'Sheikah Mods')
            self.assertEqual(viewer.check_button.cget('state'), 'disabled')
            self.assertGreater(len(viewer.list.inner.winfo_children()), 0)
            button.invoke()
            self.assertIs(self.root._sheikah_window, viewer)
        check.assert_called_once_with(self.root, self.temp.name, prompt=False)

    def test_launcher_button_falls_back_to_action_row(self):
        self.root.v_exe = tk.StringVar(master=self.root)
        row = tk.Frame(self.root)
        row.pack()
        self.root._action_btn = tk.Button(row, text='Join Server')
        self.root._action_btn.pack(side='left')
        with patch.object(client, '_startup'):
            client._ensure_button(self.root)
            self.root.update_idletasks()
        self.assertEqual(self.root._sheikah_button.master, row)

    def test_launcher_build_hook_works_without_theme_button_factory(self):
        class Launcher(tk.Toplevel):
            def _build_ui(self):
                self.v_exe = tk.StringVar(master=self)
                row = tk.Frame(self)
                row.pack()
                tk.Button(row, text='Mods').pack(side='left')

        module = SimpleNamespace(ClientLauncher=Launcher)
        with patch.dict(sys.modules, {'client.core.launcher_window': module}), \
                patch.object(client, '_app', return_value=None), \
                patch.object(client, '_startup'):
            client._install_ui()
            client._install_ui()
            launcher = Launcher(self.root)
            self.addCleanup(launcher.destroy)
            launcher._build_ui()
            launcher.update_idletasks()
            button = launcher._sheikah_button
            client._ensure_button(launcher)
            self.assertIs(launcher._sheikah_button, button)
            self.assertEqual(button.cget('text'), 'Sheikah Mods')


if __name__ == '__main__':
    unittest.main()
