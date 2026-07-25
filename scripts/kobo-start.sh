#!/usr/bin/env bash
# Bootstraps kobo-install if this is a fresh checkout, applies .env, then
# starts the whole KoboToolbox stack as a single `docker compose` project.
#
# This is the ONE command a brand new machine (e.g. the Ubuntu server) needs
# after `git clone` + filling in `.env` - kobo-install and kobo-docker are
# both gitignored (upstream/generated, not project source) and get cloned
# here automatically if missing.
#
# Usage: ./scripts/kobo-start.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f .env ]; then
  echo "ERROR: .env not found at $PROJECT_ROOT/.env" >&2
  echo "Copy .env.example to .env and fill in real values first." >&2
  exit 1
fi

if [ ! -d kobo-install ]; then
  echo "==> kobo-install/ not found - cloning it (first run on this machine)..."
  git clone https://github.com/kobotoolbox/kobo-install kobo-install
fi

if ! python3 -c "import netifaces" >/dev/null 2>&1; then
  echo "==> Installing kobo-install's netifaces dependency..."
  pip3 install --user netifaces
fi

echo "==> Applying .env (config + docker-compose.yml)..."
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
