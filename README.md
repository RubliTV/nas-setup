# NAS Setup

A browser-based installer for an existing **OpenMediaVault 8 / Debian 13 / amd64** NAS. Brazilian Portuguese and English are included; Portuguese is the default. One bootstrap command installs the local setup service. No public website or desktop app is needed.

**Status:** First implementation, with automated API/engine/browser tests and a working simulation. **Not yet validated on a live OMV machine or J1800.** Use a disposable OMV VM before trying it on a NAS containing important data. Bundled application versions are pinned upstream releases, not yet an integration-certified combination.

## What it installs

- Docker Engine and Compose through OMV-Extras (when a media app is selected).
- Optional Immich with a local database and optional machine learning.
- Optional Jellyfin configured for direct playback.
- Optional Tailscale on the host; authorize through its official login flow.

OMV remains responsible for formatting, mounting disks, and creating SMB shares. The installer only uses existing local OMV volumes. It does not migrate existing applications or manage application upgrades.

## Try the safe demo

Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/nas-setup demo
```

Open **http://127.0.0.1:8765** and choose **Explorar demonstração**. It simulates a J1800 with 8 GB RAM and two disks. No apt, Docker, systemd, or disk administration commands run in demo mode. The demo is restricted to loopback HTTP; real installations use HTTPS.

Demo jobs persist under `.demo/`. To start a separate demonstration, use a fresh directory, for example `nas-setup demo --state .demo-another --port 8766`.

## Build the release

```sh
python3 scripts/build_release.py
```

This creates `dist/nas-setup-0.1.0.tar.gz` and a SHA-256 checksum. The archive contains its own `SHA256SUMS`. No credentials, virtual environments, or demo state are packaged.

## Install on an OMV test machine

1. Install the official OMV ISO and mount a data volume in its web interface.
2. Copy the release archive and its checksum to the NAS. Verify the archive (`sha256sum -c nas-setup-0.1.0.tar.gz.sha256`) and extract it.
3. Run the single bootstrap command:

```sh
sudo bash nas-setup-0.1.0/scripts/bootstrap.sh
```

If the NAS has multiple LAN addresses, specify the intended one:

```sh
sudo bash nas-setup-0.1.0/scripts/bootstrap.sh --address 192.168.1.50
```

The bootstrap checks the supported OS before installing dependencies. It prints an HTTPS address, a 15-minute single-use access code, and the certificate fingerprint. Compare the fingerprint before trusting the generated certificate in your browser. Then select apps, select storage, review the plan, and start installation.

There is **no published download endpoint yet**. A hosted one-line download command requires publishing the release to a repository you control. Do not substitute an invented URL. Checksums downloaded from the same origin as the release detect corruption, not publisher compromise; users must trust the publisher.

### Finish and reopen

Finish setup disables the installer web service and worker. Installed apps continue running. To reopen it with a fresh access code:

```sh
sudo nas-setup reopen
```

This is a local command. The setup UI does not collect OMV, SSH, or Tailscale account passwords.

### Files and services

- Runtime: `/opt/nas-setup/venv` and `/opt/nas-setup/source`.
- Installer configuration and TLS certificate: `/etc/nas-setup`.
- Private worker/job/Compose state: `/var/lib/nas-setup/worker`.
- Browser pairing/session state: `/var/lib/nas-setup/web`.
- App data: selected OMV volume under `.nas-setup/`.
- Services: `nas-setup-web`, `nas-setup-worker`, `nas-app-immich`, `nas-app-jellyfin`.

App data directories are private, not automatically SMB shares. Jellyfin's media folder begins empty; an administrator can copy media into the generated `jellyfin/media` directory. Its Compose media mount is read-only inside Jellyfin. Application storage/Compose files should not be edited through SMB.

Containers have Docker restart disabled. A systemd service checks the actual mounted filesystem identity before each start, then runs Compose in the foreground. Missing drives therefore cannot cause an automatic Docker restart into newly created OS-disk directories.

## Access away from home

Install Tailscale on your phone/laptop, sign into the same private network, and connect using the NAS's Tailscale address. Set the Immich app's server address to that address with port 2283. Tailscale must be connected on both devices. This installer does not enable subnet routing or exit-node behavior. The setup web interface binds only to the chosen LAN address.

## Recovery

The server owns job execution; closing a browser does not stop an installation. Pause takes effect between steps. Resume rechecks system state and storage, preserves generated database secrets, and retries the same job. No automatic reboot or disk formatting is performed.

For service diagnostics:

```sh
sudo journalctl -u nas-setup-worker -u nas-setup-web --since '30 minutes ago'
sudo journalctl -u nas-app-immich -u nas-app-jellyfin --since '30 minutes ago'
```

A failed bootstrap can leave dependencies or a partial `/opt/nas-setup` installation. Automatic removal/rollback is intentionally not attempted. Inspect the printed error and partial files before retrying; do not delete application data.

## Tests

```sh
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Browser test: install Playwright for Node.js, start a **fresh** demo on port 8765, then run `node tests/browser.cjs`. Set `PLAYWRIGHT_MODULE` to your Playwright module path if it is not available through normal Node resolution. Optionally set `BROWSER_EXECUTABLE` to an existing Chrome/Chromium executable.

See `docs/testing.md` for the real-OMV validation checklist and current limits.
