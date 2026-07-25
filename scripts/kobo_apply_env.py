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
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
KOBO_INSTALL_DIR = os.path.join(PROJECT_ROOT, "kobo-install")
RUN_CONF_PATH = os.path.join(KOBO_INSTALL_DIR, ".run.conf")

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


def compile_compose():
    """Merge kobo-docker's compose files + our overrides into one static,
    fully-resolved docker-compose.yml at the project root, via explicit -f
    flags (reliable) rather than Compose's COMPOSE_FILE env var (flaky for
    this stack's YAML-anchored services)."""
    out_path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
    header = (
        "# ==============================================================================\n"
        "# GENERATED FILE - do not edit by hand.\n"
        "# Compiled by scripts/kobo_apply_env.py from:\n"
        "#   kobo-docker/docker-compose.backend.yml\n"
        "#   kobo-docker/docker-compose.backend.override.yml\n"
        "#   kobo-docker/docker-compose.frontend.yml\n"
        "#   kobo-docker/docker-compose.frontend.override.yml\n"
        "#   scripts/compose-overrides.yml  (this project's source of truth for overrides)\n"
        "# Edit scripts/compose-overrides.yml, then re-run ./scripts/kobo-start.sh.\n"
        "# ==============================================================================\n"
    )
    result = subprocess.run(
        [
            "docker", "compose",
            "--env-file", ENV_PATH,
            "-f", os.path.join(PROJECT_ROOT, "kobo-docker/docker-compose.backend.yml"),
            "-f", os.path.join(PROJECT_ROOT, "kobo-docker/docker-compose.backend.override.yml"),
            "-f", os.path.join(PROJECT_ROOT, "kobo-docker/docker-compose.frontend.yml"),
            "-f", os.path.join(PROJECT_ROOT, "kobo-docker/docker-compose.frontend.override.yml"),
            "-f", os.path.join(PROJECT_ROOT, "scripts/compose-overrides.yml"),
            "config",
        ],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        print(f"ERROR compiling docker-compose.yml:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(result.stdout)
    print(f"Compiled {out_path}")


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

    compile_compose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
