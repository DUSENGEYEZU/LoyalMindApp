# Final config: terminates TLS and reverse-proxies everything to
# kobo-docker's own `nginx` service (reachable by service name on the shared
# kobo-fe-network, listening on port 80 internally - it does NOT publish to
# the host anymore, see scripts/compose-overrides.letsencrypt.yml). That
# inner nginx routes kf/kc/ee by the Host header, which is why this proxies
# to one target regardless of which subdomain the request came in on.
#
# Rendered by scripts/kobo_apply_env.py's ensure_letsencrypt() once a real
# certificate exists at /etc/letsencrypt/live/{{PRIMARY_DOMAIN}}/.
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

server {
    listen 443 ssl;
    server_name {{SERVER_NAMES}};
    server_tokens off;
    http2 on;

    ssl_certificate /etc/letsencrypt/live/{{PRIMARY_DOMAIN}}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{{PRIMARY_DOMAIN}}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # KoboToolbox form/media uploads can be large.
    client_max_body_size 100M;

    location / {
        proxy_pass http://nginx:80;
        proxy_set_header Host              $http_host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
