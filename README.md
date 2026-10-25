# NAS Setup & Manager

A browser-based installer and permanent Docker manager for an existing **OpenMediaVault 8 / Debian 13 / amd64** NAS. Brazilian Portuguese and English are included; Portuguese is the default. One bootstrap command installs the local setup service. No public website or desktop app is needed.

**v0.3 test prerelease:** Storage provisioning is enabled for the exact versions below after disposable OMV validation of single disks, RAID 1/5/6, permissions, reboot persistence and interrupted-format recovery. See [the live validation record](docs/v02-vm-validation.md). Physical J1800 performance and the complete optional-app combination still need separate testing. The demo remains a simulation.

The storage adapter accepts **OMV 8.5.12-1** and, for RAID, **openmediavault-md 8.1.6-1**, on Debian 13 amd64. Other package versions fail the compatibility check before provisioning; a matching schema alone is insufficient.

## What the wizard configures

1. **Storage:** use an existing OMV EXT4 volume, or create EXT4 on one data disk, a two-disk RAID 1 mirror, RAID 5 (at least three disks), or RAID 6 (at least four disks).
2. **Users and folders:** create multiple ordinary file-sharing users and named SMB folders. Give each user read/write, read-only, or no access to each folder. These accounts do not receive administrator or SSH access.
3. **Applications:** optionally install Docker/Compose through OMV-Extras, Immich, Jellyfin, and Tailscale. Storage and sharing work without selecting any optional apps.
4. **Review:** check disk serials, usable capacity, accounts and permissions. Creating storage requires selecting each erasure acknowledgment and typing `ERASE`.
5. **Progress:** show installation, required external login and RAID synchronization. Finish is blocked until the array is healthy.

The operating-system disk is excluded. RAID creation accepts directly connected SATA/SAS/NVMe data disks; USB RAID is excluded. Existing filesystem contents are retained when selecting an existing volume, and existing users/folders are not silently adopted or overwritten. RAID tolerates some disk failures; it is not a backup.

For a J1800 with two 1 TB data disks, choose **RAID 1** for approximately **1 TB usable** before filesystem overhead. OMV needs a separate system disk. Immich can be demanding on that CPU; machine learning is optional and CPU-gated.

**Still manual:** install OMV from its ISO, run the bootstrap command on the NAS, open the printed HTTPS address, approve any disk erasure, authorize Tailscale, and create the first accounts inside Immich/Jellyfin. The wizard does not install the operating system or migrate existing applications.

## Try the safe demo

Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/nas-setup demo
```

Open **http://127.0.0.1:8765** and choose **Explorar demonstração**. It simulates a J1800 with 8 GB RAM, four selectable data disks, and an excluded system disk. No apt, Docker, systemd, or disk administration commands run in demo mode. The demo is restricted to loopback HTTP; real installations use HTTPS.

Demo jobs persist under `.demo/`. To start a separate demonstration, use a fresh directory, for example `nas-setup demo --state .demo-another --port 8766`.

## Build the release

```sh
python3 scripts/build_release.py
```

This creates `dist/nas-setup-0.3.0.tar.gz` and a SHA-256 checksum. The archive contains its own `SHA256SUMS`. No credentials, virtual environments, or demo state are packaged.

## Install on an OMV test machine

1. Install the official OMV 8 ISO on a separate system disk. For storage testing, attach disposable data disks; pre-mounting is only needed when choosing an existing volume.
2. Copy the release archive and its checksum to the NAS. Verify the archive (`sha256sum -c nas-setup-0.3.0.tar.gz.sha256`) and extract it.
3. Run the single bootstrap command:

```sh
sudo bash nas-setup-0.3.0/scripts/bootstrap.sh
```

If the NAS has multiple LAN addresses, specify the intended one:

```sh
sudo bash nas-setup-0.3.0/scripts/bootstrap.sh --address 192.168.1.50
```

The bootstrap checks the supported OS before installing dependencies. It asks for your manager login, then prints an HTTPS address and the certificate fingerprint. Compare the fingerprint before trusting the generated certificate in your browser. Then choose storage, users and folder access, select optional apps, review the plan, and start installation. The adapter checks the tested package versions and RPC schemas before provisioning.

### Download directly on the NAS

The command below downloads the **v0.3.0 manager-and-app-store prerelease** directly from RubliTV GitHub.

The GitHub test release is intended for an OMV test machine with disposable data. Log in to the NAS as root and run:

```sh
curl -fsSL https://github.com/RubliTV/nas-setup/releases/download/v0.3.0/install.sh | bash
```

The helper downloads the versioned archive over HTTPS, verifies its embedded SHA-256 checksum, extracts into a temporary directory, then runs the same bootstrap above. Your Mac does not need to be online. To inspect before executing, download `install.sh` first and read it locally. The helper and archive come from the same publisher; checksums detect corruption, not publisher compromise.

For multiple LAN addresses, pass the selected NAS address:

```sh
curl -fsSL https://github.com/RubliTV/nas-setup/releases/download/v0.3.0/install.sh | bash -s -- --address 192.168.1.50
```

Maintainers can build all three release assets with `python3 scripts/build_release.py --repository RubliTV/nas-setup`. Upload the generated archive, checksum and `install.sh` together to a new versioned release after validation. Never replace a published version with a different archive; the v0.1.0 assets remain unchanged.

### Permanent manager login

The bootstrap asks for a separate manager username and password in the terminal. After setup, the same HTTPS address on port 8443 opens the dashboard. Finish keeps the web service and worker running. To display the address and ensure the services are running:

```sh
sudo nas-setup reopen
```

If you forget the manager login, run `sudo nas-setup manager-account --reset` locally on the NAS. A temporary recovery code is available with `sudo nas-setup reopen --pairing`. Manager passwords are stored as salted scrypt hashes, separate from NAS file-sharing accounts.

The setup wizard also collects passwords for the new file-sharing users you create. It does not collect OMV administrator, SSH, or Tailscale account passwords. Sharing passwords are never saved in browser storage or job history; passwords are retained in a root-private file (0600 inside a 0700 directory) on the NAS across failed attempts and worker restarts, then removed when storage/user setup succeeds. These files are not encrypted; root can read them while setup is unfinished. Password fields have Portuguese/English Show/Hide controls.

### Files and services

- Runtime: `/opt/nas-setup/venv` and `/opt/nas-setup/source`.
- Installer configuration and TLS certificate: `/etc/nas-setup`.
- Private worker/job/Compose state: `/var/lib/nas-setup/worker`.
- Browser pairing/session state: `/var/lib/nas-setup/web`.
- Wizard app data: selected OMV volume under `.nas-setup/`.
- Catalog app settings: selected data volume under `.nas-manager/<app>/config`.
- Manager state: `manager.sqlite`, `manager-projects/`, and private `manager-plans/` inside the worker directory.
- Manager account and sessions: `/var/lib/nas-setup/web` (password hash, no plaintext manager password).
- Services: `nas-setup-web`, `nas-setup-worker`, `nas-app-immich`, `nas-app-jellyfin`.

App data and database directories under `.nas-setup/` remain private. A **Media** folder created by the wizard can be shared over SMB and mounted read-only in Jellyfin. The adapter checks its exact OMV registration and filesystem identity. Other shared folders cannot become arbitrary container mounts. If no Media folder was selected, Jellyfin uses its private media directory.
Wizard app containers have Docker restart disabled. A systemd service checks the saved filesystem UUID and mount path before each start, then runs Compose in the foreground. Missing drives therefore cannot cause an automatic Docker restart into newly created OS-disk directories.

## App store and containers

After initial setup, the dashboard lists **all Docker containers**, including ones installed manually. You can start, stop and restart containers, view bounded logs, and open published app ports. Apps installed by this manager also offer an update action; external apps keep their original installation method for updates. Update pulls the configured image version and recreates the app without deleting its data. New catalog versions are selected through manager releases, not automatic switches to `latest`.

The first catalog includes **Radarr, Sonarr and Prowlarr**, using versioned LinuxServer images. Select an app, choose its settings drive and NAS file-sharing account, then review and install. Radarr and Sonarr also ask for an existing shared media/download folder, available inside the apps at `/data`. Prowlarr needs only its settings folder. The wizard checks folder access and port conflicts before deployment. Each card explains the app's remaining first-run setup: its own login, download clients, indexers and connections to other apps. These app accounts are separate from the NAS manager login.

If Docker was skipped during storage setup, **Set up Docker** prepares it from the dashboard. Missing storage, NAS accounts or shared folders link the user back to storage setup or explain what to create in OMV.

**Import Compose** is the advanced path: upload or paste a Compose file, answer questions for missing `${VARIABLES}`, review ports and folders, and install. Private values are masked and omitted from the review and public job history. The importer supports image-based services, bridge networks, named volumes and existing folders on mounted NAS data. Apps using NAS bind folders must contain only long-running services with `restart: unless-stopped` or `always`; one-time init jobs and other restart policies are rejected for these projects. Named-volume-only imports retain their Docker restart policies. The importer also rejects privileged access, builds, host networking, devices, external files and unsupported options with an explanation. Imported configuration, including any secrets needed by containers, is saved in root-private files on the NAS; root can read them.

Apps using NAS bind folders are supervised per project: systemd waits for their mounts, and a guard checks the saved filesystem identities before startup. Docker automatic restart is disabled for those containers. Start, stop and restart apply to the whole guarded app, as explained by the dashboard.

Installation jobs continue when the browser closes. Failed deployment retries use their private saved configuration. Storage and app operations are serialized, and updates refuse to interrupt active work. See [v0.3 validation](docs/v030-manager-validation.md) for tested behavior and limits.

## Access away from home

Install Tailscale on your phone/laptop, sign into the same private network, and connect using the NAS's Tailscale address. Set the Immich app's server address to that address with port 2283. Tailscale must be connected on both devices. This installer does not enable subnet routing or exit-node behavior. The setup web interface binds only to the chosen LAN address.

## Recovery

The server owns job execution; closing a browser does not stop an installation. Pause takes effect between steps. Resume rechecks the recorded storage identities and permissions. The saved job retains the original confirmation for its exact disk plan across retries and service restarts. Resume stays on the progress screen, checks disk identities and operation ownership, and reuses saved passwords for unfinished account creation without another prompt. RAID setup automatically applies sole pending mdadm configuration. Other pending changes can be reviewed and applied from the progress screen before any storage operation, with approval bound to the current configuration. The durable operation journal never blindly repeats a wipe or format. If a destructive response was lost and ownership cannot be proven, the wizard stops with a recovery error rather than guessing. No automatic reboot is performed.

Command failures are recorded in a bounded, private log with generated database secrets and URLs redacted. Inspect it locally before sharing any diagnostics:

```sh
sudo cat /var/lib/nas-setup/worker/diagnostics.log
```

For service diagnostics:

```sh
sudo journalctl -u nas-setup-worker -u nas-setup-web --since '30 minutes ago'
sudo journalctl -u nas-app-immich -u nas-app-jellyfin --since '30 minutes ago'
```

To update an installed v0.2.0–v0.2.4 or reinstall v0.3.0 (including a failed or paused job), run as root on the NAS:

```sh
curl -fsSL https://github.com/RubliTV/nas-setup/releases/download/v0.3.0/install.sh | bash -s -- --update
```

The updater preserves saved jobs, disk-operation journals, manager credentials, project configurations, certificates and application data. It refuses to update while setup or a manager operation is running. It stages the runtime before stopping the interface and retains the old runtime for rollback. On the first upgrade to v0.3, create the separate manager login when the terminal asks. Open the printed address and sign in. A previously failed setup can be resumed from the setup wizard. v0.1 upgrades are not supported by this release.

The progress screen shows the current operation and elapsed time while long steps run. The smoothly animated percentage still measures completed steps, not estimated time. A connection warning replaces the activity animation if status updates stop. Resume reuses the passwords submitted when installation started; existing accounts are reverified without resetting their passwords. Passwords are never returned to the browser and accepted passwords are cleared from all inputs, even if revealed. If an older release already discarded the file, or the file is missing or invalid, unfinished accounts require one more entry; subsequent retries retain it. Before installation starts, passwords remain only in browser memory, so reloading the initial form clears them.

Immich now starts its database before launching the server. A pristine database left by interrupted initial setup is completed automatically, preserving the original authentication file and password. Existing databases, custom roles or user tables prevent automatic repair; the installer never resets the database or deletes photos. After updating a failed Immich installation, use Resume in the wizard.

A failed bootstrap can leave dependencies or a partial `/opt/nas-setup` installation. Automatic removal/rollback is intentionally not attempted. Inspect the printed error and partial files before retrying; do not delete application data.

## Tests

```sh
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Browser tests: install Playwright for Node.js, then run `node tests/browser.cjs`, `node tests/browser-progress.cjs`, and `node tests/browser-manager.cjs`. Each suite starts an isolated demo and cleans up afterward. Set `NAS_DEMO_URL` only when testing an already running fresh demo. Set `PLAYWRIGHT_MODULE` to your Playwright module path if it is not available through normal Node resolution. Optionally set `BROWSER_EXECUTABLE` to an existing Chrome/Chromium executable.

See [tests and limitations](docs/testing.md) and [disposable OMV validation](docs/v02-vm-validation.md). J1800 hardware performance, external Tailscale enrollment, and the complete application image combination need separate tests even after storage validation passes.
