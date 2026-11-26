# Sheikah Mods v1.4.0

- Share packs with a link that previews the publisher and DLL list before import.
- Edit custom packs in place or remove them while retaining downloads and saved versions.
- Preview the pack and DLLs for Join Server and Main Menu, including conflicts and compatibility notices.
- Restore a verified previous version offline and keep it pinned until updates are resumed.
- Let publishers declare supported game builds and MelonLoader versions. Match custom packs when TavernLauncher resolves server hostnames to IPs.

# Sheikah Mods v1.3.0

- Add server-specific packs from HTTPS manifests, with a manifest builder for pack authors.
- Switch packs directly in the launcher or have the launcher select a pack by server on each launch.
- Keep each pack's files, saved manifest, and ownership record separate, including packs with overlapping DLL names.
- Set `sheikah_only` in `pack-policy.json` for a distribution that follows only the built-in Sheikah server.

# Sheikah Mods v1.2.3

- Use an included Mozilla CA bundle for mod-list and DLL downloads to avoid stale certificates in individual Windows or launcher stores. HTTPS certificate, hostname, same-site redirect, and DLL hash checks stay enabled.
- Give certificate failures specific recovery guidance and include the SSL error, OpenSSL version, and local time in the launcher log. Existing offline behavior is preserved.
- Add offline TLS tests for trusted downloads, expiry, hostname mismatch, untrusted issuers, redirect restrictions, and isolated certificate loading.

# Sheikah Mods v1.2.2

- Support large mods such as Forest Rewrite. Downloads stream to disk and are verified against the mod list before installation.
- Size-limit errors identify the affected file or update.

# Sheikah Mods v1.2.1

- Change the addon author credit to **Sheikah**.

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
