#!/bin/sh
set -eu
# This setup helper may run only in the dedicated disposable guest.
[ "$(uname -s)" = Linux ] && [ "$(hostname)" = nas-v02-vm ] && [ "$(id -u)" = 0 ] || exit 1
[ "$(dpkg --print-architecture)" = amd64 ] || exit 1
. /etc/os-release
[ "$VERSION_ID" = 13 ] || exit 1
for serial in NASV02DATA1 NASV02DATA2 NASV02DATA3 NASV02DATA4 NASV02SYSTEM; do
    lsblk -dn -o SERIAL | grep -qx "$serial" || exit 1
done
export LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
apt-get update
apt-get install -y systemd-resolved psmisc gnupg wget ca-certificates
systemctl enable --now systemd-resolved
resolvectl dns enp0s2 10.0.2.3 || resolvectl dns ens3 10.0.2.3 || resolvectl dns "$(ip -o route show default | awk '{print $5; exit}')" 10.0.2.3
wget -qO /tmp/openmediavault.key https://packages.openmediavault.io/archive.key
gpg --dearmor --yes --output /usr/share/keyrings/openmediavault-archive-keyring.gpg /tmp/openmediavault.key
cat > /etc/apt/sources.list.d/openmediavault.list <<'EOF'
deb [signed-by=/usr/share/keyrings/openmediavault-archive-keyring.gpg] http://packages.openmediavault.org/public/ synchrony main
deb [signed-by=/usr/share/keyrings/openmediavault-archive-keyring.gpg] http://packages.openmediavault.io/ synchrony main
EOF
apt-get update
apt-get --yes --no-install-recommends --option DPkg::Options::=--force-confdef --option DPkg::Options::=--force-confold install openmediavault openmediavault-md smbclient python3-venv
omv-confdbadm populate
dpkg-query -W openmediavault openmediavault-md
