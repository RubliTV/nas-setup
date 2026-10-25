# NAS Setup

A browser-based installer for an existing **OpenMediaVault 8 / Debian 13 / amd64** NAS. Brazilian Portuguese and English are included; Portuguese is the default. One bootstrap command installs the local setup service. No public website or desktop app is needed.

**v0.2 test prerelease:** Storage provisioning is enabled for the exact versions below after disposable OMV validation of single disks, RAID 1/5/6, permissions, reboot persistence and interrupted-format recovery. See [the live validation record](docs/v02-vm-validation.md). Physical J1800 performance and the complete optional-app combination still need separate testing. The demo remains a simulation.

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

This creates `dist/nas-setup-0.2.2.tar.gz` and a SHA-256 checksum. The archive contains its own `SHA256SUMS`. No credentials, virtual environments, or demo state are packaged.

## Install on an OMV test machine

1. Install the official OMV 8 ISO on a separate system disk. For v0.2 testing, attach disposable data disks; pre-mounting is only needed when choosing an existing volume.
2. Copy the release archive and its checksum to the NAS. Verify the archive (`sha256sum -c nas-setup-0.2.2.tar.gz.sha256`) and extract it.
3. Run the single bootstrap command:

```sh
sudo bash nas-setup-0.2.2/scripts/bootstrap.sh
```

If the NAS has multiple LAN addresses, specify the intended one:

```sh
sudo bash nas-setup-0.2.2/scripts/bootstrap.sh --address 192.168.1.50
```

The bootstrap checks the supported OS before installing dependencies. It prints an HTTPS address, a 15-minute single-use access code, and the certificate fingerprint. Compare the fingerprint before trusting the generated certificate in your browser. Then choose storage, users and folder access, select optional apps, review the plan, and start installation. The adapter checks the tested package versions and RPC schemas before provisioning.

### Download directly on the NAS

The command below downloads the **v0.2.2 storage-and-sharing prerelease** directly from RubliTV GitHub.

The GitHub test release is intended for an OMV test machine with disposable data. Log in to the NAS as root and run:

```sh
curl -fsSL https://github.com/RubliTV/nas-setup/releases/download/v0.2.2/install.sh | bash
```

The helper downloads the versioned archive over HTTPS, verifies its embedded SHA-256 checksum, extracts into a temporary directory, then runs the same bootstrap above. Your Mac does not need to be online. To inspect before executing, download `install.sh` first and read it locally. The helper and archive come from the same publisher; checksums detect corruption, not publisher compromise.

For multiple LAN addresses, pass the selected NAS address:

```sh
curl -fsSL https://github.com/RubliTV/nas-setup/releases/download/v0.2.2/install.sh | bash -s -- --address 192.168.1.50
```

Maintainers can build all three release assets with `python3 scripts/build_release.py --repository RubliTV/nas-setup`. Upload the generated archive, checksum and `install.sh` together to a new versioned release after validation. Never replace a published version with a different archive; the v0.1.0 assets remain unchanged.

### Finish and reopen

Finish setup disables the installer web service and worker. Installed apps continue running. To reopen it with a fresh access code:

```sh
sudo nas-setup reopen
```

This is a local command. The setup UI collects passwords only for the new file-sharing users you create. It does not collect OMV administrator, SSH, or Tailscale account passwords. Sharing passwords are never saved in browser storage or job history; temporary root-private credential files are removed after use or worker restart.

### Files and services

- Runtime: `/opt/nas-setup/venv` and `/opt/nas-setup/source`.
- Installer configuration and TLS certificate: `/etc/nas-setup`.
- Private worker/job/Compose state: `/var/lib/nas-setup/worker`.
- Browser pairing/session state: `/var/lib/nas-setup/web`.
- App data: selected OMV volume under `.nas-setup/`.
- Services: `nas-setup-web`, `nas-setup-worker`, `nas-app-immich`, `nas-app-jellyfin`.

App data and database directories under `.nas-setup/` remain private. A **Media** folder created by the wizard can be shared over SMB and mounted read-only in Jellyfin. The adapter checks its exact OMV registration and filesystem identity. Other shared folders cannot become arbitrary container mounts. If no Media folder was selected, Jellyfin uses its private media directory.
Containers have Docker restart disabled. A systemd service checks the saved filesystem UUID and mount path before each start, then runs Compose in the foreground. Missing drives therefore cannot cause an automatic Docker restart into newly created OS-disk directories.

## Access away from home

Install Tailscale on your phone/laptop, sign into the same private network, and connect using the NAS's Tailscale address. Set the Immich app's server address to that address with port 2283. Tailscale must be connected on both devices. This installer does not enable subnet routing or exit-node behavior. The setup web interface binds only to the chosen LAN address.

## Recovery

The server owns job execution; closing a browser does not stop an installation. Pause takes effect between steps. Resume rechecks the recorded storage identities and permissions. The saved job retains the original confirmation for its exact disk plan across retries and service restarts. Resume stays on the progress screen, checks disk identities and operation ownership, and requests fresh passwords only for unfinished account creation. RAID setup automatically applies sole pending mdadm configuration. Other pending changes can be reviewed and applied from the progress screen before any storage operation, with approval bound to the current configuration. The durable operation journal never blindly repeats a wipe or format. If a destructive response was lost and ownership cannot be proven, the wizard stops with a recovery error rather than guessing. No automatic reboot is performed.

Command failures are recorded in a bounded, private log with generated database secrets and URLs redacted. Inspect it locally before sharing any diagnostics:

```sh
sudo cat /var/lib/nas-setup/worker/diagnostics.log
```

For service diagnostics:

```sh
sudo journalctl -u nas-setup-worker -u nas-setup-web --since '30 minutes ago'
sudo journalctl -u nas-app-immich -u nas-app-jellyfin --since '30 minutes ago'
```

To update an installed v0.2.0 or v0.2.1 (including a failed or paused job), run as root on the NAS:

```sh
curl -fsSL https://github.com/RubliTV/nas-setup/releases/download/v0.2.2/install.sh | bash -s -- --update
```

The updater preserves saved jobs, disk-operation journals, configuration, certificates and application data. It refuses to update while an installation is running. It stages the runtime before stopping the interface and retains the old runtime for rollback. Open the printed address, use the new access code, and click Resume on the saved installation. v0.1 upgrades are not supported by this patch.

Immich now starts its database before launching the server. A pristine database left by interrupted initial setup is completed automatically, preserving the original authentication file and password. Existing databases, custom roles or user tables prevent automatic repair; the installer never resets the database or deletes photos. After updating a failed Immich installation, use Resume in the wizard.

A failed bootstrap can leave dependencies or a partial `/opt/nas-setup` installation. Automatic removal/rollback is intentionally not attempted. Inspect the printed error and partial files before retrying; do not delete application data.

## Tests

```sh
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Browser test: install Playwright for Node.js, then run `node tests/browser.cjs`. It starts an isolated demo on port 8767 and cleans up afterward. Set `NAS_DEMO_URL` only when testing an already running fresh demo. Set `PLAYWRIGHT_MODULE` to your Playwright module path if it is not available through normal Node resolution. Optionally set `BROWSER_EXECUTABLE` to an existing Chrome/Chromium executable.

See [tests and limitations](docs/testing.md) and [disposable OMV validation](docs/v02-vm-validation.md). J1800 hardware performance, external Tailscale enrollment, and the complete application image combination need separate tests even after storage validation passes.
