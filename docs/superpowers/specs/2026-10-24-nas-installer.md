# NAS installer — proposed first release

Date: 2026-10-24
Status: Design for review; no implementation or live NAS installation yet.

## Purpose and agreed choices

Help Brazilian YouTube viewers turn an old PC into a usable home NAS. Viewers first install the official OMV ISO. One command then installs a setup service on that NAS; all subsequent setup happens in a browser. The interface supports Brazilian Portuguese and English, with Portuguese as the initial default and a persistent language selector. The user's example hardware is a Celeron J1800 with 8 GB RAM; a fourth-generation Core i5 is also available.

The user selected the NAS-hosted browser app and accepted one initial command. No SSH address or password form is needed in the browser. Initial SSH access is used only to run the bootstrap command as an administrator.

## Approaches considered

1. Terminal-only script: smaller maintenance burden, but less approachable for viewers.
2. Desktop launcher with browser UI and SSH backend: avoids manually entering a bootstrap command, but needs packages for each client OS.
3. NAS-hosted browser app: one bootstrap command, then the same interface on Windows, macOS, Linux, and phones. Selected by the user.

## Proposed scope

Target OMV 8 on Debian 13, amd64, installed from the official ISO. Reject other combinations with a translated explanation before changes. Do not reinstall OMV or change network configuration.

Offer Docker Engine/Compose infrastructure, Tailscale remote access, and optional Immich and Jellyfin. Neither media app is selected automatically. App selection explains the purpose and hardware implications in ordinary language.

V1 uses existing local filesystems mounted through OMV. Disk formatting, RAID creation, and SMB user/share administration remain in OMV, with instructions and a link from the setup app. This is a proposed scope boundary for approval. Do not implement a disk eraser or silently choose a disk.

No public website hosting is required. Distribution will need a real release download URL before the public one-command installation can be advertised. Until publication is authorized, support local release packaging and a documented local test installation; never print a fictional working download command.

## Viewer flow

1. Install OMV and establish administrator shell access.
2. Run the bootstrap command. It validates the OS, installs the setup service, and prints its address and temporary access code.
3. Open the browser interface, authenticate, and choose Português (Brasil) or English.
4. Review hardware and existing services. Choose apps and existing storage volumes.
5. Review an exact installation plan before pressing Install.
6. Follow named installation steps and progress logs. Refreshing or closing the browser does not stop the server-side job.
7. Complete Tailscale authorization through the official login URL, and complete each application's account creation in its own UI.
8. See verified service status, local access links, remote-access instructions, and any remaining setup tasks. A process starting does not count as an application being ready.

All labels, validation errors, recovery instructions, and status summaries are translated. Raw third-party command output is shown unchanged in an optional diagnostic view. Language switching retains form state and active job state. No cloud translation or telemetry.

## Architecture proposed by the implementer

Use Python on the NAS, with FastAPI/Uvicorn in an isolated virtual environment and static HTML/CSS/JavaScript frontend assets. Serve assets locally; require no Node.js runtime, Docker, or CDN to open the installer. Pin application dependencies in the release build.

Run the browser-facing service as an unprivileged dedicated account. Use a separate root-owned worker accessed through a restricted Unix socket. The worker accepts only predefined operations and validates all arguments again. The frontend cannot submit shell commands, Compose YAML, arbitrary image names, URLs to execute, or arbitrary host paths. The frontend account has no Docker socket access and is not a member of the docker group.

Store durable jobs and step results locally. Only one installation job may mutate the machine at once. Worker code, operation templates, and service units are root-owned. State and generated secrets have restrictive permissions. Use argument arrays rather than shell interpolation for commands.

## Bootstrap and authentication

Bootstrap obtains a versioned release over HTTPS, checks its checksum against release metadata, verifies prerequisites, and creates the service units. Checksums detect corruption; they are not an independent trust anchor when distributed from the same origin. Releases and installation documentation must state that users are trusting the publisher. Do not claim a signature workflow exists before implementing it.

Serve the installer on a separate, configurable port without changing OMV's existing web-server configuration. Use HTTPS with a generated certificate; print the certificate fingerprint in the terminal and explain the browser's initial certificate warning in both languages. Do not silently fall back to HTTP. Bind to a selected LAN address; conflicting ports or ambiguous addresses produce a clear actionable result. No UPnP, router port forwarding, or public exposure.

Print a cryptographically random, short-lived, one-use access code in the administrative terminal. Submit it in the login form, not in a URL. Store only its hash. Exchange it for a secure HttpOnly SameSite session cookie, protect mutations against CSRF, validate Host/Origin, and rate-limit login attempts. Logs and diagnostic exports must redact codes, cookies, generated database passwords, and Tailscale authentication URLs. A local administrator command can rotate access and invalidate sessions.

Provide a Finish action that disables the setup web service and worker after setup; installed applications continue running. Document a local administrator command to reopen setup with fresh authentication. No remote arbitrary terminal.

## Storage and existing installations

Discover OMV-mounted local filesystems and stable filesystem identifiers. Present labels, capacity, free space, and paths. The user selects a volume; the worker resolves its identity rather than trusting a browser-supplied path. Refuse network filesystems for application databases and reject symlink escapes and unexpected pre-existing app directories.

Create namespaced directories only on the selected volume. Persist the filesystem identity and recheck it immediately before each write. Application startup must depend on the selected filesystem being mounted; never let a missing data drive silently redirect writes onto the OS disk. Use Compose bind-mount settings that do not auto-create missing host paths.

Prefer local SSD storage for Immich's database; allow a separate database volume selection. Explain slower indexing if a rotational local disk is selected. Do not publish database directories through SMB.

Detect existing Docker, Compose, app projects, and Tailscale enrollment. A healthy managed installation is reusable. An unrelated or incompatible installation is reported as a conflict; no automatic migration, package removal, database upgrade, or replacement. V1 does not import unmanaged application stacks.

## Application installation

Use OMV-Extras' documented Docker/Compose installation route, verified against the supported OMV version in an integration VM. Maintain installer-managed Compose projects under a clearly identified namespace; do not promise Compose-plugin UI integration until verified. OMV remains the storage manager.

Maintain a release manifest of tested app versions and matching official Compose definitions. Use exact versions or digests; never download moving latest templates during a viewer's installation. Generate and persist secrets once, preserving them on retry. Immich server, database/vector extension, and machine-learning definitions must come from a tested compatible release set.

Check memory, CPU flags, free space, filesystem properties, internet access, package-manager locks, and port conflicts. Inspect the complete instruction requirements of the selected images rather than assuming every amd64 CPU works. If a CPU cannot run the pinned Immich ML image, disable that feature only if the selected Immich release supports a tested configuration; otherwise explain that Immich is unavailable. Do not offer an unsupported old Immich release as the workaround. Jellyfin's initial profile assumes direct playback and promises no hardware transcoding.

Install Tailscale on the host. Default to access to this NAS only, with neither subnet routing nor exit-node configuration. Start authorization and show the official login link only within the authenticated session. Poll machine-readable enrollment status. Tailscale selection remains optional and the user authenticates their own account; the installer does not collect their provider password.

## Recovery and lifecycle

Persist states: pending, running, waiting-for-user, succeeded, failed, interrupted. After restart, inspect actual package/container/service state before reconciling an interrupted step. Retries do not recreate secrets, overwrite data, or launch duplicate projects. Package failures expose actionable recovery instructions; never delete apt lock files or kill another package manager.

Timeouts bound checks and commands. Closing a browser keeps the job running. Explicit pause waits at the next safe step; do not terminate package installation mid-transaction. Do not automatically reboot. If reboot is needed, report why and resume through the durable state machine afterward.

Failed steps retain data and logs. Automatic rollback is restricted to newly generated installer files that are proven unused; never run destructive container volume deletion. Removal and app upgrades are outside v1. Show installed versions and link to maintenance instructions. The installer is not itself a backup system.

## Validation and completion criteria

Meaningful tests cover argument validation, token/session behavior, bilingual UI state, storage identity changes, symlink escapes, repeated execution, secret redaction, and job recovery. Use fake command adapters for unit and UI development so a preview cannot alter the developer's machine.

Integration-test on a disposable OMV 8 VM with distinct OS/data disks: fresh install, interrupted install, retry, reboot with data disk present and missing, port conflict, and an existing unmanaged app. Confirm OMV remains accessible and each selected app passes its own readiness check. Tailscale login requires an authorized test account; report that check as untested until performed.

Test in current desktop browsers and a narrow mobile viewport. Test the J1800 physically before claiming compatibility or performance. A passing mocked test suite is not evidence that the full NAS installation works.

## Still to decide before public release

The project name, source repository and release host, distribution license, signing identity, and exact validated app-version manifest. These do not block a local implementation but do block claiming a publicly usable one-command release. The execution host currently cannot access the configured Dialer directory; choose an accessible, dedicated NAS project directory before implementation, keeping this work separate from Dialer.

## Reference material consulted

- https://www.openmediavault.org/install.html
- https://docs.openmediavault.org/en/8.x/administration/services/ssh.html
- https://wiki.omv-extras.org/doku.php?id=omv8:docker_in_omv
- https://docs.immich.app/install/requirements/
- https://docs.immich.app/install/docker-compose/
- https://tailscale.com/docs/install/linux
