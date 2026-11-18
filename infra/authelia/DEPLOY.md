# Authelia — Stage 1 deploy (portal only, nothing enforced yet)

Goal: a working `auth.example.com` login portal with password + TOTP + passkey,
enrolled for both users. `merge.example.com` is NOT yet protected (that's Stage 2).
Everything here is **additive** — no existing site/route changes.

## 0. DNS (do first, manual — GoDaddy)
Add `A  auth  <reserved-ip>` for `example.com`. Verify: `dig +short auth.example.com`.

## 1. Secrets → /srv/memory/secrets/.env
Generate three secrets and append (quoted not needed; single tokens):
```bash
for k in AUTHELIA_SESSION_SECRET AUTHELIA_STORAGE_ENCRYPTION_KEY \
         AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET; do
  echo "$k=$(openssl rand -hex 32)"
done   # append the three lines to /srv/memory/secrets/.env
```

## 2. Config files → /srv/memory/authelia/
```bash
ssh memory 'mkdir -p /srv/memory/authelia'
rsync -av infra/authelia/configuration.yml infra/authelia/users_database.yml \
  memory:/srv/memory/authelia/
```
Then generate real password hashes (you choose the passwords) and edit
`users_database.yml` on the droplet, replacing the two placeholders:
```bash
ssh memory "docker run --rm authelia/authelia:4.38 \
  authelia crypto hash generate argon2 --password 'PICK_A_PASSWORD'"
# paste the $argon2id$... into /srv/memory/authelia/users_database.yml
```

## 3. docker-compose service
Add to `/srv/memory/docker-compose.yml` under `services:` (back it up first):
```yaml
  authelia:
    image: authelia/authelia:4.38
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
    environment:
      AUTHELIA_SESSION_SECRET: ${AUTHELIA_SESSION_SECRET}
      AUTHELIA_STORAGE_ENCRYPTION_KEY: ${AUTHELIA_STORAGE_ENCRYPTION_KEY}
      AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET: ${AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET}
    volumes:
      - /srv/memory/authelia:/config
    networks:
      - internal
      - web
```

## 4. Caddy — add the portal site (BACK UP THE CADDYFILE FIRST)
```bash
ssh memory 'cp /srv/memory/Caddyfile /srv/memory/Caddyfile.bak.$(date -u +%s)'
```
Append this block to `/srv/memory/Caddyfile` (additive — touches nothing else):
```
auth.example.com {
    reverse_proxy memory-authelia-1:9091
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
    }
}
```

## 5. Bring it up
```bash
ssh memory 'cd /srv/memory && docker compose --env-file secrets/.env up -d authelia && docker compose restart caddy'
ssh memory 'docker logs --tail 30 memory-authelia-1'   # expect "listening" + config valid
curl -I https://auth.example.com                          # 200 / login portal
```

## 6. Enroll (in a browser, both users)
- Visit `https://auth.example.com`, log in with username + the password you set.
- Register **TOTP** (authenticator app) and a **passkey**. The enrollment link is
  written to `/srv/memory/authelia/notification.txt` (read it via `ssh memory cat ...`)
  since there's no SMTP.

## Verify Stage 1
- `auth.example.com` shows the login portal and both users can complete password +
  TOTP + passkey enrollment.
- `merge.example.com` still works exactly as before (token login unchanged).
- All other sites (crm, engage, example.com marketing) unaffected.

→ Stage 2 (forward_auth on merge + shared-secret + auth.py hybrid) is separate.
