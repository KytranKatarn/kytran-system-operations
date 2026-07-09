#!/usr/bin/env bash
# Propagate canonical kytran_auth.py to all product deployment directories.
# Run this after updating kytran-auth/kytran_auth.py, then rebuild affected containers.
#
# Usage: cd kytran-auth && ./sync.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL="$SCRIPT_DIR/kytran_auth.py"
ROOT="$(dirname "$SCRIPT_DIR")"

DESTINATIONS=(
    "$ROOT/archie-comms/kytran_auth.py"
    "$ROOT/what-the-fact/shared/kytran_auth.py"
    # Bundled kytran-auth-pkg/ destinations (pip-install from local bundle, outside ROOT)
    "/mnt/standalone/kytran-gov-watch/kytran-auth-pkg/kytran_auth.py"
    # media_hub: Dockerfile already sources from kytran-auth/ directly (no copy needed)
    # kytran-business-suite: own repo — update manually
    # kytran-server-manager: own repo — update manually
    # civic-watch, legal-watch, market-watch: add after Task 9 migration
)

echo "Syncing canonical kytran_auth.py to product directories..."
for dest in "${DESTINATIONS[@]}"; do
    if [[ -d "$(dirname "$dest")" ]]; then
        cp "$CANONICAL" "$dest"
        echo "  ✓ $(realpath --relative-to="$ROOT" "$dest")"
    else
        echo "  ✗ SKIP (dir missing): $dest"
    fi
done
echo "Done."
