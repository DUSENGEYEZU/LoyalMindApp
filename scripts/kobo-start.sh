#!/usr/bin/env bash
# Applies .env (only if KOBO_SETUP_REQUIRED=true), then starts the whole
# KoboToolbox stack as a single `docker compose` project.
#
# Usage: ./scripts/kobo-start.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f .env ]; then
  echo "ERROR: .env not found at $PROJECT_ROOT/.env" >&2
  exit 1
fi

echo "==> Checking .env against kobo-install config..."
python3 scripts/kobo_apply_env.py

echo ""
echo "==> Starting KoboToolbox (docker compose up -d)..."
docker compose up -d

echo ""
echo "==> Waiting for nginx to answer..."
DOMAIN="$(grep -E '^KOBO_PUBLIC_DOMAIN_NAME=' .env | cut -d= -f2-)"
KPI_SUB="$(grep -E '^KOBO_KPI_SUBDOMAIN=' .env | cut -d= -f2-)"
URL="http://${KPI_SUB}.${DOMAIN}/"
for i in $(seq 1 90); do
  if curl -sf --max-time 3 -o /dev/null "$URL"; then
    echo ""
    echo "KoboToolbox is up: $URL"
    exit 0
  fi
  sleep 3
done

echo ""
echo "Containers are started but $URL isn't answering yet. Check with:"
echo "  docker compose ps"
echo "  docker compose logs -f kpi"
