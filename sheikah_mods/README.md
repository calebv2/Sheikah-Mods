# Sheikah Mods

A standalone TavernLauncher addon for installing and updating the Sheikah Sanctuary client mods.
Version **1.2.1**. It runs independently of the Modpacks addon.
Its windows use TavernLauncher's original color scheme and shared theme controls.

Version 1.2.0 adds next-launch status, a smart action button, live progress, and friendly recovery controls.

The mod list is fixed in the addon:
https://att.aservertown.fans/manifest.json

The list is downloaded fresh when checked; its file names and SHA-256 hashes are not frozen into the addon.
The current list contains 49 DLLs. It provides no optional-mod or dependency information, so Sheikah Mods
uses the entire list rather than guessing which mods can be left out.

## Install

1. Install and configure TavernLauncher and MelonLoader. Start the game once so its `Mods` folder exists.
2. Extract the `sheikah_mods` folder into TavernLauncher's `addons` folder.
3. Enable **Sheikah Mods** in **Addons**, save, and restart TavernLauncher.
4. Review the listed changes and choose **Install mods**.

Use one updater for the Sheikah files. Disable the old Modpacks addon if it manages these same mods.
Existing Modpacks folders and settings are not migrated or removed automatically.

## Everyday use

- A permanent **Sheikah Mods** button beside the launcher's **Mods** tools opens the mod list,
  including installed mods, available changes, and installed versions when available.
- The button remains available while background checks run. Checks begin when the launcher opens.
- A status beside the launch controls follows the selected address: **Ready for Sheikah**, required
  setup/updates, or **Sheikah mods will be parked for this server**. Address lookups run in the background.
- The viewer's main button becomes **Install mods**, **Update mods**, **All up to date**, or **Retry**.
  It stays disabled while work is in progress or everything is up to date.
- Progress shows completed downloads, such as **Downloading 8 of 49**, and **Switching mods...** with file
  counts. The launcher stays responsive and the progress appears in the viewer and launch wait window.
- Errors explain the next step and offer **Retry**. Technical details remain in the launcher log.
- If the mod site is unavailable, the saved manifest is used to verify installed file hashes. A complete
  matching set is reported as usable offline; missing or different files require reconnecting to finish setup.
  Retry always attempts a fresh check, so cached data is never presented as the newest online list.
- **Check now** gets a fresh list and lets you reconsider skipped changes.
- Startup and game launches share checks from the last two minutes and share requests already in progress.
- Skipping remembers those exact changes for this launcher session.
- Close the game before installing or updating. Accepted updates finish before a launch continues.
- Joining `att.aservertown.fans:1757` loads Sheikah's mods into `Mods`. Its resolved IP or an alias for
  the same address and port is recognized too.
- Joining another server or launching **Main Menu** parks Sheikah's files in `SheikahMods/parked`
  next to the game. Returning to Sheikah moves them back automatically. No mod files are deleted by switching.
- Unrelated mods stay in `Mods`. If Sheikah previously replaced a local DLL, the local copy is restored
  for other servers. Edits made to that local copy while away are saved and restored on later switches.
- While parked, updates are installed in the holding folder, so they do not leak back into other servers.
- Files already installed before this version are recognized by their manifest hashes on the first switch.
  A matching filename alone is not enough to move an unrecognized local DLL.
- Close the game between servers and start the next server from TavernLauncher. Mods cannot be swapped
  inside a running game. Joining Sheikah from an in-game main-menu board cannot load its parked DLLs.

## File safety

- Downloads use HTTPS from `att.aservertown.fans`, with SHA-256 verification before anything is installed.
- A different local DLL is shown as **REPLACE**. Applying saves the local copy under
  `SheikahMods/backups` next to the game before replacing it.
- When the site removes a mod, only an unchanged copy installed by this addon is removed. If Sheikah Mods
  replaced a pre-existing local DLL, its original copy is restored instead. While parked, the original
  already in `Mods` is left alone. Edited files are kept.
- Unrelated DLLs in `Mods`, configuration files, and `UserData` are left alone.
- If files change during downloading, the update stops. Failed installations roll back previous files.
  If rollback cannot finish, the error identifies recovery copies under `SheikahMods/transactions`.
- Failed server switches stop the launch and roll back previous files and saved records. If rollback cannot
  finish, the error gives the remaining file locations and transaction records.
- A damaged ownership record stops updates rather than guessing which files belong to the addon.

## Scope

Sheikah Mods has no custom manifest field, pack creation, rename/delete, zip import/export, editable server assignments,
pack merging, individual mod toggles, or loose-mod organizer. Server switching is fixed to Sheikah.
TavernLauncher still handles game patching and
MelonLoader. This addon does not determine whether an individual server mod is optional.

## Development

From the `sheikah_mods` folder, run `python -m unittest discover -s tests -v`.
The tests use temporary folders and fake downloads rather than installed game files.
Run `python tests/preview.py` for a sample of the Sheikah Mods update screen.
