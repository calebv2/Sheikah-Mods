import hashlib
from pathlib import Path
import tempfile
import threading
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ui_support import client, core, dialog_style, make_root, sample_plan, window

ui_state = client.ui_state


class OfflineAndProgressTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = temp.name
        self.mods = Path(self.game, 'Mods')
        self.mods.mkdir()
        self.data = {'A.dll': b'a', 'B.dll': b'b', 'C.dll': b'c'}
        self.manifest = tuple(core.Mod(name, hashlib.sha256(data).hexdigest().upper(),
                                      'https://' + core.HOST + '/' + name) for name, data in self.data.items())

    def fetch(self, url, limit):
        return self.data[url.rsplit('/', 1)[-1]]

    def install(self, progress=None):
        return core.install(core.inspect(self.game, self.manifest), self.fetch, running=lambda _: False, progress=progress)

    def test_offline_ready_requires_matching_installed_hashes(self):
        self.install()
        with patch.object(core.checks, 'get', side_effect=core.Offline('offline')):
            plan = core.inspect(self.game, force=True)
        self.assertTrue(plan.offline)
        self.assertTrue(ui_state.usable(plan))
        self.assertIn('can still be used', ui_state.offline_message(plan))
        self.assertEqual(ui_state.action(plan), ('Retry', True))
        self.assertIn('Installed Sheikah mods usable', ui_state.next_launch(plan, True)[0])
        (self.mods / 'A.dll').write_bytes(b'changed')
        with patch.object(core.checks, 'get', side_effect=core.Offline('offline')):
            plan = core.inspect(self.game)
        self.assertFalse(ui_state.usable(plan))
        self.assertIn('Reconnect', ui_state.offline_message(plan))
        self.assertNotIn('Ready for Sheikah', ui_state.next_launch(plan, True)[0])

    def test_no_saved_list_never_claims_offline_ready(self):
        with patch.object(core.checks, 'get', side_effect=core.Offline('offline')):
            with self.assertRaises(core.Offline):
                core.inspect(self.game)
        self.assertIn('no saved mod list', ui_state.offline_message(None))

    def test_invalid_remote_list_does_not_silently_use_cached_data(self):
        self.install()
        with patch.object(core.checks, 'get', side_effect=core.Error('invalid manifest')):
            with self.assertRaisesRegex(core.Error, 'invalid manifest'):
                core.inspect(self.game)

    def test_download_counts_are_ordered_and_end_before_installation(self):
        events = []
        self.install(events.append)
        downloads = [event for event in events if event.startswith('Downloading')]
        self.assertEqual(downloads, ['Downloading %d of 3' % number for number in range(4)])
        self.assertLess(events.index('Downloading 3 of 3'), events.index('Installing verified mods...'))

    def test_bad_hash_never_reports_installation(self):
        events = []
        with self.assertRaises(core.Error):
            core.install(core.inspect(self.game, self.manifest), lambda url, limit: b'wrong',
                         running=lambda _: False, progress=events.append)
        self.assertNotIn('Installing verified mods...', events)
        self.assertFalse((self.mods / 'A.dll').exists())

    def test_switching_reports_file_counts(self):
        self.install()
        events = []
        client.switching.switch(self.game, False, running=lambda _: False, progress=events.append)
        self.assertEqual(events, ['Switching mods %d of 3' % number for number in range(4)])

    def test_fetch_failures_are_distinguished_from_invalid_manifests(self):
        opener = Mock()
        opener.open.side_effect = OSError('network unavailable')
        with patch.object(core, 'build_opener', return_value=opener):
            with self.assertRaises(core.Offline):
                core.fetch(core.MANIFEST_URL, 100)


class UsabilityWindowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = temp.name
        Path(self.game, 'Mods').mkdir()
        self.exe = Path(self.game, core.GAME_EXE)
        self.exe.write_bytes(b'test')
        make_root(self)
        style = dialog_style()
        style.__enter__()
        self.addCleanup(style.__exit__, None, None, None)
        self.root.v_exe = tk.StringVar(master=self.root, value=str(self.exe))
        self.root.v_ip = tk.StringVar(master=self.root, value=core.HOST)
        self.root.v_port = tk.StringVar(master=self.root, value='1757')
        self.root._print = Mock()
        self.plan = sample_plan(self.game)

    def ready_plan(self):
        return core.Plan(self.game, self.plan.mods, (), tuple(mod.name for mod in self.plan.mods), b'', {})

    def viewer(self):
        opened = window.SheikahWindow(self.root, self.game, Mock(), lambda: client._apply_click(self.root))
        self.root._sheikah_window = opened
        self.addCleanup(opened.destroy)
        return opened

    def add_status(self):
        row = tk.Frame(self.root)
        row.pack()
        self.root._action_btn = tk.Button(row, text='Join Server')
        self.root._action_btn.pack()
        client._ensure_status(self.root)

    def test_one_primary_button_reflects_install_update_ready_and_retry(self):
        opened = self.viewer()
        installs = tuple(change for change in self.plan.changes if change.action == 'install')
        initial = core.Plan(self.game, self.plan.mods[:4], installs, (), b'', {})
        opened.show(initial)
        self.assertEqual(opened.apply_button.cget('text'), 'Install mods')
        opened.show(self.plan)
        self.assertEqual(opened.apply_button.cget('text'), 'Update mods')
        opened.show(self.ready_plan())
        self.assertEqual(opened.apply_button.cget('text'), 'All up to date')
        self.assertEqual(opened.apply_button.cget('state'), 'disabled')
        opened.show(self.plan, issue=core.Error('failed'))
        self.assertEqual(opened.apply_button.cget('text'), 'Retry')
        self.assertEqual(opened.apply_button.cget('state'), 'normal')
        opened.show(self.plan, busy=True)
        self.assertEqual(opened.apply_button.cget('state'), 'disabled')

    def test_status_tracks_selected_server_without_blocking_on_dns(self):
        self.root._sheikah_plan = self.ready_plan()
        self.add_status()
        self.assertIn('Ready for Sheikah', self.root._sheikah_next_status.cget('text'))
        self.root.v_port.set('2000')
        self.root.v_ip.set('other.example')
        self.assertIn('will be parked', self.root._sheikah_next_status.cget('text'))
        self.root.v_port.set('invalid')
        self.assertIn('Check the server address', self.root._sheikah_next_status.cget('text'))

    def test_late_dns_reply_cannot_replace_new_destination_status(self):
        self.root._sheikah_plan = self.ready_plan()
        self.add_status()
        pending = []
        with patch.object(window, 'background', side_effect=lambda root, work, done: pending.append(done)):
            self.root.v_ip.set('other.example')
            elapsed = tk.BooleanVar(master=self.root)
            self.root.after(300, lambda: elapsed.set(True))
            self.root.wait_variable(elapsed)
        self.assertEqual(len(pending), 1)
        self.root.v_ip.set(core.HOST)
        pending[0](False, None)
        self.assertIn('Ready for Sheikah', self.root._sheikah_next_status.cget('text'))

    def test_progress_callbacks_run_on_main_thread(self):
        called = []
        main = threading.get_ident()

        def work(emit):
            self.assertNotEqual(threading.get_ident(), main)
            emit('Downloading 1 of 2')
            emit('Downloading 2 of 2')
            return 'done'

        result = window.run_waiting(self.root, work, 'Downloading...',
                                    progress=lambda message: called.append((message, threading.get_ident())))
        self.assertEqual(result, 'done')
        self.assertEqual(called[-1], ('Downloading 2 of 2', main))
        self.assertFalse(self.root._sheikah_pending)

    def test_retry_after_failed_check_rechecks_instead_of_installing(self):
        opened = self.viewer()
        self.root._sheikah_plan = self.plan
        self.root._sheikah_issue = core.Offline('offline')
        self.root._sheikah_retry = 'check'
        opened.show(self.plan, issue=self.root._sheikah_issue)
        with patch.object(client, 'check') as check, patch.object(client, '_install') as install:
            opened.apply_button.invoke()
        check.assert_called_once_with(self.root, self.game, force=True, prompt=False)
        install.assert_not_called()

    def test_retry_after_failed_install_retries_approved_plan(self):
        opened = self.viewer()
        self.root._sheikah_plan = self.plan
        self.root._sheikah_issue = core.Error('locked')
        self.root._sheikah_retry = 'install'
        opened.show(self.plan, issue=self.root._sheikah_issue)
        with patch.object(client, '_install') as install:
            opened.apply_button.invoke()
        install.assert_called_once_with(self.root, self.plan)

    def test_friendly_error_releases_busy_state_and_exposes_retry(self):
        self.viewer()
        self.root._sheikah_pending = True
        with patch.object(client, '_app', return_value=self.root):
            client._checked(self.root, None, core.Offline('private backend details'), True, False)
        self.assertFalse(self.root._sheikah_pending)
        opened = self.root._sheikah_window
        self.assertEqual(opened.apply_button.cget('text'), 'Retry')
        self.assertIn('Reconnect', opened.status.cget('text'))
        self.assertNotIn('private backend details', opened.status.cget('text'))

    def test_changing_game_path_clears_old_readiness(self):
        self.root._sheikah_plan = self.ready_plan()
        self.add_status()
        self.root.v_exe.set(str(Path(self.game, 'missing.exe')))
        self.assertIsNone(self.root._sheikah_plan)
        self.assertNotIn('Ready for Sheikah', self.root._sheikah_next_status.cget('text'))


if __name__ == '__main__':
    unittest.main()
