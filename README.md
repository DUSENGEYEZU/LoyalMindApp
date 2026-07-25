---
noteId: "20d49310884111f1a39b93143153b659"
tags: []

---

# LoyalMindApp

Local and production infrastructure for Loyal Minds Ltd's data collection platform, built on **[KoboToolbox](https://www.kobotoolbox.org/)**.

This repo does not contain KoboToolbox's source code — it contains everything needed to **configure, run, and deploy** a KoboToolbox instance: environment configuration, a Docker Compose entry point, and setup scripts. KoboToolbox itself is pulled in as Docker images plus two upstream repositories (`kobo-install`, `kobo-docker`), both cloned locally and gitignored (see [Project structure](#project-structure)).

---

## Prerequisites

- **Docker Desktop** (or Docker Engine + Compose v2) — `docker compose version` should report `v2.20+` (this project uses the `include:`/`COMPOSE_FILE` chaining features)
- **Python 3.12+** with `pip`
- **git**
- On macOS: the `netifaces` Python package (`pip3 install --user netifaces`) — required by `kobo-install`'s setup wizard

---

## Quick start

```bash
./scripts/kobo-start.sh
```

This starts every container (KPI, KoboCat, Enketo Express, PostgreSQL, MongoDB, Redis, nginx — 12 containers total) as a single Docker Compose project and waits for the app to answer before returning.

Once it's up, open the app at the domain configured in `.env` (locally: **http://kf.kobo.local**) and log in with the `KOBO_SUPERUSER_USERNAME` / `KOBO_SUPERUSER_PASSWORD` set in that same file.

To stop everything (data is untouched — safe to restart anytime):

```bash
./scripts/kobo-stop.sh
```

Other useful commands (run from this directory, since Compose reads `.env` here automatically):

```bash
docker compose ps           # status of all containers
docker compose logs -f kpi  # tail logs for the main app (or: nginx, postgres, mongo, ...)
docker compose up -d        # same as kobo-start.sh, but skips the .env sync check
docker compose down         # same as kobo-stop.sh
```

---

## Configuration: `.env` is the single source of truth

Every variable used anywhere in this stack — domain/subdomains, admin credentials, PostgreSQL/MongoDB/Redis credentials, Django and Enketo secret keys, SMTP settings, ports — lives in **`.env`** in this directory. It is gitignored (it contains secrets); `.env.example` is the tracked, secrets-blanked template to copy from on a fresh checkout.

### The `KOBO_SETUP_REQUIRED` flag

- **`false`** (default / normal day-to-day value): `kobo-start.sh` starts the stack exactly as currently configured. Nothing in `kobo-install/.run.conf` or `kobo-env/` is touched.
- **`true`**: on the next `./scripts/kobo-start.sh` (or manual `python3 scripts/kobo_apply_env.py`), every value in `.env` is written into `kobo-install/.run.conf` and re-rendered into `kobo-env/` (KoboToolbox's actual runtime config), then the flag is **automatically reset to `false`**.

So: edit `.env`, set `KOBO_SETUP_REQUIRED=true`, run `./scripts/kobo-start.sh` — your changes apply and the flag resets itself. Leave it `false` the rest of the time.

### What changing `.env` can do

| You want to...                              | Edit these `.env` vars                                                        |
|----------------------------------------------|--------------------------------------------------------------------------------|
| Change the admin login                       | `KOBO_SUPERUSER_USERNAME`, `KOBO_SUPERUSER_PASSWORD`                          |
| Point at a real domain (production)          | `KOBO_INSTALL_TYPE=server`, `KOBO_PUBLIC_DOMAIN_NAME`, `KOBO_*_SUBDOMAIN`      |
| Rotate database/queue passwords              | `POSTGRES_PASSWORD`, `MONGO_*_PASSWORD`, `REDIS_PASSWORD`                       |
| Send real email                              | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_USE_TLS`         |
| Change exposed ports                         | `KOBO_NGINX_PORT`, `POSTGRES_PORT`, `MONGO_PORT`, `REDIS_MAIN_PORT`/`REDIS_CACHE_PORT` |

---

## Project structure

```
LoyalMindApp/
├── .env                  # secrets + config — single source of truth (gitignored)
├── .env.example           # tracked template (secrets blanked)
├── docker-compose.yml     # override layer: platform pins + network fix (see below)
├── scripts/
│   ├── kobo-start.sh       # apply .env (if needed) + start the stack
│   ├── kobo-stop.sh        # stop the stack
│   └── kobo_apply_env.py   # .env -> kobo-install/.run.conf -> kobo-env/ sync
├── kobo-install/          # upstream installer (gitignored, cloned automatically)
├── kobo-docker/           # upstream compose files + runtime data (gitignored)
│   ├── .vols/              # PostgreSQL/MongoDB/Redis data (bind mounts, not Docker volumes)
│   ├── backups/            # backup output, if enabled
│   └── log/                # container logs
└── kobo-env/              # rendered runtime config, generated from .env (gitignored)
```

`kobo-install` and `kobo-docker` are the official upstream projects ([kobotoolbox/kobo-install](https://github.com/kobotoolbox/kobo-install), [kobotoolbox/kobo-docker](https://github.com/kobotoolbox/kobo-docker)). They are cloned locally the first time setup runs and are **not** committed — treat them as build artifacts, not project source.

### Why `docker-compose.yml` exists

`kobo-install` normally runs the frontend and backend as two **separate** Compose projects (`kobofe` / `kobobe`). This repo's `.env` sets `COMPOSE_FILE` to chain both of `kobo-docker`'s compose files together with this repo's `docker-compose.yml` as a final override layer, so the whole thing runs as **one** Compose project (`loyalmind-kobo`) startable/stoppable with a single command. That override layer also:

- Un-externalizes the backend network (`kobo-be-network`), since frontend and backend now share one project and don't need kobo-install's separate-project trick.
- Pins a handful of images (`kpi` and its Celery workers/beat, `enketo_express`, `postgres`) to `platform: linux/amd64`, because those images publish no `arm64` build — required to run them under emulation on Apple Silicon Macs.

**Deploying to an x86_64 server (e.g. Ubuntu):** delete the `platform: linux/amd64` lines in `docker-compose.yml` — native images are available there and emulation isn't needed.

---

## Data persistence

PostgreSQL, MongoDB, and Redis all store data via **bind mounts** under `kobo-docker/.vols/`, not named Docker volumes. This means:

- Stopping/starting/recreating containers (`kobo-stop.sh` → `kobo-start.sh`, or `docker compose up -d` after a config change) never loses data.
- Back up the project by backing up `kobo-docker/.vols/` (and `kobo-docker/backups/` if scheduled backups are enabled via `KOBO_USE_BACKUP`).

---

## Troubleshooting

- **`You must install netinfaces first!`** — run `pip3 install --user netifaces` (macOS/Linux) and retry.
- **Docker image pulls fail with `... EOF`** — Docker Desktop's VM can't reach Docker Hub. If you're on a VPN or proxy client, disconnect it and retry; this was the root cause the one time we hit it.
- **`no matching manifest for linux/arm64/v8`** — the image has no arm64 build. Add `platform: linux/amd64` for that service in `docker-compose.yml` (already done for the known offenders — see above).
- **App not answering right after `kobo-start.sh`** — a cold start (first boot, or right after `docker compose down`) can take a couple of minutes while KPI runs its startup checks/migrations. Check progress with `docker compose logs -f kpi`.

---

## Roadmap

1. ✅ Run KoboToolbox locally on macOS via Docker (this document).
2. ⏳ Front the stack with nginx on a custom domain/subdomain, proxying to the local Compose stack.
3. ⏳ Deploy to the production Ubuntu server, pointed at a real domain.
