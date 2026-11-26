# Sheikah Mods

A TavernLauncher addon for installing, updating, and switching client mod packs for A Township Tale. Sheikah Sanctuary is included, and players can add packs from other servers.

## Install

1. Close TavernLauncher and A Township Tale.
2. Extract the `sheikah_mods` folder into TavernLauncher's `addons` folder. Allow it to replace files from an earlier release.
3. Enable **Sheikah Mods** in **Addons**, save, and restart TavernLauncher. Install MelonLoader and start the game once if its `Mods` folder does not exist yet.
4. Open **Sheikah Mods** beside **Mods** in the launcher. Use a single updater for any given set of DLLs.

Your existing Sheikah files and backups carry over from v1.2.2.

## Add a pack

Choose **Add pack → Import share link** and paste a link from the pack owner. Review the publisher, server, and DLL list before selecting **Add pack**. You can also enter the pack name, game server host or IP, port, and HTTPS manifest URL directly.

To download files ahead of time, select the pack and use **Check now** followed by **Install mods**. Use packs from publishers you trust, since the DLLs run as game code.

Under **Pack options**, choose **Edit pack** to change a custom pack's details without losing its installed files. **Remove pack** previews what will be parked or restored. Removing a pack keeps its downloads and saved versions available if you add the same server and manifest URL later.

## Choose when packs switch

- **Automatic** is the default. Joining a registered server through TavernLauncher loads the pack assigned to its host and port. The addon also matches resolved addresses, including when TavernLauncher gives the game an IP instead of the hostname you entered. If multiple packs share that IP and port, join using the registered hostname or switch manually. Unassigned servers and **Main Menu** use your regular mods.
- **Switch now** applies the selected pack immediately while the game is closed. This turns on manual mode. Your selection stays active on subsequent launches, including Main Menu, until you select another pack or click **Follow server**.
- **Follow server** returns to automatic matching on the next launch.
- **Preview launch** shows the active pack, mode, and installed files for both **Join Server** and **Main Menu**. It calls out temporary DLL replacements, missing files, and compatibility notices from the saved manifest.
- Close the game before installing, updating, or switching. Starting a new game process is required to load different DLLs.

Updates to an inactive pack go into its own holding folder. Switching preserves unrelated mods and restores existing files that an active pack temporarily replaced. Cached manifests let the addon verify a complete installed pack when its site is offline. Each download is checked against its SHA-256 digest before installation.

## Restore a previous version

The updater saves one previous version when it can verify the files already installed. Select the pack and choose **Pack options → Restore previous version** to review and restore those local files. A restored version stays pinned across launches until you choose **Resume updates**. You can restore offline. If a saved file fails verification, the current install is preserved.

## Publish a pack for your server

1. Put the client DLLs you want players to use into a folder.
2. In **Sheikah Mods → Add pack**, choose **Create manifest from DLLs** and select that folder. You can add a release label and list supported game builds and MelonLoader versions. **Use this installation's versions** copies the detected values if you are publishing from the setup you tested. The addon writes `manifest.json` with the DLL names, SHA-256 hashes, and relative download URLs.
3. Upload the manifest and its DLLs to the same HTTPS folder. Download URLs and redirects must stay on the manifest hostname. Changing a DLL requires regenerating the manifest.
4. Register the pack in your launcher, then send players the link from **Pack options → Copy share link**.

A manifest can list up to 300 client DLLs, with a combined download limit of 512 MiB per update. TavernLauncher manages its own voice chat files. Keep those and server-only DLLs out of the pack.

If you edit manifests directly, use the optional `release` string and `compatibility` object alongside `files`. The compatibility object accepts `game_builds` and `melonloader_versions`, each an array of supported versions. Use the exact build identifier from the game's `version` file and numeric MelonLoader versions such as `0.7.3`. These declarations show a warning when the installed version is outside the supported list or cannot be detected. Players can review the warning and choose whether to continue.

## HTTPS certificates

Keep `cacert.pem` beside `core.py` when installing or packaging the addon.
If certificate verification still fails, check the affected PC's date and time and install the
latest addon. The launcher log includes the SSL error, OpenSSL version, and local time.

`cacert.pem` is the unmodified Mozilla CA bundle from
[certifi 2026.10.23](https://pypi.org/project/certifi/2026.10.23/), distributed under the
Mozilla Public License 2.0 in `CERTIFI-LICENSE`. Refresh it from a maintained certifi release
when preparing future addon releases; do not download replacement trust roots at runtime.

- Source wheel: `certifi-2026.10.23-py3-none-any.whl` from PyPI.
- Wheel SHA-256: `e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1`.
- Bundle SHA-256: `2ef52ad56b4803e264045c915e55c236cbfa045b84b81efefc218a19eb44bd2b`.

## Development

Run `python -m unittest discover -s sheikah_mods/tests -v` from the repository root. The tests use temporary folders and fake downloads. Run `python sheikah_mods/tests/preview.py` for a sample of the update screen.
