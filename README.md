---
noteId: "20d49310884111f1a39b93143153b659"
tags: []

---

# LoyalMindApp

Local and production infrastructure for Loyal Minds Ltd's data collection platform, built on **[KoboToolbox](https://www.kobotoolbox.org/)**.

This repo does not contain KoboToolbox's source code — it contains everything needed to **configure, run, and deploy** a KoboToolbox instance: environment configuration, a Docker Compose entry point, and setup scripts. KoboToolbox itself is pulled in as Docker images plus two upstream repositories (`kobo-install`, `kobo-docker`), both cloned locally and gitignored (see [Project structure](#project-structure)).

---

## Prerequisites

- **Docker Desktop** (or Docker Engine + Compose v2) — `docker compose version` should report `v2.20+`
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

Once you've started at least once, plain `docker compose` commands work directly — no wrapper, no flags:

```bash
docker compose ps           # status of all containers
docker compose logs -f kpi  # tail logs for the main app (or: nginx, postgres, mongo, ...)
docker compose up -d        # same as kobo-start.sh, but skips the .env sync
docker compose down         # same as kobo-stop.sh
```

If you edit `.env`, run `./scripts/kobo-start.sh` again before using bare `docker compose` — it's what regenerates `docker-compose.yml` from your new values (see below).

---

## Configuration: `.env` is the single source of truth

Every variable used anywhere in this stack — domain/subdomains, admin credentials, PostgreSQL/MongoDB/Redis credentials, Django and Enketo secret keys, SMTP settings, ports — lives in **`.env`** in this directory. It is gitignored (it contains secrets); `.env.example` is the tracked, secrets-blanked template to copy from on a fresh checkout.

### Every start re-applies `.env`

`./scripts/kobo-start.sh` runs `scripts/kobo_apply_env.py`, which unconditionally, every time:
1. writes the current `.env` values into `kobo-install/.run.conf`
2. re-renders `kobo-env/` (KoboToolbox's actual runtime config)
3. compiles `kobo-docker`'s compose files + `.env` into a single `docker-compose.yml` at the project root

There's no flag to flip: edit `.env`, run `./scripts/kobo-start.sh`, the change takes effect. Re-running with unchanged values is a cheap no-op.

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
├── .env                        # secrets + config — single source of truth (gitignored)
├── .env.example                 # tracked template (secrets blanked)
├── docker-compose.yml           # GENERATED - compiled stack, don't edit (gitignored)
├── scripts/
│   ├── kobo-start.sh             # apply .env + start the stack
│   ├── kobo-stop.sh              # stop the stack
│   ├── kobo_apply_env.py         # .env -> kobo-install/.run.conf -> kobo-env/ -> docker-compose.yml
│   └── compose-overrides.yml     # source of truth for platform pins / port / network fix
├── kobo-install/                # upstream installer (gitignored, cloned automatically)
├── kobo-docker/                 # upstream compose files + runtime data (gitignored)
│   ├── .vols/                    # PostgreSQL/MongoDB/Redis data (bind mounts, not Docker volumes)
│   ├── backups/                  # backup output, if enabled
│   └── log/                      # container logs
└── kobo-env/                    # rendered runtime config, generated from .env (gitignored)
```

`kobo-install` and `kobo-docker` are the official upstream projects ([kobotoolbox/kobo-install](https://github.com/kobotoolbox/kobo-install), [kobotoolbox/kobo-docker](https://github.com/kobotoolbox/kobo-docker)). They are cloned locally the first time setup runs and are **not** committed — treat them as build artifacts, not project source.

### Why `docker-compose.yml` is generated

`kobo-install` normally runs the frontend and backend as two **separate** Compose projects (`kobofe` / `kobobe`), and its compose files merge several services via a YAML anchor (`<<: *django`). Combining all of that at runtime via Compose's `COMPOSE_FILE` env var turned out to be unreliable — it intermittently drops the merged `image` field for one of those services, a different one each time.

The fix: `kobo_apply_env.py` resolves the merge **once**, using explicit `-f` flags (reliable in testing) across `kobo-docker`'s 4 files plus `scripts/compose-overrides.yml`, and writes the fully-flattened result to `docker-compose.yml`. That file has nothing left to merge at runtime, so plain `docker compose up`/`down`/`ps`/`logs` just work — no wrapper script, no flags.

**To change the override behavior** (ports, platform pins, network), edit `scripts/compose-overrides.yml`, not `docker-compose.yml` — the latter is overwritten every time `kobo-start.sh` runs. Currently `compose-overrides.yml`:

- Un-externalizes the backend network (`kobo-be-network`), since frontend and backend now share one project and don't need kobo-install's separate-project trick.
- Pins a handful of images (`kpi` and its Celery workers/beat, `enketo_express`, `postgres`) to `platform: linux/amd64`, because those images publish no `arm64` build — required to run them under emulation on Apple Silicon Macs.

**Deploying to an x86_64 server (e.g. Ubuntu):** delete the `platform: linux/amd64` lines in `scripts/compose-overrides.yml` — native images are available there and emulation isn't needed.

---

## Data persistence

PostgreSQL, MongoDB, and Redis all store data via **bind mounts** under `kobo-docker/.vols/`, not named Docker volumes. This means:

- Stopping/starting/recreating containers (`kobo-stop.sh` → `kobo-start.sh`, or `docker compose up -d` after a config change) never loses data.
- Back up the project by backing up `kobo-docker/.vols/` (and `kobo-docker/backups/` if scheduled backups are enabled via `KOBO_USE_BACKUP`).

See **[docs/DATABASES.md](docs/DATABASES.md)** for how to connect to PostgreSQL/MongoDB/Redis directly and what each table/collection contains.

---

## Troubleshooting

- **`You must install netinfaces first!`** — run `pip3 install --user netifaces` (macOS/Linux) and retry.
- **Docker image pulls fail with `... EOF`** — Docker Desktop's VM can't reach Docker Hub. If you're on a VPN or proxy client, disconnect it and retry; this was the root cause the one time we hit it.
- **`no matching manifest for linux/arm64/v8`** — the image has no arm64 build. Add `platform: linux/amd64` for that service in `scripts/compose-overrides.yml`, then re-run `./scripts/kobo-start.sh` (already done for the known offenders — see above).
- **App not answering right after `kobo-start.sh`** — a cold start (first boot, or right after `docker compose down`) can take a couple of minutes while KPI runs its startup checks/migrations. Check progress with `docker compose logs -f kpi`.

---

## Roadmap

1. ✅ Run KoboToolbox locally on macOS via Docker (this document).
2. ✅ Front the stack with nginx on a custom domain/subdomain, proxying to the local Compose stack.
3. ✅ Deploy to the production Ubuntu server, pointed at a real domain (`loyalminds.org`), secured with a real Let's Encrypt certificate, auto-deployed on push to `main`.

For full production architecture, HTTPS/Let's Encrypt internals, CI/CD, and the secrets inventory, see **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.
