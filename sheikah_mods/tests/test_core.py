import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
core = importlib.import_module('sheikah_mods.core')


def sha(data):
    return hashlib.sha256(data).hexdigest().upper()


class CoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = temp.name
        self.mods = Path(self.game, 'Mods')
        self.mods.mkdir()
        self.data = {'A.dll': b'new-a', 'B.dll': b'new-b'}
        self.manifest = self.make_manifest(self.data)
        patcher = patch.object(core, '_deferred', {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_manifest(self, files):
        return tuple(core.Mod(name, sha(data), 'https://' + core.HOST + '/' + name) for name, data in files.items())

    def fetch(self, url, limit):
        return self.data[url.rsplit('/', 1)[-1]]

    def inspect(self, mods=None):
        return core.inspect(self.game, self.manifest if mods is None else mods)

    def install(self, plan=None, fetcher=None):
        return core.install(plan or self.inspect(), fetcher or self.fetch, running=lambda _: False)

    def test_install_full_list_and_detect_no_changes(self):
        (self.mods / 'Unrelated.dll').write_bytes(b'personal')
        result = self.install()
        self.assertFalse(result.changes)
        self.assertEqual(len(result.ready), 2)
        self.assertEqual((self.mods / 'Unrelated.dll').read_bytes(), b'personal')

    def test_changed_loose_copy_is_backed_up_and_restored_when_unlisted(self):
        (self.mods / 'A.dll').write_bytes(b'my-original')
        plan = self.inspect()
        self.assertEqual(plan.changes[0].action, 'replace')
        self.install(plan)
        state = json.loads(core.state_path(self.game).read_text())['files']
        backup = Path(self.game, core.STATE_DIR, state['a.dll']['original'])
        self.assertEqual(backup.read_bytes(), b'my-original')
        plan = self.inspect(self.make_manifest({'B.dll': b'new-b'}))
        self.assertEqual(plan.changes[0].action, 'restore')
        self.install(plan)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'my-original')

    def test_unlisted_owned_file_is_removed_but_user_edit_is_preserved(self):
        self.install()
        (self.mods / 'A.dll').write_bytes(b'custom')
        plan = self.inspect(())
        self.assertEqual({c.action for c in plan.changes}, {'keep', 'remove'})
        self.install(plan)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'custom')
        self.assertFalse((self.mods / 'B.dll').exists())
        self.assertFalse(self.inspect(()).changes)

    def test_matching_preexisting_mod_is_reused_without_claiming_ownership(self):
        (self.mods / 'A.dll').write_bytes(b'new-a')
        self.install()
        state = json.loads(core.state_path(self.game).read_text())['files']
        self.assertNotIn('a.dll', state)
        self.install(self.inspect(()))
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'new-a')

    def test_bad_hash_changes_nothing(self):
        (self.mods / 'A.dll').write_bytes(b'old')
        with self.assertRaisesRegex(core.Error, 'SHA-256'):
            self.install(fetcher=lambda url, limit: b'bad')
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'old')
        self.assertFalse((self.mods / 'B.dll').exists())

    def test_edit_during_download_is_preserved(self):
        def fetch(url, limit):
            (self.mods / 'A.dll').write_bytes(b'my-edit')
            return self.fetch(url, limit)
        with self.assertRaisesRegex(core.Error, 'mods changed'):
            self.install(fetcher=fetch)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'my-edit')

    def test_game_running_blocks_install(self):
        with self.assertRaisesRegex(core.Error, 'Close A Township Tale'):
            core.install(self.inspect(), self.fetch, running=lambda _: True)
        self.assertFalse((self.mods / 'A.dll').exists())

    def test_failed_metadata_save_rolls_back_all_files(self):
        (self.mods / 'A.dll').write_bytes(b'old')
        with patch.object(core, '_save_state', side_effect=PermissionError('locked')):
            with self.assertRaisesRegex(core.Error, 'Previous files were restored'):
                self.install()
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'old')
        self.assertFalse((self.mods / 'B.dll').exists())

    def test_failed_removal_restores_file_and_ownership_state(self):
        self.install()
        state = core.state_path(self.game).read_bytes()
        plan = self.inspect(self.make_manifest({'B.dll': b'new-b'}))
        with patch.object(core, '_save_state', side_effect=PermissionError('locked')):
            with self.assertRaises(core.Error):
                self.install(plan)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'new-a')
        self.assertEqual(core.state_path(self.game).read_bytes(), state)

    def test_changed_saved_original_stops_restore_before_any_removal(self):
        (self.mods / 'A.dll').write_bytes(b'original')
        self.install()
        state = json.loads(core.state_path(self.game).read_text())['files']
        Path(self.game, core.STATE_DIR, state['a.dll']['original']).write_bytes(b'changed backup')
        with self.assertRaisesRegex(core.Error, 'saved original copy'):
            self.install(self.inspect(self.make_manifest({'B.dll': b'new-b'})))
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'new-a')

    def test_total_download_budget_prevents_installation(self):
        with patch.object(core, 'MAX_TOTAL', 2):
            with self.assertRaises(core.Error):
                self.install()
        self.assertFalse((self.mods / 'A.dll').exists())
        self.assertFalse((self.mods / 'B.dll').exists())

    def test_failed_second_placement_rolls_back_every_file(self):
        (self.mods / 'A.dll').write_bytes(b'old-a')
        (self.mods / 'B.dll').write_bytes(b'old-b')
        replace = os.replace

        def fail(src, dst):
            if Path(src).name == 'B.dll' and Path(dst).parent == self.mods:
                if Path(src).parent.name != 'previous':
                    raise PermissionError('locked')
            return replace(src, dst)

        with patch.object(core.os, 'replace', side_effect=fail):
            with self.assertRaises(core.Error):
                self.install()
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'old-a')
        self.assertEqual((self.mods / 'B.dll').read_bytes(), b'old-b')

    def test_failed_rollback_keeps_recovery_files(self):
        (self.mods / 'A.dll').write_bytes(b'old')
        replace = os.replace

        def fail(src, dst):
            if Path(src).parent.name == 'previous':
                raise PermissionError('locked rollback')
            return replace(src, dst)

        with patch.object(core, '_save_state', side_effect=PermissionError('locked state')), \
                patch.object(core.os, 'replace', side_effect=fail):
            with self.assertRaisesRegex(core.Error, 'Recovery copies'):
                self.install()
        backups = list(Path(self.game, core.STATE_DIR, 'transactions').rglob('previous/A.dll'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'old')

    def test_invalid_saved_state_is_not_treated_as_empty_ownership(self):
        core.state_path(self.game).parent.mkdir()
        core.state_path(self.game).write_text('{broken')
        with self.assertRaisesRegex(core.Error, 'saved file list is invalid'):
            self.inspect()

    def test_skip_remembered_until_changes_differ(self):
        plan = self.inspect()
        core.defer(plan)
        self.assertTrue(core.deferred(self.inspect()))
        self.assertFalse(core.deferred(self.inspect(self.make_manifest({'A.dll': b'newer'}))))

    def test_cached_manifest_rechecks_local_files(self):
        with patch.object(core, 'checks', core.ManifestChecks()), patch.object(core, 'read_manifest', return_value=self.manifest) as read:
            core.inspect(self.game)
            (self.mods / 'A.dll').write_bytes(b'new-a')
            plan = core.inspect(self.game)
        read.assert_called_once()
        self.assertIn('A.dll', plan.ready)

    def test_concurrent_checks_share_request(self):
        entered, release = threading.Event(), threading.Event()

        def read():
            entered.set()
            self.assertTrue(release.wait(3))
            return self.manifest

        cache = core.ManifestChecks()
        with patch.object(core, 'read_manifest', side_effect=read) as fetch:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(cache.get)
                try:
                    self.assertTrue(entered.wait(3))
                    second = pool.submit(cache.get)
                finally:
                    release.set()
                self.assertEqual(first.result(3), second.result(3))
        fetch.assert_called_once()

    def test_manual_and_expired_checks_fetch_again(self):
        cache = core.ManifestChecks()
        with patch.object(core.time, 'monotonic', return_value=100) as clock, \
                patch.object(core, 'read_manifest', return_value=self.manifest) as read:
            cache.get()
            cache.get()
            self.assertEqual(read.call_count, 1)
            cache.get(force=True)
            clock.return_value = 221
            cache.get()
            self.assertEqual(read.call_count, 3)

    def test_failed_checks_are_shared_briefly_and_then_retried(self):
        cache = core.ManifestChecks()
        with patch.object(core.time, 'monotonic', return_value=100) as clock, \
                patch.object(core, 'read_manifest', side_effect=core.Error('offline')) as read:
            for _ in range(2):
                with self.assertRaises(core.Error):
                    cache.get()
            self.assertEqual(read.call_count, 1)
            clock.return_value = 111
            with self.assertRaises(core.Error):
                cache.get()
            self.assertEqual(read.call_count, 2)

    def test_manifest_url_is_fixed_and_names_are_validated(self):
        entries = [{'filename': 'A.dll', 'sha256': sha(b'new'), 'url': '/A.dll'}]

        def fetch(url, limit):
            self.assertTrue(url.startswith(core.MANIFEST_URL + '?'))
            return json.dumps({'files': entries}).encode()

        self.assertEqual(core.read_manifest(fetch)[0].url, 'https://' + core.HOST + '/A.dll')
        for bad in ('../Bad.dll', 'C:Bad.dll', 'CON.dll', 'Backend.Server.dll', 'CircuitsVoiceChat.dll'):
            entries[0]['filename'] = bad
            with self.subTest(name=bad), self.assertRaises(core.Error):
                core.read_manifest(fetch)

    def test_external_host_and_empty_manifest_are_refused(self):
        with self.assertRaises(core.Error):
            core.safe_url('https://other.example/A.dll')
        with self.assertRaises(core.Error):
            core.safe_url('http://' + core.HOST + '/A.dll')
        with self.assertRaises(core.Error):
            core.read_manifest(lambda url, limit: b'{"files":[]}')


if __name__ == '__main__':
    unittest.main()
