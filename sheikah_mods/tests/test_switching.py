import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
core = importlib.import_module('sheikah_mods.core')
switching = importlib.import_module('sheikah_mods.switching')


class SwitchingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = temp.name
        self.mods = Path(self.game, 'Mods')
        self.mods.mkdir()
        self.holding = Path(self.game, core.STATE_DIR, switching.PARKED_DIR)
        self.data = {'A.dll': b'sheikah-a', 'B.dll': b'sheikah-b'}
        self.manifest = self.make_manifest(self.data)

    def make_manifest(self, data):
        return tuple(core.Mod(name, hashlib.sha256(value).hexdigest().upper(),
                              'https://' + core.HOST + '/' + name) for name, value in data.items())

    def fetch(self, url, limit):
        return self.data[url.rsplit('/', 1)[-1]]

    def install(self, mods=None):
        return core.install(core.inspect(self.game, self.manifest if mods is None else mods),
                            self.fetch, running=lambda _: False)

    def switch(self, sheikah, mods=None):
        return switching.switch(self.game, sheikah, mods, running=lambda _: False)

    def test_round_trip_moves_only_sheikah_mods(self):
        self.install()
        (self.mods / 'Personal.dll').write_bytes(b'my-mod')
        self.assertEqual(set(self.switch(False)), {'A.dll', 'B.dll'})
        self.assertTrue(switching.is_parked(self.game))
        self.assertFalse((self.mods / 'A.dll').exists())
        self.assertEqual((self.holding / 'A.dll').read_bytes(), b'sheikah-a')
        self.assertEqual((self.mods / 'Personal.dll').read_bytes(), b'my-mod')
        self.assertFalse(core.inspect(self.game, self.manifest).changes)
        self.switch(True)
        self.assertFalse(switching.is_parked(self.game))
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah-a')
        self.assertEqual((self.mods / 'Personal.dll').read_bytes(), b'my-mod')
        self.assertFalse((self.holding / 'A.dll').exists())

    def test_existing_matching_install_is_adopted_on_first_switch(self):
        for name, data in self.data.items():
            (self.mods / name).write_bytes(data)
        (self.mods / 'Personal.dll').write_bytes(b'loose')
        core.inspect(self.game, self.manifest)
        self.assertFalse(core._read_state(core._state_bytes(self.game)))
        self.switch(False)
        self.assertFalse((self.mods / 'A.dll').exists())
        self.assertEqual((self.mods / 'Personal.dll').read_bytes(), b'loose')
        self.switch(True)
        self.assertEqual((self.mods / 'B.dll').read_bytes(), b'sheikah-b')

    def test_different_unmanaged_mod_with_same_filename_stays_for_other_server(self):
        (self.mods / 'A.dll').write_bytes(b'personal-version')
        core.inspect(self.game, self.manifest)
        self.switch(False)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'personal-version')

    def test_original_local_copy_returns_for_other_servers(self):
        (self.mods / 'A.dll').write_bytes(b'personal-version')
        self.install()
        self.switch(False)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'personal-version')
        self.switch(True)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah-a')
        self.switch(False)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'personal-version')

    def test_edit_to_other_server_copy_is_preserved_across_switches(self):
        (self.mods / 'A.dll').write_bytes(b'personal-version')
        self.install()
        self.switch(False)
        (self.mods / 'A.dll').write_bytes(b'my-latest-edit')
        self.switch(True)
        self.switch(False)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'my-latest-edit')

    def test_new_conflicting_loose_copy_is_saved_and_restored(self):
        self.install()
        self.switch(False)
        (self.mods / 'A.dll').write_bytes(b'new-other-server-mod')
        self.switch(True)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah-a')
        self.switch(False)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'new-other-server-mod')

    def test_updates_while_parked_do_not_install_into_other_server_mods(self):
        (self.mods / 'A.dll').write_bytes(b'personal-version')
        self.install()
        self.switch(False)
        self.data['A.dll'] = b'new-sheikah-a'
        self.manifest = self.make_manifest(self.data)
        self.install()
        self.assertEqual((self.holding / 'A.dll').read_bytes(), b'new-sheikah-a')
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'personal-version')
        self.assertFalse((self.mods / 'B.dll').exists())
        self.switch(True)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'new-sheikah-a')

    def test_removal_while_parked_does_not_put_original_in_holding(self):
        (self.mods / 'A.dll').write_bytes(b'personal-version')
        self.install()
        self.switch(False)
        self.manifest = self.make_manifest({'B.dll': b'sheikah-b'})
        self.install()
        self.assertFalse((self.holding / 'A.dll').exists())
        self.switch(True)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'personal-version')
        self.assertEqual((self.mods / 'B.dll').read_bytes(), b'sheikah-b')

    def test_retired_edited_copy_stays_parked(self):
        self.install()
        self.switch(False)
        (self.holding / 'A.dll').write_bytes(b'custom-server-copy')
        self.manifest = self.make_manifest({'B.dll': b'sheikah-b'})
        self.install()
        self.switch(True)
        self.assertEqual((self.holding / 'A.dll').read_bytes(), b'custom-server-copy')
        self.assertFalse((self.mods / 'A.dll').exists())

    def test_missing_retired_copy_does_not_block_current_mods(self):
        (self.mods / 'A.dll').write_bytes(b'personal')
        self.install()
        self.switch(False)
        (self.holding / 'A.dll').unlink()
        current = self.make_manifest({'B.dll': b'sheikah-b'})
        core.inspect(self.game, current)
        self.switch(True)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'personal')
        self.assertEqual((self.mods / 'B.dll').read_bytes(), b'sheikah-b')
        self.assertNotIn('a.dll', core._read_state(core._state_bytes(self.game)))

    def test_unknown_cached_file_is_never_loaded_or_deleted(self):
        self.install()
        self.switch(False)
        (self.holding / 'Unknown.dll').write_bytes(b'personal')
        self.switch(True)
        self.assertEqual((self.holding / 'Unknown.dll').read_bytes(), b'personal')
        self.assertFalse((self.mods / 'Unknown.dll').exists())

    def test_failed_move_rolls_back_all_moves(self):
        self.install()
        state = core._state_bytes(self.game)
        replace = os.replace

        def fail(src, dst):
            if Path(src).name == 'B.dll' and Path(dst).parent == self.holding:
                raise PermissionError('locked')
            return replace(src, dst)

        with patch.object(switching.os, 'replace', side_effect=fail):
            with self.assertRaisesRegex(core.Error, 'Previous files were restored'):
                self.switch(False)
        self.assertFalse(switching.is_parked(self.game))
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah-a')
        self.assertEqual(core._state_bytes(self.game), state)

    def test_failed_metadata_save_restores_original_and_sheikah_files(self):
        (self.mods / 'A.dll').write_bytes(b'original')
        self.install()
        state = core._state_bytes(self.game)
        write = switching._write_bytes

        def fail(path, data):
            if path.name == 'switching.json':
                raise PermissionError('locked record')
            return write(path, data)

        with patch.object(switching, '_write_bytes', side_effect=fail):
            with self.assertRaises(core.Error):
                self.switch(False)
        self.assertFalse(switching.is_parked(self.game))
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah-a')
        self.assertEqual(core._state_bytes(self.game), state)

    def test_failed_rollback_retains_the_unmoved_copy(self):
        self.install()
        replace = os.replace

        def fail(src, dst):
            if Path(dst).parent == self.holding and Path(src).name == 'B.dll':
                raise PermissionError('locked move')
            if Path(src).parent == self.holding and Path(src).name == 'A.dll':
                raise PermissionError('locked restore')
            return replace(src, dst)

        with patch.object(switching.os, 'replace', side_effect=fail):
            with self.assertRaisesRegex(core.Error, 'Recovery copies'):
                self.switch(False)
        self.assertEqual((self.holding / 'A.dll').read_bytes(), b'sheikah-a')
        self.assertEqual((self.mods / 'B.dll').read_bytes(), b'sheikah-b')

    def test_game_running_blocks_switch(self):
        self.install()
        with self.assertRaises(core.Error):
            switching.switch(self.game, False, running=lambda _: True)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah-a')

    def test_changed_managed_file_is_not_silently_reclassified(self):
        self.install()
        (self.mods / 'A.dll').write_bytes(b'custom')
        with self.assertRaisesRegex(core.Error, 'changed outside'):
            self.switch(False)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'custom')

    def test_stale_install_plan_cannot_write_after_switch(self):
        self.install()
        plan = core.inspect(self.game, self.manifest)
        self.switch(False)
        with self.assertRaises(core.Error):
            core.install(plan, self.fetch, running=lambda _: False)

    def test_offline_switch_uses_saved_manifest(self):
        for name, data in self.data.items():
            (self.mods / name).write_bytes(data)
        core.inspect(self.game, self.manifest)
        with patch.object(core.checks, 'get', side_effect=core.Error('offline')):
            self.switch(False)
            self.switch(True)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'sheikah-a')

    def test_no_record_or_manifest_blocks_guessing_ownership(self):
        (self.mods / 'A.dll').write_bytes(b'unknown')
        with self.assertRaisesRegex(core.Error, 'Check the Sheikah mod list once'):
            self.switch(False)
        self.assertEqual((self.mods / 'A.dll').read_bytes(), b'unknown')


class RoutingTests(unittest.TestCase):
    def test_hostname_ip_alias_and_port(self):
        lookup = lambda host: {'203.0.113.7'} if host in (core.HOST, '203.0.113.7', 'alias.test') else {'203.0.113.8'}
        self.assertTrue(switching.is_sheikah((core.HOST.upper() + '.', 1757), lookup))
        self.assertTrue(switching.is_sheikah(('203.0.113.7', 1757), lookup))
        self.assertTrue(switching.is_sheikah(('alias.test', 1757), lookup))
        self.assertFalse(switching.is_sheikah((core.HOST, 1758), lookup))
        self.assertFalse(switching.is_sheikah(('other.test', 1757), lookup))
        self.assertFalse(switching.is_sheikah(None, lookup))
        with self.assertRaises(core.Error):
            switching.is_sheikah(('unknown.test', 1757), lambda _: set())

    def test_main_menu_local_and_explicit_server_arguments(self):
        self.assertIsNone(switching.target_from_args(['game.exe', '/force_offline']))
        self.assertEqual(switching.target_from_args(['game.exe', '/join_local_server']), ('127.0.0.1', 1757))
        self.assertEqual(switching.target_from_args(['game.exe', '/dev_server_ip', core.HOST, '/dev_server_port', '1758']),
                         (core.HOST, 1758))

    def test_invalid_address_does_not_guess_profile(self):
        for args in (['/dev_server_ip'], ['/dev_server_ip', ''],
                     ['/dev_server_ip', core.HOST, '/dev_server_port', 'broken']):
            with self.subTest(args=args), self.assertRaises(core.Error):
                switching.target_from_args(args)


if __name__ == '__main__':
    unittest.main()
