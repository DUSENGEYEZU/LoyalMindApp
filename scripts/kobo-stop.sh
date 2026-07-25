#!/usr/bin/env bash
# Stops the KoboToolbox stack. Data is untouched (bind-mounted under
# kobo-docker/.vols, kobo-docker/backups, kobo-docker/log) - safe to re-run
# ./scripts/kobo-start.sh afterwards.
#
# Usage: ./scripts/kobo-stop.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

docker compose down
