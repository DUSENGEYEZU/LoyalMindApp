---
noteId: "08a3d7f089b411f1b3d50b7ab8702a56"
tags: []

---

# LoyalMindApp — Technical Setup & Deployment Guide

This document explains **how this project is architected, how to run it locally, and how it's deployed to production**, in enough detail for another engineer to pick it up cold.

It intentionally does **not** contain real passwords, keys, or tokens — those live in `.env` (local, gitignored) and in GitHub Actions Secrets (production). See [Secrets inventory](#secrets-inventory) for where each one lives and how to get it from the project owner.

---

## 1. What this is

A **[KoboToolbox](https://www.kobotoolbox.org/)** deployment, rebranded as **LoyalMinds**, running as a single Docker Compose project. This repo does not contain KoboToolbox's source — it contains everything needed to *configure, build, and run* an instance:

- `kobo-install` and `kobo-docker` — official upstream repos, cloned automatically, gitignored (treat as build tooling, not project source)
- `scripts/` — this project's own orchestration: applies `.env`, compiles the final `docker-compose.yml`, and (in production) bootstraps HTTPS
- `branding/` — a small Docker build step that rebrands the official `kpi` image (text + logo)
- `.github/workflows/loyal-prod.yml` — CI/CD: push to `main` → auto-deploy to the production server

### Why a single Compose project (not kobo-install's default)

kobo-install normally runs frontend and backend as **two separate** Compose projects (`kobofe` / `kobobe`). This project merges everything — kobo-docker's 4 compose files plus `scripts/compose-overrides.yml` (and, in production, `scripts/compose-overrides.letsencrypt.yml`) — into **one** project (`COMPOSE_PROJECT_NAME=loyalmind-kobo`), resolved once via `docker compose ... config` and written to a static `docker-compose.yml`.

Why resolve it once instead of letting Compose merge multiple `-f` files at runtime: kobo-docker's compose files use a YAML anchor (`<<: *django`) across several services, and Compose's `COMPOSE_FILE`-driven multi-file merge intermittently drops the merged `image:` field for one of them (a different one each run). Pre-resolving with explicit `-f` flags sidesteps the bug entirely. **Never hand-edit `docker-compose.yml`** — it's regenerated on every `kobo-start.sh` run.

### Services (14 containers in production; 12 locally — no `nginx_ssl_proxy`/`certbot`)

| Service | Role |
|---|---|
| `nginx` | kobo-docker's own reverse proxy — routes `kf`/`kc`/`ee` subdomains by Host header to the right backend |
| `nginx_ssl_proxy` *(prod only)* | Terminates TLS, owns host ports 80/443, proxies to `nginx` internally |
| `certbot` *(prod only)* | Renews the Let's Encrypt certificate every 12h |
| `kpi` | KPI — the main frontend app (form builder, project management) |
| `worker`, `worker_kobocat`, `worker_low_priority`, `worker_long_running_tasks`, `beat` | Celery workers + scheduler for KPI/KoboCat async tasks |
| `postgres` | Two databases: `koboform` (KPI) and `kobocat` (KoboCat), with PostGIS |
| `mongo` | KPI's submission-data store |
| `redis_main`, `redis_cache` | Session/cache/broker |
| `enketo_express` | Renders/serves web forms (the `ee` subdomain) |

---

## 2. Configuration model: `.env` is the single source of truth

Every variable — domain, credentials, ports, HTTPS settings — lives in one `.env` file. **There are two independent copies, never shared:**

| | Local dev | Production |
|---|---|---|
| File location | `.env` in the repo root, on the developer's machine | Does not exist as a standalone file in the repo — its content lives in the `PROD_ENV_FILE` GitHub Actions secret, written to `~/loyalmind-app-prod/.env` on the server on every deploy |
| Tracked in git? | No — gitignored | No — never touches git; injected by CI |
| `KOBO_INSTALL_TYPE` | `workstation` | `server` |
| Domain | `kobo.local` (resolved via `/etc/hosts`) | `loyalminds.org` (real public DNS) |
| HTTPS | off | on, via Let's Encrypt |

`.env.example` is the tracked, secrets-blanked template — copy it to `.env` on a fresh checkout and fill in real values.

### `scripts/kobo_apply_env.py` — applied on every start, unconditionally

Run via `./scripts/kobo-start.sh` before every `docker compose up`. Every time, it:

1. Writes the current `.env` values into `kobo-install/.run.conf` (kobo-install's own config format — reusing kobo-install's `Config`/`Setup`/`Template` classes so the render stays byte-compatible with what it expects).
2. Re-renders `kobo-env/` (KoboToolbox's actual runtime config directory).
3. Compiles kobo-docker's compose files + `scripts/compose-overrides.yml` (+ `scripts/compose-overrides.letsencrypt.yml`, **only** when `KOBO_USE_LETSENCRYPT=true`) into `docker-compose.yml`.
4. If `KOBO_USE_LETSENCRYPT=true`: runs `ensure_letsencrypt()` — see [§4](#4-https--lets-encrypt-architecture).

It's idempotent — re-running with unchanged `.env` values is a fast no-op (`No values differ from current .run.conf.`).

**Key design point:** the Let's Encrypt overlay file is only ever added to the compose merge when `KOBO_USE_LETSENCRYPT=true`. Local dev's `.env` never sets that, so local dev's `nginx` keeps its normal `80:80` port publish and is completely unaffected by anything production-specific.

---

## 3. Local development setup

**Prerequisites:** Docker Desktop (Compose v2.20+), Python 3.12+, git, and on macOS the `netifaces` pip package (installed automatically by `kobo-start.sh` if missing).

```bash
git clone <this repo>
cd LoyalMindApp
cp .env.example .env        # then fill in real values (see .env.example's comments)
./scripts/kobo-start.sh
```

This clones `kobo-install` on first run, applies `.env`, starts all containers, and waits for the app to answer. Open the app at `http://kf.<KOBO_PUBLIC_DOMAIN_NAME>/` (default: `http://kf.kobo.local/`).

`./scripts/kobo-stop.sh` stops everything — data is untouched (PostgreSQL/MongoDB/Redis all use bind mounts under `kobo-docker/.vols/`, not ephemeral Docker volumes).

### `/etc/hosts` (macOS/workstation-specific)

For `KOBO_INSTALL_TYPE=workstation`, your browser needs `kf.kobo.local`/`kc.kobo.local`/`ee.kobo.local` to resolve somewhere. Point them at the loopback address - it's on the same machine as the containers' published port 80, so this works regardless of network:

```
127.0.0.1  kf.kobo.local kc.kobo.local ee.kobo.local
```

This is permanently stable - no LAN IP involved, so nothing to keep in sync when you change networks.

**Historical note:** earlier versions of this setup used the Mac's actual LAN IP for both `/etc/hosts` and a `KOBO_LOCAL_INTERFACE_IP` env var (needed so kobo-docker's containers could reach the host via `extra_hosts`). That LAN IP drifted every time the Mac changed networks (different Wi-Fi, VPN, router lease renewal), repeatedly breaking local dev with no other changes. Fixed for good in `kobo_apply_env.py`'s `apply_mapping()`: for `KOBO_INSTALL_TYPE=workstation`, `local_interface_ip`/`primary_backend_ip` are now hardcoded to Docker Compose's special `host-gateway` value, which Docker resolves to the host machine dynamically at container start - no IP to track, ever. `KOBO_LOCAL_INTERFACE_IP` no longer exists as a `.env` variable.

### Apple Silicon note

`scripts/compose-overrides.yml` pins several images (`kpi` + its Celery workers/beat, `enketo_express`, `postgres`) to `platform: linux/amd64`, since they publish no `arm64` build. These run under emulation on Apple Silicon Macs. **On an x86_64 server these lines must be removed** (see below — the CI workflow does this automatically).

---

## 4. HTTPS / Let's Encrypt architecture (production only)

kobo-install has its **own** built-in Let's Encrypt support (`use_letsencrypt` config flag + the `kobotoolbox/nginx-certbot` image). It is **not used here** — it assumes the old two-project (`kobofe`/`kobobe`) layout and hardcodes the network name `kobofe_kobo-fe-network`, which doesn't exist in this project's single merged project (whose network is `loyalmind-kobo_kobo-fe-network`). `kobo_apply_env.py` unconditionally forces kobo-install's own flag **off** (`use_letsencrypt: False` in `.run.conf`) to stop it from silently unpublishing kobo-docker's own nginx port 80.

Instead, this project has a **from-scratch equivalent**, defined in `scripts/compose-overrides.letsencrypt.yml` (only merged in when `KOBO_USE_LETSENCRYPT=true`):

- `nginx` (kobo-docker's own): its `ports: 80:80` is reset (`ports: !reset []` — plain `ports: []` is a no-op on modern Compose, it merges/concatenates rather than replaces an inherited list) so the host ports are free for the proxy below.
- `nginx_ssl_proxy` (new): owns host ports 80 + 443, terminates TLS, reverse-proxies to kobo-docker's own `nginx` internally over the shared network.
- `certbot` (new): renews the certificate on a 12h loop.

Config for `nginx_ssl_proxy` is rendered from `scripts/letsencrypt-templates/{bootstrap,final}.conf.tpl` by `render_nginx_ssl_proxy_conf()` — **never hand-edit `nginx-ssl-proxy/conf/app.conf` directly**, it's overwritten every run.

### The bootstrap → real cert dance (`ensure_letsencrypt()` in `kobo_apply_env.py`)

1. **No cert yet:** render the *bootstrap* config (HTTP-only, answers the ACME `http-01` challenge + a friendly placeholder page), bring everything up, request the cert via `certbot certonly --webroot`, then switch to the *final* config (HTTPS termination + reverse proxy) and reload nginx.
2. **Cert already exists and is current:** no-op except re-asserting the final config is loaded (idempotent — safe to run on every deploy).
3. **Cert exists but is stale** — detected two ways, both handled by forcing a reissue with `--force-renewal --expand`:
   - **Wrong CA** (`cert_is_staging()`): `KOBO_LETSENCRYPT_STAGING` flipped from `true` to `false` since the cert was issued. Certificate presence alone (`cert_exists()`) doesn't tell you *which* CA issued it — a plain existence check would silently keep serving the untrusted staging cert forever.
   - **Missing domain** (`cert_domains()`): `.env` now asks for a domain (e.g. the apex domain, added after initial setup — see [§5](#5-domainnetwork-topology)) that the on-disk cert doesn't cover as a SAN.

Certificate/CA/domain-coverage checks run **inside a throwaway `certbot` container** (`docker compose run --rm --entrypoint ...`), not via host filesystem checks — certbot locks `live/`/`archive/` to `0700 root`-only (correctly, to protect the private key), which the non-root deploy user can't `stat()` directly; a host-side check would silently and permanently think no cert exists.

**Rate limit safety:** always validate a fresh setup with `KOBO_LETSENCRYPT_STAGING=true` first. Flip to `false` only once a staging cert issues successfully.

---

## 5. Domain/network topology (production)

```
Browser
  │
  ▼  DNS: loyalminds.org, kf./kc./ee.loyalminds.org  →  <public IP>  (DigitalOcean-managed zone)
  │
  ▼  NAT/port-forward on the RHA network:  <public IP>:80/443  →  10.10.80.75:80/443
  │
  ▼  nginx_ssl_proxy  (TLS termination, owns host 80+443)
  │     • kf.loyalminds.org           → 301 redirect → https://loyalminds.org
  │     • loyalminds.org, kc., ee.    → proxy_pass → nginx:80 (internal)
  │       (apex domain's Host header is rewritten to kf.loyalminds.org via an
  │        nginx `map`, so the inner nginx's Host-based routing treats a bare
  │        loyalminds.org request exactly like the KPI frontend)
  │
  ▼  nginx  (kobo-docker's own, internal-only — no host port)
        • kf./bare apex → kpi
        • kc.            → kobocat (via kpi)
        • ee.            → enketo_express
```

**Why `kf.` redirects to the bare apex domain:** so end users see and share `https://loyalminds.org` rather than the internal `kf.` name. `kc.`/`ee.` are left as direct subdomains — they're API/form-rendering endpoints, not meant to be browsed directly by end users, and other parts of the stack reference them by their subdomain name internally.

The production server itself (`10.10.80.75`) is **only reachable via a VPN** onto the RHA private network (OpenConnect/Cisco AnyConnect-compatible) — see the CI workflow below for exactly how it connects.

### Adding a new domain/subdomain later

1. Add the DNS record (apex or subdomain → the public IP).
2. Add it to the relevant `.env` var (`KOBO_PUBLIC_DOMAIN_NAME` or a new `KOBO_*_SUBDOMAIN`), and to `_letsencrypt_domains()` in `kobo_apply_env.py` if it's not one of the 3 standard subdomains.
3. Re-run `kobo_apply_env.py` (or redeploy) — `cert_domains()` will detect the gap and reissue the certificate automatically with `--expand`.

---

## 6. CI/CD — `.github/workflows/loyal-prod.yml`

**Trigger:** every push to `main`, or manually via the GitHub Actions UI (**Actions** tab → **Deploy - Production (loyalminds.org)** → **Run workflow** — enabled by `workflow_dispatch:`).

**What it does, step by step:**

1. Checks out the repo (GitHub-hosted `ubuntu-latest` runner).
2. Installs `openconnect` + `vpnc-scripts`.
3. Connects to the RHA VPN: does a throwaway connection attempt to extract the server's certificate pin (`pin-sha256:...`), then reconnects for real with `--passwd-on-stdin --servercert <pin> --background`.
4. Writes the SSH deploy key from the `PROD_SSH_KEY` secret to disk (note: `printf '%s\n'`, not `'%s'` — see the inline comment; a missing trailing newline makes OpenSSH refuse to parse the key with a cryptic `error in libcrypto`).
5. Packages **only git-tracked files** (`git ls-files | tar -czf ...`) — this deliberately never touches gitignored server-side state (`kobo-install/`, `kobo-docker/`, `kobo-env/`, `docker-compose.yml`, `nginx-ssl-proxy/`), which is what makes redeploys safe without `rsync --delete` or similar.
6. Writes the `PROD_ENV_FILE` secret's content to a temp file.
7. Copies both to the server over `scp`.
8. SSHes in and: extracts the tarball, moves the fresh `.env` into place, strips the `platform: linux/amd64` lines from `scripts/compose-overrides.yml` (server-side copy only — those pins are Apple-Silicon-only and this server is native x86_64), then runs `./scripts/kobo-start.sh`.
9. Disconnects the VPN (`if: always()`, so this runs even on failure).

**Security note:** every secret is passed via a step's `env:` block and referenced as a shell variable (`$VAR`); none are interpolated directly into `run:` script text as `${{ secrets.X }}` — this avoids the secret value ever being echoed into shell history/logs via naive string interpolation.

---

## 7. Secrets inventory

None of these are in this document or in the repo. Get actual values from the project owner.

| Name | Where it lives | Used for |
|---|---|---|
| `VPN_SERVER`, `VPN_GROUP`, `VPN_USERNAME`, `VPN_PASSWORD` | GitHub Actions secret | Connecting to the RHA VPN to reach the private server |
| `PROD_SSH_HOST`, `PROD_SSH_USER` | GitHub Actions secret | SSH target on the production server |
| `PROD_SSH_KEY` | GitHub Actions secret | Private half of a dedicated deploy keypair (`~/.ssh/loyalmind_prod_deploy_key` locally); the matching public key is in the server's `~/.ssh/authorized_keys` for that user only |
| `PROD_ENV_FILE` | GitHub Actions secret | The entire production `.env` content — DB/queue/Django/Enketo secrets, superuser login, Let's Encrypt settings |
| Local `.env` | Developer's machine only, gitignored | Local dev credentials (separate from and unrelated to production's) |

**To view/update a GitHub Actions secret:** repo → **Settings** → **Secrets and variables** → **Actions**. Secrets are write-only once saved (can't be read back through the UI) — updating `PROD_ENV_FILE` doesn't redeploy by itself; it takes effect on the next push to `main` or manual `workflow_dispatch` run.

---

## 8. Troubleshooting reference

| Symptom | Likely cause / fix |
|---|---|
| Local app stops responding, nothing else changed | Was LAN IP drift historically — structurally fixed now (see [§3](#etchosts-macosworkstation-specific)). If it still happens, check `docker compose ps` for a crashed container instead |
| `docker exec ... cat /var/log/nginx/error.log` hangs forever | That log is symlinked to `/dev/stderr` per standard Docker nginx images — it's a live stream, `cat` blocks. Use `docker logs <container>` or check `nginx -t` / the rendered `app.conf` instead |
| VPN connects but the server's public DNS lookups fail from your own machine afterward | The RHA VPN pushes internal-only DNS servers (search domain `idc.bsc.rw`) that can't resolve public domains — this is a *client-side* testing artifact only, not a server problem. Check `networksetup -getdnsservers "<active interface>"` and clear it (`networksetup -setdnsservers "<interface>" Empty`) if it's stuck after disconnecting |
| `certbot did not issue a certificate` | DNS for the domain(s) doesn't point at the server yet, or ports 80/443 aren't reachable from the public internet. Verify both, then just re-run — `nginx_ssl_proxy` is left running so it's safe to retry |
| MongoDB crash-loops on the server | Kernel 6.19+/TCMalloc rseq-ABI incompatibility with floating `mongo:8.0` (MongoDB SERVER-121912) — this project pins `mongo:8.0.15` in `scripts/compose-overrides.yml`, confirmed to start cleanly on this server's kernel |
| `no matching manifest for linux/arm64/v8` | An image has no arm64 build — add `platform: linux/amd64` for that service in `scripts/compose-overrides.yml` (already done for known offenders; remove these same lines when deploying to an x86_64 server) |

See also **[docs/DATABASES.md](DATABASES.md)** for connecting to PostgreSQL/MongoDB/Redis directly.
