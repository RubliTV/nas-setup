#!/bin/sh
# Run from the unpacked, verified release. Does not format disks.
set -eu
NAS_RELEASE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$NAS_RELEASE_DIR/packaging/install.py" "$@"
