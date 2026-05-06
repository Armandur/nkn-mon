#!/usr/bin/env bash
# Speglar repot till /mnt/vmworkspace/nkn-mon (virtiofs-mount mot Unraid-hosten).
#
# Användning:
#   ./scripts/sync-to-vmworkspace.sh           # synka skarpt
#   ./scripts/sync-to-vmworkspace.sh -n        # dry-run
#   ./scripts/sync-to-vmworkspace.sh -nv       # dry-run + verbose
#
# Extra argument skickas vidare till rsync.
set -euo pipefail

SRC="/home/rasmus/workspace/nkn-mon/"
DST="/mnt/vmworkspace/nkn-mon/"

if [[ ! -d "/mnt/vmworkspace" ]]; then
  echo "FEL: /mnt/vmworkspace finns inte - är virtiofs-mounten aktiv?" >&2
  exit 1
fi

mkdir -p "$DST"

# --no-owner/--no-group/--no-perms: virtiofs-mounten styr rättigheter (nobody:users)
# --chmod=ugo=rwX: gör allt läs-/skrivbart för hosten
# --delete: spegling, raderade filer i SRC tas bort i DST
rsync \
  --archive \
  --no-owner --no-group --no-perms \
  --chmod=ugo=rwX \
  --delete \
  --human-readable \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='*.egg-info/' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='node_modules/' \
  --exclude='tmp/' \
  --exclude='dev.log' \
  --exclude='*.log' \
  --exclude='*.db' \
  --exclude='*.db-journal' \
  "$@" \
  "$SRC" "$DST"

echo "Speglat $SRC -> $DST"
