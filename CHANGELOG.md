# Sheikah Mods v1.2.0

Standalone TavernLauncher addon for the Sheikah Sanctuary client mod list:
https://att.aservertown.fans/manifest.json

## Easier everyday use

- Next-launch status beside Join follows the selected server: ready for Sheikah, setup or updates required, or Sheikah mods will be parked.
- One main action changes between Install mods, Update mods, All up to date, and Retry.
- Live download and switching counts keep users informed while the launcher stays responsive.
- Friendly errors explain the next step. Retry rechecks the list or retries an approved installation.
- Offline checks verify installed hashes against the saved manifest and distinguish usable installed mods from incomplete setup.

## Dedicated Sheikah setup

- Fixed manifest with concurrent downloads and SHA-256 verification.
- Sheikah's mods load only when joining att.aservertown.fans:1757, including its resolved IP or aliases for the same endpoint.
- Other servers and Main Menu park Sheikah's mods safely outside Mods, restoring local copies when needed.
- Startup and joining share recent checks; skipped changes stay skipped for the launcher session.
- Backups and rollback protect local files. The addon uses the original launcher colors and works independently of Modpacks.

## Install or update

Close TavernLauncher. Extract the `sheikah_mods` folder from **Sheikah-Mods-v1.2.0.zip** into the launcher's `addons` folder. Enable **Sheikah Mods** in Addons, save, and restart. Use one updater for the same Sheikah files.

Close the game before changing servers. Automatic switching runs before a new game process starts; in-game server boards cannot load parked DLLs.

Validated with 78 automated checks and a visual check in the packaged TavernLauncher. The ZIP contains addon source and documentation only.
