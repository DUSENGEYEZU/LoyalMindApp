# Final config: terminates TLS and reverse-proxies everything to
# kobo-docker's own `nginx` service (reachable by service name on the shared
# kobo-fe-network, listening on port 80 internally - it does NOT publish to
# the host anymore, see scripts/compose-overrides.letsencrypt.yml). That
# inner nginx routes kf/kc/ee by the Host header - the bare apex domain
# isn't one of its own server names, so requests for it are proxied through
# with the Host header rewritten to {{KF_DOMAIN}} (see the `map` below), so
# inner nginx treats https://{{APEX_DOMAIN}} exactly like the KPI frontend.
#
# Rendered by scripts/kobo_apply_env.py's ensure_letsencrypt() once a real
# certificate exists at /etc/letsencrypt/live/{{PRIMARY_DOMAIN}}/.

map $host $kobo_upstream_host {
    default             $host;
    {{APEX_DOMAIN}}      {{KF_DOMAIN}};
}

server {
    listen 80;
    server_name {{SERVER_NAMES}};
    server_tokens off;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# {{KF_DOMAIN}} is the internal/historical name for the KPI frontend - send
# users to the bare apex domain instead, that's the one they should see.
server {
    listen 443 ssl;
    server_name {{KF_DOMAIN}};
    server_tokens off;
    http2 on;

    ssl_certificate /etc/letsencrypt/live/{{PRIMARY_DOMAIN}}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{{PRIMARY_DOMAIN}}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    return 301 https://{{APEX_DOMAIN}}$request_uri;
}

server {
    listen 443 ssl;
    server_name {{PROXY_SERVER_NAMES}};
    server_tokens off;
    http2 on;

    ssl_certificate /etc/letsencrypt/live/{{PRIMARY_DOMAIN}}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{{PRIMARY_DOMAIN}}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # KoboToolbox form/media uploads can be large.
    client_max_body_size 100M;

    # Static training/user-guide site (docs-site/ in the repo, mounted
    # read-only in scripts/compose-overrides.letsencrypt.yml) - served
    # directly by this proxy, never reaches kobo-docker's own nginx/kpi.
    location = /docs {
        return 301 /docs/;
    }
    location /docs/ {
        alias /usr/share/nginx/docs/;
        index index.html;
        charset utf-8;
    }

    location / {
        proxy_pass http://nginx:80;
        proxy_set_header Host              $kobo_upstream_host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
