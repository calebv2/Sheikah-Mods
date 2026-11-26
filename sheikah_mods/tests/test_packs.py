import hashlib
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ui_support import client, core, dialog_style, make_root, window
from sheikah_mods import compatibility, history, packs, switching
from test_client import run_inline


class PackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = temp.name
        self.live = Path(self.game, "Mods")
        self.live.mkdir()
        self.policy = Path(self.game, "pack-policy.json")
        locked = patch.object(packs, "POLICY_PATH", self.policy)
        locked.start()
        self.addCleanup(locked.stop)
        self.first = packs.add(self.game, "First", "first.test", 1757, "https://first.test/mods/manifest.json")
        self.second = packs.add(self.game, "Second", "second.test", 1758, "https://second.test/mods/manifest.json")

    def install(self, pack, content):
        url = "https://" + (pack or packs.SHEIKAH).host + "/Shared.dll"
        mod = core.Mod("Shared.dll", hashlib.sha256(content).hexdigest().upper(), url)
        plan = core.inspect(self.game, (mod,), pack=pack)
        return core.install(plan, lambda url, limit: content, running=lambda _: False)

    def install_release(self, pack, release, files, requirements=None):
        data = {"release": release, "compatibility": requirements or {},
                "files": [{"filename": name, "sha256": hashlib.sha256(content).hexdigest(),
                           "url": name} for name, content in files.items()]}
        mods = core.parse_manifest(json.dumps(data).encode(), pack.manifest_url)
        plan = core.inspect(self.game, mods, pack=pack)
        return core.install(plan, lambda url, limit: files[url.rsplit("/", 1)[-1]], running=lambda _: False)

    def switch(self, target, **kwargs):
        return switching.switch_pack(self.game, target, running=lambda _: False, **kwargs)

    def test_overlapping_filenames_round_trip_and_restore_regular_copy(self):
        original = self.live / "Shared.dll"
        original.write_bytes(b"player version")
        self.install(None, b"sheikah version")
        self.install(self.first, b"first version")
        self.install(self.second, b"second version")
        self.assertEqual(original.read_bytes(), b"sheikah version")

        for pack, content in ((self.first, b"first version"), (self.second, b"second version"),
                              (self.first, b"first version"), (packs.SHEIKAH, b"sheikah version")):
            self.switch(pack.id)
            self.assertEqual(original.read_bytes(), content)

        self.switch(packs.REGULAR_ID)
        self.assertEqual(original.read_bytes(), b"player version")
        self.assertEqual((switching.storage_folder(self.game, self.second) / "Shared.dll").read_bytes(),
                         b"second version")

    def test_cross_pack_switch_rolls_back_both_packs_on_failed_activation(self):
        self.install(None, b"sheikah")
        self.install(self.first, b"first")
        self.install(self.second, b"second")
        self.switch(self.first.id)
        outgoing_state = core.state_path(self.game, self.first).read_bytes()
        incoming_state = core.state_path(self.game, self.second).read_bytes()
        incoming = switching.storage_folder(self.game, self.second) / "Shared.dll"
        replace = os.replace

        def fail_incoming(src, dst):
            if src == incoming:
                raise OSError("simulated lock")
            replace(src, dst)

        with patch.object(switching.os, "replace", side_effect=fail_incoming):
            with self.assertRaisesRegex(core.Error, "Previous files were restored"):
                self.switch(self.second.id, manual=True)

        self.assertEqual(switching.selection(self.game), (self.first.id, False))
        self.assertEqual((self.live / "Shared.dll").read_bytes(), b"first")
        self.assertEqual(incoming.read_bytes(), b"second")
        self.assertEqual(core.state_path(self.game, self.first).read_bytes(), outgoing_state)
        self.assertEqual(core.state_path(self.game, self.second).read_bytes(), incoming_state)

    def test_switch_respects_removal_of_a_regular_copy_while_pack_is_inactive(self):
        live_file = self.live / "Shared.dll"
        live_file.write_bytes(b"regular")
        self.install(None, b"sheikah")
        self.install(self.first, b"first")
        self.switch(self.first.id)
        self.switch(packs.REGULAR_ID)
        self.assertEqual(live_file.read_bytes(), b"regular")
        live_file.unlink()
        self.switch(self.first.id)
        self.switch(packs.REGULAR_ID)
        self.assertFalse(live_file.exists())

    def test_manual_mode_persists_and_lock_restores_server_routing(self):
        self.install(self.first, b"first")
        self.switch(self.first.id, manual=True)
        self.assertEqual(switching.route(self.game, self.second.target), self.first.id)
        self.assertEqual(switching.route(self.game, None), self.first.id)
        switching.resume_automatic(self.game)
        self.assertEqual(switching.route(self.game, self.second.target), self.second.id)
        self.assertEqual(switching.route(self.game, None), packs.REGULAR_ID)
        self.switch(self.first.id, manual=True)

        self.policy.write_text('{"sheikah_only":true}', encoding="utf-8")
        self.assertEqual(packs.catalog(self.game), (packs.SHEIKAH,))
        self.assertEqual(switching.route(self.game, None), packs.REGULAR_ID)
        self.assertEqual(switching.route(self.game, packs.SHEIKAH.target), packs.SHEIKAH_ID)
        with self.assertRaises(core.Error):
            self.switch(self.first.id)
        with self.assertRaises(core.Error):
            self.switch(packs.SHEIKAH_ID, manual=True)
        self.switch(packs.REGULAR_ID)
        self.assertFalse((self.live / "Shared.dll").exists())
        self.assertEqual(switching.selection(self.game), (packs.REGULAR_ID, False))

    def test_reads_legacy_parked_record_and_reuses_pack_storage(self):
        plan = self.install(None, b"legacy")
        self.switch(packs.REGULAR_ID)
        switching._switch_path(self.game).write_bytes(b'{"version":1,"parked":true}')
        self.switch(packs.SHEIKAH_ID)
        self.assertEqual((self.live / "Shared.dll").read_bytes(), b"legacy")
        self.assertEqual(switching.cached_manifest(self.game), plan.mods)

    def test_generated_manifest_can_be_hosted_and_keeps_downloads_on_origin(self):
        folder = Path(self.game, "publish")
        folder.mkdir()
        file = folder / "My Mod #1.dll"
        file.write_bytes(b"client dll")
        manifest = packs.create_manifest(folder)
        data = manifest.read_bytes()
        uri = self.first.manifest_url + "?revision=4"

        def fetch(url, limit):
            self.assertIn("?revision=4&_sheikah_check=", url)
            return data

        mod, = core.read_manifest(fetcher=fetch, manifest_url=uri)
        self.assertEqual(mod.name, file.name)
        self.assertEqual(mod.sha256, core.digest(file))
        self.assertEqual(mod.url, "https://first.test/mods/My%20Mod%20%231.dll")
        changed = json.loads(data)
        changed["files"][0]["url"] = "https://different.test/bad.dll"
        with self.assertRaises(core.Error):
            core.parse_manifest(json.dumps(changed).encode(), uri)
        with self.assertRaises(FileExistsError):
            packs.create_manifest(folder)

    def test_share_links_round_trip_and_reimport_preserves_identity(self):
        self.assertEqual(packs.import_shared(self.game, packs.parse_link(packs.share_link(self.first))), self.first)
        self.assertEqual(packs.import_shared(self.game, packs.parse_link(packs.share_link(packs.SHEIKAH))), packs.SHEIKAH)
        original = packs.add(self.game, "New & different", "2001:db8::1", 2000,
                             "https://new.test/manifest.json?variant=A%20B&round=2")
        link = packs.share_link(original)
        parsed = packs.parse_link(link)
        self.assertEqual(parsed.name, original.name)
        self.assertEqual(parsed.target, original.target)
        self.assertEqual(parsed.manifest_url, original.manifest_url)
        with self.assertRaises(core.Error):
            packs.parse_link(link + "&name=override")

    def test_edit_keeps_storage_and_rejects_stale_install_plan(self):
        self.install(self.first, b"first")
        plan = core.inspect(self.game, switching.cached_manifest(self.game, self.first), pack=self.first)
        changed = packs.edit(self.game, self.first, "Renamed", "new.test", 2222, "https://new.test/manifest.json")
        self.assertEqual(changed.id, self.first.id)
        self.assertEqual(switching.route(self.game, changed.target), changed.id)
        self.assertEqual(switching.cached_manifest(self.game, changed), ())
        self.assertEqual((switching.storage_folder(self.game, changed) / "Shared.dll").read_bytes(), b"first")
        with self.assertRaisesRegex(core.Error, "registered pack changed"):
            core.install(plan, running=lambda _: False)

    def test_remove_active_pack_restores_regular_files_and_readding_reuses_downloads(self):
        live = self.live / "Shared.dll"
        live.write_bytes(b"regular")
        self.install(None, b"sheikah")
        self.install(self.first, b"first")
        self.switch(self.first.id, manual=True)
        packs.remove(self.game, self.first, running=lambda _: False)
        self.assertEqual(live.read_bytes(), b"regular")
        self.assertEqual(switching.selection(self.game), (packs.REGULAR_ID, False))
        self.assertNotIn(self.first, packs.catalog(self.game))
        back = packs.add(self.game, self.first.name, *self.first.target, self.first.manifest_url)
        self.assertEqual(back, self.first)
        self.assertEqual((switching.storage_folder(self.game, back) / live.name).read_bytes(), b"first")

    def test_failed_removal_preserves_registration_and_active_manual_pack(self):
        self.install(self.first, b"first")
        self.switch(self.first.id, manual=True)
        with patch.object(packs, "_save_catalog", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                packs.remove(self.game, self.first, running=lambda _: False)
        self.assertEqual(switching.selection(self.game), (self.first.id, True))
        self.assertIn(self.first, packs.catalog(self.game))
        self.assertEqual((self.live / "Shared.dll").read_bytes(), b"first")

    def test_launch_preview_flags_regular_conflicts_and_changes_nothing(self):
        live = self.live / "Shared.dll"
        live.write_bytes(b"regular")
        (self.live / "Unrelated.dll").write_bytes(b"unrelated")
        self.install(None, b"sheikah")
        self.install(self.first, b"first")
        before = switching.selection(self.game)
        info = switching.launch_files(self.game, self.first.id, self.first)
        self.assertEqual(info.pack, ("Shared.dll",))
        self.assertEqual(info.regular, ("Unrelated.dll",))
        self.assertEqual(info.conflicts, ("Shared.dll",))
        text = "\n".join(client._launch_details(self.game, self.first.target))
        self.assertIn("Join Server · First", text)
        self.assertIn("Main Menu · Regular mods", text)
        self.assertEqual(switching.selection(self.game), before)
        self.assertEqual(live.read_bytes(), b"sheikah")

    def test_resolved_endpoint_and_alternative_hostname_match_registered_pack(self):
        address = "203.0.113.91"
        lookup = lambda host: {address} if host in (self.second.host, "alias.test") else set()
        self.assertEqual(switching.route(self.game, (address, self.second.port), lookup=lookup), self.second.id)
        self.assertEqual(switching.route(self.game, ("alias.test", self.second.port), lookup=lookup), self.second.id)
        extra = packs.add(self.game, "Same DNS", "alias.test", self.second.port, "https://alias.test/manifest.json")
        with self.assertRaisesRegex(core.Error, "Multiple mod packs"):
            switching.route(self.game, (address, self.second.port), lookup=lookup)
        self.assertEqual(switching.route(self.game, (address, self.second.port), extra.target, lookup), extra.id)

    def test_restore_previous_version_offline_and_pin_survives_switching(self):
        self.install_release(self.first, "1.0", {"Shared.dll": b"one", "Old.dll": b"old"})
        self.install_release(self.first, "2.0", {"Shared.dll": b"two", "New.dll": b"new"})
        self.switch(self.first.id)
        plan = core.restore_plan(self.game, self.first)
        self.assertEqual(plan.mods.release, "1.0")
        with patch.object(core, "read_manifest", side_effect=AssertionError("unexpected network read")):
            restored = core.install(plan, restoring=True, running=lambda _: False)
            self.assertTrue(restored.pinned)
            self.assertEqual((self.live / "Shared.dll").read_bytes(), b"one")
            self.assertTrue((self.live / "Old.dll").exists())
            self.assertFalse((self.live / "New.dll").exists())
            self.switch(packs.REGULAR_ID)
            self.switch(self.first.id)
            pinned = core.inspect(self.game, force=True, pack=self.first)
        self.assertTrue(pinned.pinned)
        self.assertEqual(pinned.mods.release, "1.0")
        self.assertFalse(pinned.changes)
        self.assertEqual(core.restore_plan(self.game, self.first).mods.release, "2.0")
        history.resume(self.game, self.first)
        with patch.object(core, "read_manifest", side_effect=core.Offline("offline")):
            self.assertFalse(core.inspect(self.game, pack=self.first).pinned)

    def test_damaged_restore_archive_does_not_mutate_current_version_or_pin_it(self):
        self.install_release(self.first, "1", {"Shared.dll": b"one"})
        self.install_release(self.first, "2", {"Shared.dll": b"two"})
        self.switch(self.first.id)
        plan = core.restore_plan(self.game, self.first)
        saved = history.read(core._state_bytes(self.game, self.first))
        (history.directory(self.game, self.first) / saved.previous / "Shared.dll").write_bytes(b"tampered")
        with self.assertRaisesRegex(core.Error, "SHA-256"):
            core.install(plan, restoring=True, running=lambda _: False)
        self.assertEqual((self.live / "Shared.dll").read_bytes(), b"two")
        self.assertEqual(history.available(self.game, self.first), (True, False))

    def test_compatibility_metadata_round_trips_and_detects_mismatch(self):
        (Path(self.game) / "version").write_text("wrong-build\n", encoding="utf-8")
        requirements = {"game_builds": ["tested-build"], "melonloader_versions": ["0.7.3.0"]}
        with patch.object(compatibility, "loader_version", return_value="0.7.3"):
            result = self.install_release(self.first, "Early access", {"Shared.dll": b"one"}, requirements)
        self.assertEqual(result.mods.release, "Early access")
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("wrong-build", result.warnings[0])
        cached = switching.cached_manifest(self.game, self.first)
        self.assertEqual(cached.compatibility, requirements)
        self.assertEqual(cached.release, result.mods.release)

    def test_launch_hook_uses_registered_pack_and_honors_manual_mode(self):
        self.install(None, b"sheikah")
        first = self.install(self.first, b"first")
        second = self.install(self.second, b"second")
        exe = Path(self.game, core.GAME_EXE)
        exe.touch()
        root = SimpleNamespace(_print=lambda *args: None, v_exe=SimpleNamespace(get=lambda: str(exe)))
        with patch.object(client, "_app", return_value=root), \
                patch.object(client.runtime, "game_running", return_value=False), \
                patch.object(window, "wait_for"), \
                patch.object(window, "run_waiting", side_effect=run_inline), \
                patch.object(core, "read_manifest", side_effect=lambda *, manifest_url:
                             first.mods if manifest_url == self.first.manifest_url else second.mods):
            client.before_launch([str(exe), "/dev_server_ip", self.first.host])
            self.assertEqual((self.live / "Shared.dll").read_bytes(), b"first")
            self.switch(self.second.id, manual=True)
            client.before_launch([str(exe), "/dev_server_ip", self.first.host])
            self.assertEqual((self.live / "Shared.dll").read_bytes(), b"second")
            switching.resume_automatic(self.game)
            client.before_launch([str(exe)])
            self.assertFalse((self.live / "Shared.dll").exists())

    def test_launch_hook_uses_launcher_hostname_when_ip_is_shared(self):
        self.install(None, b"sheikah")
        plan = self.install(self.first, b"first")
        exe = Path(self.game, core.GAME_EXE)
        exe.touch()
        address = "203.0.113.91"
        root = SimpleNamespace(_print=lambda *args: None, v_exe=SimpleNamespace(get=lambda: str(exe)),
                               v_ip=SimpleNamespace(get=lambda: self.first.host),
                               v_port=SimpleNamespace(get=lambda: str(self.first.port)))
        with patch.object(client, "_app", return_value=root), \
                patch.object(client.runtime, "game_running", return_value=False), \
                patch.object(window, "wait_for"), \
                patch.object(window, "run_waiting", side_effect=run_inline), \
                patch.object(switching, "_addresses", return_value={address}), \
                patch.object(core, "read_manifest", return_value=plan.mods):
            client.before_launch([str(exe), "/dev_server_ip", address, "/dev_server_port", str(self.first.port)])
        self.assertEqual((self.live / "Shared.dll").read_bytes(), b"first")

    def test_share_link_dialog_previews_then_registers_without_installing(self):
        root = make_root(self)
        incoming = packs.Pack("", "Community", "community.test", 2000, "https://community.test/manifest.json")
        docs = core.Manifest((core.Mod("Preview.dll", "A" * 64, "https://community.test/Preview.dll"),), "1.0")

        def use_import():
            dialog = next(item for item in root.winfo_children() if isinstance(item, window.AddPackDialog))
            dialog.import_button.invoke()

        parent = tk.Toplevel(root)
        try:
            parent.after(50, use_import)
            with patch.object(window.simpledialog, "askstring", return_value=packs.share_link(incoming)), \
                    patch.object(window, "review_text", return_value=True) as review, \
                    patch.object(window, "background", side_effect=lambda widget, work, done: done(work(), None)), \
                    patch.object(core, "read_manifest", return_value=docs):
                dialog = window.AddPackDialog(root, self.game)
            self.assertEqual(dialog.result.name, incoming.name)
            review.assert_called_once()
            self.assertIn("Preview.dll", review.call_args.args[2])
            self.assertIn("Publisher community.test", review.call_args.args[2])
            self.assertFalse(core.state_path(self.game, dialog.result).exists())
        finally:
            parent.destroy()

    def test_switch_now_and_follow_server_buttons_change_mode(self):
        self.install(None, b"sheikah")
        plan = self.install(self.first, b"first")
        exe = Path(self.game, core.GAME_EXE)
        exe.touch()
        root = make_root(self)
        root.v_exe = SimpleNamespace(get=lambda: str(exe))
        with dialog_style(), patch.object(client, "check"), \
                patch.object(window, "run_waiting", side_effect=run_inline), \
                patch.object(client.runtime, "game_running", return_value=False), \
                patch.object(client.messagebox, "showerror", side_effect=AssertionError), \
                patch.object(core, "read_manifest", return_value=plan.mods):
            client._open(root)
            viewer = root._sheikah_window
            viewer.refresh_packs(self.first.id)
            viewer.show(plan)
            viewer.switch_button.invoke()
            self.assertEqual(switching.selection(self.game), (self.first.id, True))
            self.assertIn("Manual", viewer.mode_label.cget("text"))
            viewer.auto_button.invoke()
            self.assertEqual(switching.selection(self.game), (self.first.id, False))
            self.assertIn("Automatic", viewer.mode_label.cget("text"))
            viewer.destroy()
            root._sheikah_window = None
