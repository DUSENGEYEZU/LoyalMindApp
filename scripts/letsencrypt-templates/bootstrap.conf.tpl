# Bootstrap config: HTTP-only, just enough to answer the ACME HTTP-01
# challenge so certbot can issue the real certificate. Rendered by
# scripts/kobo_apply_env.py's ensure_letsencrypt() before any real cert
# exists yet - replaced by final.conf.tpl once issuance succeeds.
server {
    listen 80;
    server_name {{SERVER_NAMES}};
    server_tokens off;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 200 'LoyalMinds - provisioning HTTPS certificate, try again shortly.';
        add_header Content-Type text/plain;
    }
}
