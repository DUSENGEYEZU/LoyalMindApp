#!/usr/bin/env python3
"""
Sync ../.env into kobo-install/.run.conf and re-render kobo-env/, then reset
the KOBO_SETUP_REQUIRED flag back to false.

This is the ONLY place `.env` values get applied to the actual KoboToolbox
config. It is a no-op (prints and exits 0) when KOBO_SETUP_REQUIRED=false.

Run via ./scripts/kobo-start.sh - not usually invoked directly.
"""
import json
import os
import re
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


def set_env_var(path, key, new_value):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    pattern = re.compile(rf"^{re.escape(key)}=")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={new_value}\n"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={new_value}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main():
    env = load_dotenv(ENV_PATH)
    if not env:
        print(f"No .env found at {ENV_PATH} - nothing to do.")
        return 0

    setup_required = env.get("KOBO_SETUP_REQUIRED", "false").strip().lower() in (
        "1", "true", "yes", "on"
    )
    if not setup_required:
        print("KOBO_SETUP_REQUIRED=false - using existing kobo-install config as-is.")
        return 0

    if not os.path.isfile(RUN_CONF_PATH):
        print(
            f"ERROR: {RUN_CONF_PATH} does not exist yet. Run the interactive "
            "kobo-install setup once first (see kobo-install/readme.md), THEN "
            "use this .env-driven flow for subsequent changes.",
            file=sys.stderr,
        )
        return 1

    with open(RUN_CONF_PATH, encoding="utf-8") as f:
        run_conf = json.load(f)

    changed = []
    for env_key, (conf_key, type_) in MAPPING.items():
        if env_key not in env:
            continue
        new_value = coerce(env[env_key], type_)
        if run_conf.get(conf_key) != new_value:
            changed.append((conf_key, run_conf.get(conf_key), new_value))
        run_conf[conf_key] = new_value

    install_type = env.get("KOBO_INSTALL_TYPE", "workstation").strip().lower()
    local_installation = install_type == "workstation"
    if run_conf.get("local_installation") != local_installation:
        changed.append(("local_installation", run_conf.get("local_installation"), local_installation))
    run_conf["local_installation"] = local_installation

    nginx_port = env.get("KOBO_NGINX_PORT")
    if nginx_port:
        for key in ("exposed_nginx_docker_port", "nginx_proxy_port"):
            if run_conf.get(key) != nginx_port:
                changed.append((key, run_conf.get(key), nginx_port))
            run_conf[key] = nginx_port

    interface_ip = env.get("KOBO_LOCAL_INTERFACE_IP")
    if interface_ip:
        for key in ("local_interface_ip", "primary_backend_ip"):
            if run_conf.get(key) != interface_ip:
                changed.append((key, run_conf.get(key), interface_ip))
            run_conf[key] = interface_ip

    with open(RUN_CONF_PATH, "w", encoding="utf-8") as f:
        json.dump(run_conf, f, indent=2, sort_keys=True)

    if not changed:
        print("KOBO_SETUP_REQUIRED=true but no values differ from current .run.conf.")
    else:
        print(f"Applied {len(changed)} change(s) to .run.conf:")
        for conf_key, old, new in changed:
            old_display = "***" if "password" in conf_key or "secret" in conf_key or "key" in conf_key else old
            new_display = "***" if "password" in conf_key or "secret" in conf_key or "key" in conf_key else new
            print(f"  - {conf_key}: {old_display!r} -> {new_display!r}")

    # Re-render kobo-env/ from the updated .run.conf using kobo-install's own
    # Template engine (safer than reimplementing its Jinja logic here).
    sys.path.insert(0, KOBO_INSTALL_DIR)
    cwd = os.getcwd()
    os.chdir(KOBO_INSTALL_DIR)
    try:
        from helpers.config import Config
        from helpers.setup import Setup
        from helpers.template import Template

        config = Config()
        Setup.clone_kobodocker(config)
        Template.render(config, force=True)
    finally:
        os.chdir(cwd)

    hosts_sensitive_keys = {"public_domain_name", "kpi_subdomain", "kc_subdomain", "ee_subdomain", "local_interface_ip"}
    if local_installation and any(k in hosts_sensitive_keys for k, _, _ in changed):
        print(
            "\nNOTE: domain/subdomain/IP changed and this is a Workstation "
            "install - you may need to update /etc/hosts. Re-run kobo-install's "
            "own setup (`python3 run.py --setup`) once to trigger that, or edit "
            "/etc/hosts by hand."
        )

    set_env_var(ENV_PATH, "KOBO_SETUP_REQUIRED", "false")
    print("\nKOBO_SETUP_REQUIRED reset to false in .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
