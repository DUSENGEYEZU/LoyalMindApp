#!/usr/bin/env python3
"""
Sync ../.env into kobo-install/.run.conf, re-render kobo-env/, then compile
the whole stack into a single ../docker-compose.yml - EVERY time this runs,
unconditionally. `.env` is the single source of truth: whatever is in it
right now gets applied, no flag to flip.

On a brand new machine (fresh clone of this repo, e.g. the Ubuntu server),
kobo-install/.run.conf won't exist yet - this script detects that and builds
a full config from kobo-install's own defaults + this .env. kobo-install
itself must already be cloned at kobo-install/ - ./scripts/kobo-start.sh
takes care of that before calling this script.

The compile step (last part of main()) merges kobo-docker's frontend/backend
compose files with scripts/compose-overrides.yml (this project's platform
pins/network/port patches) via explicit `-f` flags, and writes the fully-
resolved, single-file result to docker-compose.yml at the project root. That
file is what `docker compose up` actually reads - after running this script
once, plain `docker compose up`/`down`/`ps`/`logs` all just work, no flags,
no wrapper. (Explicit `-f` flags here, not Compose's COMPOSE_FILE env var -
COMPOSE_FILE intermittently drops the merged `image` field for one of
kobo-docker's YAML-anchored services; resolving the merge once and writing a
static file sidesteps that entirely.)

Idempotent: re-running with unchanged .env values is a fast no-op write
(reports "No values differ..."), so it's safe/cheap to call before every
`docker compose up`.

Run via ./scripts/kobo-start.sh - not usually invoked directly.
"""
import os
import re
import shutil
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
KOBO_INSTALL_DIR = os.path.join(PROJECT_ROOT, "kobo-install")
RUN_CONF_PATH = os.path.join(KOBO_INSTALL_DIR, ".run.conf")
LETSENCRYPT_TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "scripts/letsencrypt-templates")
NGINX_SSL_PROXY_DIR = os.path.join(PROJECT_ROOT, "nginx-ssl-proxy")

# ENV_VAR -> (run.conf key, type) — type is one of: str, bool, int
MAPPING = {
    "KOBO_PUBLIC_DOMAIN_NAME": ("public_domain_name", str),
    "KOBO_KPI_SUBDOMAIN": ("kpi_subdomain", str),
    "KOBO_KC_SUBDOMAIN": ("kc_subdomain", str),
    "KOBO_EE_SUBDOMAIN": ("ee_subdomain", str),
    "KOBO_USE_HTTPS": ("https", bool),
    "KOBO_SUPERUSER_USERNAME": ("super_user_username", str),
    "KOBO_SUPERUSER_PASSWORD": ("super_user_password", str),
    "POSTGRES_USER": ("postgres_user", str),
    "POSTGRES_PASSWORD": ("postgres_password", str),
    "POSTGRES_REPLICATION_PASSWORD": ("postgres_replication_password", str),
    "KPI_POSTGRES_DB": ("kpi_postgres_db", str),
    "KC_POSTGRES_DB": ("kc_postgres_db", str),
    "POSTGRES_PORT": ("postgresql_port", str),
    "MONGO_ROOT_USERNAME": ("mongo_root_username", str),
    "MONGO_ROOT_PASSWORD": ("mongo_root_password", str),
    "MONGO_USER_USERNAME": ("mongo_user_username", str),
    "MONGO_USER_PASSWORD": ("mongo_user_password", str),
    "MONGO_PORT": ("mongo_port", str),
    "REDIS_PASSWORD": ("redis_password", str),
    "REDIS_MAIN_PORT": ("redis_main_port", str),
    "REDIS_CACHE_PORT": ("redis_cache_port", str),
    "DJANGO_SECRET_KEY": ("django_secret_key", str),
    "DJANGO_SESSION_COOKIE_AGE": ("django_session_cookie_age", int),
    "ENKETO_API_TOKEN": ("enketo_api_token", str),
    "ENKETO_ENCRYPTION_KEY": ("enketo_encryption_key", str),
    "ENKETO_LESS_SECURE_ENCRYPTION_KEY": ("enketo_less_secure_encryption_key", str),
    "SMTP_HOST": ("smtp_host", str),
    "SMTP_PORT": ("smtp_port", str),
    "SMTP_USER": ("smtp_user", str),
    "SMTP_PASSWORD": ("smtp_password", str),
    "SMTP_USE_TLS": ("smtp_use_tls", bool),
    "DEFAULT_FROM_EMAIL": ("default_from_email", str),
    "KOBO_USE_BACKUP": ("use_backup", bool),
}


def load_dotenv(path):
    values = {}
    if not os.path.isfile(path):
        return values
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.strip().startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def coerce(value, type_):
    if type_ is bool:
        return value.strip().lower() in ("1", "true", "yes", "on")
    if type_ is int:
        return int(value)
    return value


def apply_mapping(run_conf, env):
    """Overlay .env values onto a run.conf dict (in place). Returns list of changes."""
    changed = []

    def set_key(conf_key, new_value):
        if run_conf.get(conf_key) != new_value:
            changed.append((conf_key, run_conf.get(conf_key), new_value))
        run_conf[conf_key] = new_value

    for env_key, (conf_key, type_) in MAPPING.items():
        if env_key in env:
            set_key(conf_key, coerce(env[env_key], type_))

    install_type = env.get("KOBO_INSTALL_TYPE", "workstation").strip().lower()
    set_key("local_installation", install_type == "workstation")

    # Always force kobo-install's OWN `use_letsencrypt` flag off - it's a
    # different, same-named-by-coincidence setting from our KOBO_USE_
    # LETSENCRYPT env var. kobo-install defaults it to True for any fresh
    # server-type install, which makes its nginx template comment out the
    # `ports: 80:80` line entirely (deferring to ITS OWN nginx-certbot
    # proxy, which we never stand up - we have a separate, from-scratch
    # equivalent, see compose-overrides.letsencrypt.yml). Left at its
    # default, kobo-docker's nginx ends up with no port published at all
    # and nothing else fills the gap.
    set_key("use_letsencrypt", False)

    nginx_port = env.get("KOBO_NGINX_PORT")
    if nginx_port:
        set_key("exposed_nginx_docker_port", nginx_port)
        set_key("nginx_proxy_port", nginx_port)

    interface_ip = env.get("KOBO_LOCAL_INTERFACE_IP")
    if interface_ip:
        set_key("local_interface_ip", interface_ip)
        set_key("primary_backend_ip", interface_ip)

    kobodocker_path = env.get("KOBO_KOBODOCKER_PATH")
    if kobodocker_path:
        # Resolve relative to PROJECT_ROOT (where .env lives), not
        # KOBO_INSTALL_DIR - kobo-docker is a sibling of kobo-install, not
        # nested inside it.
        set_key(
            "kobodocker_path",
            os.path.realpath(os.path.join(PROJECT_ROOT, kobodocker_path)),
        )

    return changed


def is_true(env, key):
    return env.get(key, "").strip().lower() in ("1", "true", "yes", "on")


def compile_compose(env):
    """Merge kobo-docker's compose files + our overrides into one static,
    fully-resolved docker-compose.yml at the project root, via explicit -f
    flags (reliable) rather than Compose's COMPOSE_FILE env var (flaky for
    this stack's YAML-anchored services).

    scripts/compose-overrides.letsencrypt.yml is only appended when
    KOBO_USE_LETSENCRYPT=true - local dev never sees it, so kobo-docker's own
    nginx keeps its normal host port 80 exactly as before."""
    out_path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
    files = [
        "kobo-docker/docker-compose.backend.yml",
        "kobo-docker/docker-compose.backend.override.yml",
        "kobo-docker/docker-compose.frontend.yml",
        "kobo-docker/docker-compose.frontend.override.yml",
        "scripts/compose-overrides.yml",
    ]
    if is_true(env, "KOBO_USE_LETSENCRYPT"):
        files.append("scripts/compose-overrides.letsencrypt.yml")

    header = (
        "# ==============================================================================\n"
        "# GENERATED FILE - do not edit by hand.\n"
        "# Compiled by scripts/kobo_apply_env.py from:\n"
        + "".join(f"#   {f}\n" for f in files)
        + "# Edit scripts/compose-overrides.yml (or .letsencrypt.yml), then re-run\n"
        "# ./scripts/kobo-start.sh.\n"
        "# ==============================================================================\n"
    )
    args = ["docker", "compose", "--env-file", ENV_PATH]
    for f in files:
        args += ["-f", os.path.join(PROJECT_ROOT, f)]
    args.append("config")

    result = subprocess.run(args, capture_output=True, text=True, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"ERROR compiling docker-compose.yml:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(result.stdout)
    print(f"Compiled {out_path}")


_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$")


def _letsencrypt_domains(env):
    domain = env["KOBO_PUBLIC_DOMAIN_NAME"]
    subs = [
        env.get("KOBO_KPI_SUBDOMAIN", "kf"),
        env.get("KOBO_KC_SUBDOMAIN", "kc"),
        env.get("KOBO_EE_SUBDOMAIN", "ee"),
    ]
    domains = [f"{sub}.{domain}" for sub in subs]
    # Bare apex domain too, so https://{domain} (not just https://kf.{domain})
    # is covered by the certificate - see render_nginx_ssl_proxy_conf() for
    # how it's proxied to the same KPI frontend. Appended, not prepended:
    # domains[0] (the primary/cert-directory name) must stay kf.{domain} for
    # back-compat with certs issued before this was added.
    domains.append(domain)
    # These get interpolated into shell/container commands and nginx config
    # below (cert_exists, cert_is_staging, certbot -d, app.conf), so reject
    # anything that isn't a plain hostname up front rather than letting a
    # malformed .env value reach any of those.
    for d in domains:
        if not _HOSTNAME_RE.match(d):
            print(f"ERROR: {d!r} is not a valid hostname (check KOBO_PUBLIC_DOMAIN_NAME "
                  "and the subdomain vars in .env).", file=sys.stderr)
            sys.exit(1)
    return domains, domains[0]  # (all domains, primary/first domain)


def render_nginx_ssl_proxy_conf(env, bootstrap):
    """Render nginx-ssl-proxy/conf/app.conf from the bootstrap or final
    template. Never hand-edit the output - it's overwritten every run."""
    domains, primary_domain = _letsencrypt_domains(env)
    apex_domain = env["KOBO_PUBLIC_DOMAIN_NAME"]
    kf_domain = f'{env.get("KOBO_KPI_SUBDOMAIN", "kf")}.{apex_domain}'
    # Everything except kf.<domain> - kf redirects to the apex domain (see
    # final.conf.tpl), so it gets its own dedicated server block instead.
    proxy_server_names = [d for d in domains if d != kf_domain]

    template_name = "bootstrap.conf.tpl" if bootstrap else "final.conf.tpl"
    with open(os.path.join(LETSENCRYPT_TEMPLATES_DIR, template_name), encoding="utf-8") as f:
        content = f.read()
    content = content.replace("{{SERVER_NAMES}}", " ".join(domains))
    content = content.replace("{{PRIMARY_DOMAIN}}", primary_domain)
    content = content.replace("{{APEX_DOMAIN}}", apex_domain)
    content = content.replace("{{KF_DOMAIN}}", kf_domain)
    content = content.replace("{{PROXY_SERVER_NAMES}}", " ".join(proxy_server_names))

    conf_dir = os.path.join(NGINX_SSL_PROXY_DIR, "conf")
    os.makedirs(conf_dir, exist_ok=True)
    with open(os.path.join(conf_dir, "app.conf"), "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Rendered nginx-ssl-proxy/conf/app.conf ({'bootstrap' if bootstrap else 'final'})")


def cert_exists(primary_domain):
    """Check certificate existence via a container, not the host
    filesystem - certbot locks `live/`/`archive/` down to 0700 root-only
    (correctly, to protect the private key), which the non-root deploy
    user can't stat directly. os.path.isfile() would just silently return
    False there every time, making every future deploy think no cert
    exists and re-request one - risking Let's Encrypt's rate limits."""
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "--entrypoint", "test", "certbot",
         "-f", f"/etc/letsencrypt/live/{primary_domain}/fullchain.pem"],
        cwd=PROJECT_ROOT, capture_output=True,
    )
    return result.returncode == 0


def cert_is_staging(primary_domain):
    """True if the on-disk certificate was issued by Let's Encrypt's
    staging CA. cert_exists() only checks presence, not which CA issued
    it - without this, flipping KOBO_LETSENCRYPT_STAGING from true to
    false would hit the idempotent early-return path below and silently
    keep serving the old untrusted staging cert forever, since certbot's
    own `certonly` is a no-op when a cert already exists and isn't near
    expiry."""
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "--entrypoint", "openssl", "certbot",
         "x509", "-in", f"/etc/letsencrypt/live/{primary_domain}/fullchain.pem",
         "-noout", "-issuer"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    return "STAGING" in result.stdout


def cert_domains(primary_domain):
    """DNS SANs on the on-disk certificate for primary_domain (empty set if
    it can't be read). cert_exists() only checks presence, not which names
    the cert actually covers - without this, adding a new domain to .env
    (e.g. the bare apex domain) would hit the idempotent early-return path
    below and never actually get added to the certificate."""
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "--entrypoint", "openssl", "certbot",
         "x509", "-in", f"/etc/letsencrypt/live/{primary_domain}/fullchain.pem",
         "-noout", "-ext", "subjectAltName"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    names = set()
    for part in result.stdout.replace("\n", ",").split(","):
        part = part.strip()
        if part.startswith("DNS:"):
            names.add(part[len("DNS:"):])
    return names


def ensure_letsencrypt(env):
    """Idempotent: does nothing once a real certificate already exists (the
    long-running `certbot` service in compose-overrides.letsencrypt.yml
    handles renewal from then on). Only the first-ever run does the
    dummy-cert -> real-cert bootstrap dance, so this is safe to call on
    every ./scripts/kobo-start.sh without re-requesting certificates or
    hitting Let's Encrypt's rate limits."""
    if not is_true(env, "KOBO_USE_LETSENCRYPT"):
        return

    email = env.get("KOBO_LETSENCRYPT_EMAIL", "").strip()
    if not email:
        print(
            "ERROR: KOBO_USE_LETSENCRYPT=true but KOBO_LETSENCRYPT_EMAIL is "
            "not set in .env.", file=sys.stderr,
        )
        sys.exit(1)

    domains, primary_domain = _letsencrypt_domains(env)
    certbot_conf_dir = os.path.join(NGINX_SSL_PROXY_DIR, "data/certbot/conf")
    certbot_www_dir = os.path.join(NGINX_SSL_PROXY_DIR, "data/certbot/www")
    os.makedirs(certbot_conf_dir, exist_ok=True)
    os.makedirs(certbot_www_dir, exist_ok=True)

    for fname in ("options-ssl-nginx.conf", "ssl-dhparams.pem"):
        dest = os.path.join(certbot_conf_dir, fname)
        if not os.path.isfile(dest):
            shutil.copyfile(os.path.join(LETSENCRYPT_TEMPLATES_DIR, fname), dest)

    want_staging = is_true(env, "KOBO_LETSENCRYPT_STAGING")
    existing = cert_exists(primary_domain)
    # A cert whose CA doesn't match what .env now asks for (typically:
    # staging cert left over from initial bootstrap, KOBO_LETSENCRYPT_
    # STAGING flipped to false since) needs a forced reissue - see
    # cert_is_staging()'s docstring for why the plain `existing` check
    # alone isn't enough.
    stale_ca = existing and cert_is_staging(primary_domain) != want_staging
    # Likewise for a domain .env now asks for (e.g. the apex domain added
    # after the cert was first issued) that the on-disk cert doesn't cover.
    missing_domains = set(domains) - cert_domains(primary_domain) if existing else set()
    needs_reissue = stale_ca or bool(missing_domains)

    if existing and not needs_reissue:
        # Already issued with the right CA; just make sure the final (not
        # bootstrap) conf is in place, in case app.conf was somehow missing
        # or stale. Rewriting the file alone isn't enough - nginx doesn't
        # notice bind-mounted config changes on its own, so an already-
        # running nginx_ssl_proxy would otherwise keep serving whatever
        # config (e.g. bootstrap-only, no port 443 block) it last loaded at
        # startup. `up -d` is a no-op if already running with this same
        # image/spec; the reload is what actually picks up the new file.
        render_nginx_ssl_proxy_conf(env, bootstrap=False)
        subprocess.run(["docker", "compose", "up", "-d"], cwd=PROJECT_ROOT, check=True)
        subprocess.run(
            ["docker", "compose", "exec", "-T", "nginx_ssl_proxy", "nginx", "-s", "reload"],
            cwd=PROJECT_ROOT,
        )
        return

    run = lambda args: subprocess.run(args, cwd=PROJECT_ROOT, check=True)

    if needs_reissue:
        # nginx_ssl_proxy is already up and already serving the final conf
        # (which includes the port-80 ACME-challenge location), so the
        # bootstrap dance isn't needed here - just re-request the cert.
        reasons = []
        if stale_ca:
            reasons.append(
                f"issued by the {'staging' if not want_staging else 'production'} CA but "
                f"KOBO_LETSENCRYPT_STAGING={'true' if want_staging else 'false'} now"
            )
        if missing_domains:
            reasons.append(f"missing domain(s) {', '.join(sorted(missing_domains))}")
        print(f"\nExisting certificate for {primary_domain} needs reissuing "
              f"({'; '.join(reasons)})...")
    else:
        print(f"\nNo certificate yet for {primary_domain} - running Let's Encrypt bootstrap...")
        render_nginx_ssl_proxy_conf(env, bootstrap=True)

        # Full `up -d`, not just `up -d nginx_ssl_proxy` - kobo-docker's own
        # `nginx` service also needs recreating here, since this is what
        # actually applies its `ports: []` override (releasing host port 80) so
        # nginx_ssl_proxy can bind it instead. Compose won't recreate a service
        # just because a *different*, explicitly-named service was targeted.
        run(["docker", "compose", "up", "-d"])
        time.sleep(3)  # give nginx a moment to start listening on 80

    certbot_args = [
        # --entrypoint overrides the service's default entrypoint (a renewal
        # loop that ignores its arguments) so this one-shot `certonly` call
        # actually runs instead of silently starting the loop again.
        "docker", "compose", "run", "--rm", "--entrypoint", "certbot",
        "certbot", "certonly",
        "--webroot", "-w", "/var/www/certbot",
        "--rsa-key-size", "4096",
        "--email", email,
        "--agree-tos", "--non-interactive",
    ]
    if want_staging:
        certbot_args.append("--staging")
    if needs_reissue:
        # --force-renewal: otherwise certbot sees a still-valid cert for the
        # same domains and silently no-ops ("not yet due for renewal") -
        # needed for the stale_ca case. --expand: required non-interactively
        # whenever the requested domain set is a superset of what the
        # existing cert lineage covers (the missing_domains case) - without
        # it certbot errors asking to confirm with --expand or --duplicate.
        certbot_args += ["--force-renewal", "--expand"]
    for d in domains:
        certbot_args += ["-d", d]

    result = subprocess.run(certbot_args, cwd=PROJECT_ROOT)
    if result.returncode != 0 or not cert_exists(primary_domain):
        print(
            "ERROR: certbot did not issue a certificate. Common causes: DNS "
            f"for {', '.join(domains)} doesn't point at this server yet, or "
            "ports 80/443 aren't reachable from the public internet. "
            "nginx_ssl_proxy is left running in bootstrap mode so you can "
            "retry once that's fixed (just re-run this script).",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Certificate issued for {primary_domain}. Switching to the final config...")
    render_nginx_ssl_proxy_conf(env, bootstrap=False)
    run(["docker", "compose", "exec", "-T", "nginx_ssl_proxy", "nginx", "-s", "reload"])
    print("HTTPS is live.")


def print_changes(changed):
    print(f"Applied {len(changed)} change(s) to .run.conf:")
    for conf_key, old, new in changed:
        is_secret = any(s in conf_key for s in ("password", "secret", "key"))
        old_display = "***" if is_secret else old
        new_display = "***" if is_secret else new
        print(f"  - {conf_key}: {old_display!r} -> {new_display!r}")


def main():
    if not os.path.isdir(KOBO_INSTALL_DIR):
        print(
            f"ERROR: {KOBO_INSTALL_DIR} does not exist. "
            "./scripts/kobo-start.sh clones it automatically - run this "
            "script through that, not directly, on a fresh machine.",
            file=sys.stderr,
        )
        return 1

    env = load_dotenv(ENV_PATH)
    if not env:
        print(f"No .env found at {ENV_PATH} - nothing to do.")
        return 0

    fresh_machine = not os.path.isfile(RUN_CONF_PATH)

    # Import kobo-install's own Config/Setup/Template - reused rather than
    # reimplemented so we stay byte-compatible with whatever it expects.
    sys.path.insert(0, KOBO_INSTALL_DIR)
    cwd = os.getcwd()
    os.chdir(KOBO_INSTALL_DIR)
    try:
        from helpers.config import Config
        from helpers.setup import Setup
        from helpers.template import Template

        config = Config()
        if fresh_machine:
            print("No kobo-install/.run.conf yet - building fresh config from "
                  "kobo-install's defaults + .env ...")
            run_conf = config.get_upgraded_dict()  # template merged with {} (nothing read yet)
        else:
            run_conf = config.get_dict()

        changed = apply_mapping(run_conf, env)
        config.set_config(run_conf)
        config.write_config()  # sets date_created/date_modified correctly, like kobo-install itself does

        if not changed:
            print("No values differ from current .run.conf.")
        else:
            print_changes(changed)

        # kobo-install's own wizard normally creates kobodocker_path and
        # writes its `.uniqid` file *before* ever calling clone_kobodocker
        # (which unconditionally tries to move that file out of the way,
        # clone, then move it back). We bypass that wizard, so replicate
        # just those two steps here - otherwise clone_kobodocker crashes
        # with FileNotFoundError on a truly fresh machine (only surfaces
        # when kobo-docker/ doesn't already exist, e.g. a brand new server;
        # local dev never hit this because kobo-docker/ was already present
        # from earlier testing).
        os.makedirs(run_conf["kobodocker_path"], exist_ok=True)
        config.write_unique_id()

        # Clones kobo-docker if missing, then renders kobo-env/ from the
        # config we just wrote.
        Setup.clone_kobodocker(config)
        Template.render(config, force=True)
    finally:
        os.chdir(cwd)

    local_installation = run_conf.get("local_installation")
    hosts_sensitive_keys = {"public_domain_name", "kpi_subdomain", "kc_subdomain", "ee_subdomain", "local_interface_ip"}
    if local_installation and (fresh_machine or any(k in hosts_sensitive_keys for k, _, _ in changed)):
        print(
            "\nNOTE: this is a Workstation install with a domain/IP that may "
            "not be in /etc/hosts yet. If the app doesn't resolve, add the "
            "domain/subdomains to /etc/hosts (see kobo-install's own "
            "`python3 run.py --setup` for the automated version of this step, "
            "which needs sudo)."
        )

    compile_compose(env)
    ensure_letsencrypt(env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
