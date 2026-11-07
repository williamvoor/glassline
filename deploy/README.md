# Deploy: one VM

The explorer on a single Ubuntu VM with Docker Compose, the same as `explorer/run.sh` in
Workspace mode: the Workspace picker, saved Conversations, and Settings (New Workspace, Regenerate,
Delete). Containers:

| Service | What it does | Exposed |
|---|---|---|
| `net` | Owns the network namespace the next three share, and the published ports | 80, 443 |
| `web` | Caddy: HTTPS (Let's Encrypt), basic auth, the built client at `/`, `/agent/*` and `/bootstrap/*` proxied | via `net` |
| `agent` | The explorer agent, Workspace mode (`EXPLORER_DATA_DIR=/data`), Muse Spark | 127.0.0.1 only |
| `bootstrap` | The bootstrap server in Settings mode: drafts Workspaces with Muse Wonder, writes `/data`, reloads the agent | 127.0.0.1 only |
| `db` | Postgres 18 with the `austender` database's `mart` schema | compose network only |

The browser loads the client from `https://<host>/`, reaches the agent at `https://<host>/agent/`
and Settings at `https://<host>/bootstrap/`, all on one origin behind one login. The Workspace
listing and each Workspace's card name `https://<host>/agent/w/<name>` (`AGENT_PUBLIC_URL`),
because the browser's A2A client posts to the card's `url`.

Both Python servers bind to 127.0.0.1 and refuse a foreign `Host` header (their DNS-rebinding
guard), as they do locally. Sharing `net`'s namespace lets Caddy reach them on 127.0.0.1, and Caddy
sets `Host: localhost:<port>`. `net` is never restarted, so the others can restart one at a time.

**Everyone with the login shares everything:** the same Workspaces, the same saved Conversations,
and one Settings session (the bootstrap server holds one draft at a time). Give the login only to
people you trust with that. Never publish Postgres's port.

## Files

| File | Role |
|---|---|
| `Dockerfile` | Targets `agent` (Python, the repo layout kept) and `web` (Caddy + the built client) |
| `Dockerfile.dockerignore` | Keeps the build context (the repo root) to what the image copies |
| `compose.yaml` | The services above, plus `roles` (profile `setup`, one-off) |
| `Caddyfile` | Site, TLS, auth, proxy |
| `ensure_roles.py` | Runs austender's own `ensure_roles` against the VM's database |
| `workspaces.toml` | The first Workspace list (AusTender), copied into `/data` on the agent's first start |
| `connections.toml` | The first Connection list (AusTender's read-only login, by variable name), seeded the same way |
| `start-agent.sh` | The agent's command: seed the lists if missing, then start |
| `connections.env` | Optional: more read-only logins Settings can offer (git-ignored, made on the VM) |
| `.env.example` | Settings to copy to `deploy/.env` on the VM |
| `basic_auth.caddy` | The login, made on the VM (git-ignored) |
| `dumps/` | Where the database dump goes on the VM (git-ignored) |

## 1. AWS (once)

Running cost, roughly (us-east-1 list prices): t3.small about US$15/month, the 100 GB gp3 disk about
US$8 (about 15 GB is used; 30 GB would do), the Elastic IP about US$3.60: about US$27/month plus
model use. Stopping the instance stops the instance charge only. For a disk backup, snapshot the
volume in EC2 (or set up a Data Lifecycle Manager policy).

The instance used so far: `i-02c0dcaf1c06eefcb`, `t3.small` (2 GB, x86_64), Ubuntu 26.04, 100 GB gp3,
`us-east-1`, security group `glassline-web`.

1. **Security group:** 22 from your IP only; 80 and 443 from anywhere. Port 80 must be open for
   Let's Encrypt to issue the certificate and for the http→https redirect.
2. **Elastic IP:** allocate one and associate it with the instance *before* the first start of
   `web`. The hostname is `<ip>.sslip.io`, so a new IP means a new hostname, a new certificate and a
   rebuild of `web` (the client has the URL baked in).

```bash
aws ec2 allocate-address --region us-east-1 --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=Name,Value=glassline}]'
aws ec2 associate-address --region us-east-1 --instance-id i-02c0dcaf1c06eefcb --allocation-id <eipalloc-…>
```

## 2. Make the dump (your machine)

From the local AusTender database (`austender/`'s Compose container), schema `mart` only: it is all
the explorer reads (`explorer_ro` sees nothing else), and it is self-contained (no views, foreign
keys or functions reaching into `raw` or `core`). Custom format, compressed, streamed straight to
`deploy/dumps/` (git-ignored). Read-only on the local database; it takes seconds and is about 70 MB.

```bash
mkdir -p deploy/dumps
docker exec austender-postgres pg_dump -U austender_loader -Fc -n mart -d austender > deploy/dumps/austender.dump
```

The dump carries the tables, indexes, the table and column comments (the agent's schema prompt),
and the grants to `explorer_ro`. It doesn't carry roles: `roles` (step 6) makes them. To refresh the
VM's data later, re-ingest locally, redo this dump and the restore. The VM can't ingest by itself.

## 3. Copy the code and the dump to the VM (your machine)

The repo has no remote, so copy the files it needs (the paths the Dockerfile uses):

```bash
HOST=ubuntu@<elastic-ip>
KEY=~/.ssh/will2.pem
cd genUI
rsync -az --delete -e "ssh -i $KEY" --relative \
  --exclude node_modules --exclude .venv --exclude dist --exclude __pycache__ --exclude .env \
  ./austender/pyproject.toml ./austender/README.md ./austender/src \
  ./explorer/agent/pyproject.toml ./explorer/agent/uv.lock ./explorer/agent/README.md ./explorer/agent/src \
  ./explorer/bootstrap/server/pyproject.toml ./explorer/bootstrap/server/uv.lock \
  ./explorer/bootstrap/server/README.md ./explorer/bootstrap/server/src ./explorer/bootstrap/templates \
  ./explorer/catalog ./explorer/client ./datasets/austender \
  ./deploy/Dockerfile ./deploy/Dockerfile.dockerignore ./deploy/compose.yaml ./deploy/Caddyfile \
  ./deploy/ensure_roles.py ./deploy/workspaces.toml ./deploy/connections.toml ./deploy/start-agent.sh \
  ./deploy/.env.example ./deploy/README.md \
  "$HOST:genUI/"
ssh -i $KEY $HOST mkdir -p genUI/deploy/dumps
scp -i $KEY deploy/dumps/austender.dump "$HOST:genUI/deploy/dumps/austender.dump"
```

## 4. Prepare the VM (once, on the VM)

```bash
ssh -i ~/.ssh/will2.pem ubuntu@<elastic-ip>

# Docker, Compose v2 and BuildKit (the Dockerfile.dockerignore needs BuildKit)
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx
sudo usermod -aG docker ubuntu   # then log out and back in

# 2 GB of swap: the client build and the restore are tight in 2 GB of RAM
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Check: `docker compose version` and `docker buildx version` both print a version.

## 5. Settings and login (on the VM)

```bash
cd ~/genUI/deploy
cp .env.example .env && chmod 600 .env
openssl rand -hex 24    # run twice: one for LOADER_PASSWORD, one for EXPLORER_RO_PASSWORD
nano .env               # PUBLIC_HOST=<elastic-ip>.sslip.io, ACME_EMAIL, META_API_KEY, the two passwords
```

The login. The hostname is public (every certificate is listed in Certificate Transparency
logs, which bots watch), there is no lockout, and anyone who gets in spends your Meta key and can
use Settings, so make the password long and random (`openssl rand -base64 24`) and keep it
in a password manager. `caddy hash-password` prompts for it, so it doesn't land in shell history:

```bash
docker run --rm -it caddy:2.10.2 caddy hash-password   # type the password twice; it prints a $2a$… hash
nano basic_auth.caddy
```

with this content (a tab before each username; one line per person, so you can remove one):

```
basic_auth {
	<username> <the $2a$… hash>
}
```

The file must exist before the first `docker compose up`. Otherwise Docker creates an empty
*directory* named `basic_auth.caddy`, and Caddy fails on the import. If that happened: `rmdir
basic_auth.caddy`, write the file, then `docker compose up -d web`.

To change it later, rewrite the file and run `docker compose restart web`.

## 6. Build, restore the database, start (on the VM)

Swap (step 4) must be on before building. Build one target at a time, so the two builds don't
compete for 2 GB (`bootstrap` uses the `agent` image):

```bash
cd ~/genUI/deploy
docker compose build agent                  # a few minutes the first time
docker compose build web
docker compose up -d db
docker compose run --rm roles               # creates explorer_ro before the restore, so its GRANTs apply
```

The restore rebuilds `mart` (about 0.9 GB with its indexes), which takes a few minutes on this
instance. Run it in `tmux`, so a dropped SSH session doesn't kill it:

```bash
tmux new -s restore
docker compose exec -T db pg_restore -U austender_loader -d austender --exit-on-error -j 2 /dumps/austender.dump \
  > restore.log 2>&1; echo "pg_restore exit: $?" | tee -a restore.log
# detach with Ctrl-b d; reattach with `tmux attach -t restore`; follow with `tail -f restore.log`
```

`--exit-on-error` stops at the first error, so `pg_restore exit: 0` means it all went in. Then:

```bash
docker compose run --rm roles               # again: database-level grants and settings the dump doesn't carry
docker compose exec -T db vacuumdb -U austender_loader -d austender --analyze-only
docker compose exec -T db psql -U austender_loader -d austender -c "select count(*) from mart.contract"
docker compose up -d
```

**If the restore failed or was interrupted**, the database is half loaded and a rerun fails at once
(`schema "mart" already exists`). Start the database over. This deletes only the database volume:

```bash
docker compose stop db && docker compose rm -f db && docker volume rm glassline_pgdata
docker compose up -d db && docker compose run --rm roles   # then the restore again
```

Once the site works, the dump file can go: `rm dumps/austender.dump`.

Check:

```bash
docker compose ps                           # db healthy; net, agent, bootstrap and web up
docker compose logs agent | tail            # "seeded /data/workspaces.toml" once, "listening on http://127.0.0.1:10002"
docker compose logs bootstrap | tail        # "bootstrap: http://127.0.0.1:8765/ (Settings mode)"
docker compose logs web | grep -i certificate   # "certificate obtained successfully"
```

Then open `https://<elastic-ip>.sslip.io/`, log in, pick the AusTender Workspace, and ask a question.

## Workspaces and Settings

The Workspace list and every Workspace's files and saved Conversations live in the `explorer_data`
volume (`/data` in `agent` and `bootstrap`). The first start seeds the list from
`deploy/workspaces.toml` (AusTender, Connection `austender`) and the Connection list from
`deploy/connections.toml` (login variable `AUSTENDER_DATABASE_URL`); after that the volume's copies
are the ones in use and Settings edits them. A Workspace list from before Connections (entries
with `dsn_env`) is rewritten by Settings when it starts; until then those Workspaces show as
unavailable ("needs migration"), so check the picker after the first deploy with this change. AusTender is repo kind, so Settings can delete it but
can't recreate it: the agent re-adds it on its next start (`docker compose restart agent`) whenever
no Workspace is named `austender`. Back it up with
`docker run --rm -v glassline_explorer_data:/data -v "$PWD":/out alpine tar czf /out/explorer-data.tgz -C /data .`

**Add a database for New Workspace.** Settings offers the Connections and any declared
`*_DATABASE_URL` login no Connection names yet (choosing one makes it a Connection); several
Workspaces can share one. Users never type a connection string. Each must be a
read-only role (Settings refuses one that can write). Declare them in `deploy/connections.env`:

```bash
cd ~/genUI/deploy
touch connections.env && chmod 600 connections.env
nano connections.env        # e.g. MYDATA_DATABASE_URL=postgresql://<readonly-user>:<password>@<host>:5432/<db>
docker compose up -d agent bootstrap
```

The database must be reachable from the VM: on the compose network as `db` (another database in
this Postgres, with its own read-only role) or elsewhere. Never put `LOADER_PASSWORD` there.

**Spend:** drafting a Workspace (and its Boards) calls Muse Wonder, and the daily cap below does
not count it. Settings shows an estimate before a draft starts.

## HTTPS

Caddy gets the certificate from Let's Encrypt by itself on first start (HTTP-01 on port 80 or
TLS-ALPN on 443), renews it before expiry, and keeps it in the `caddy_data` volume, so restarts and
rebuilds don't re-issue it. If Let's Encrypt refuses (for example a rate limit on the shared
`sslip.io` domain), Caddy falls back to ZeroSSL on its own. Nothing to run by hand.

If issuance fails, `docker compose logs web` names the reason. The usual causes: port 80 or 443
closed in the security group, or `PUBLIC_HOST` not matching the instance's current public IP.

## Daily question cap

Meta documents no hard spend cap on the model key, so the agent has its own: `MAX_TURNS_PER_DAY`
(default 200) model runs per UTC day across all users and Workspaces (one shared count). A model run is a typed question or an Ask,
each up to `MAX_TOOL_ITERATIONS` model calls. Drill-downs and Boards use no model and are never
capped, and so are Settings' drafts (see above). Once the cap is reached, the site says so and appends `TURN_LIMIT_CONTACT` (set it in
`.env`, e.g. your GitHub link). The agent logs one warning when the cap is reached.

The count lives in memory. To reset it early: `docker compose restart agent`. To change the cap:
edit `MAX_TURNS_PER_DAY` in `.env`, then `docker compose up -d agent`.

## Day to day

```bash
docker compose logs -f agent                # follow the agent (never prints keys, DSNs or prompts)
docker compose restart agent                # or bootstrap, or web: each restarts alone (not net)
# Update the code: rerun the rsync in step 3 (without the dump), then
docker compose up -d --build
```

Stopping the instance is fine: everything restarts on boot (`restart: unless-stopped`), and with the
Elastic IP the hostname and certificate stay valid.

`docker compose down` keeps the database, the Workspaces and Conversations, and the certificates.
`down -v` **deletes all three**.

## Troubleshooting

| Symptom | Check |
|---|---|
| Browser asks for the login on every request | `basic_auth.caddy` exists and was mounted; restart `web` |
| `web` exits with an import error | `basic_auth.caddy` is a directory (see step 5) |
| Page loads, then "Cannot reach the agent" | `docker compose logs agent`; `/agent/workspaces` must list `https://<PUBLIC_HOST>/agent/w/<name>` URLs |
| A Workspace shows as unavailable | its reason in the picker; `login can write`: rerun `roles`; `… not set`: the variable is missing |
| Settings: no database to choose | declare a `*_DATABASE_URL` in `connections.env` (see "Workspaces and Settings") |
| A Workspace says "needs migration" | Settings rewrites old `dsn_env` entries when it starts: `docker compose restart bootstrap` |
| 403 "forbidden" from `/agent` or `/bootstrap` | the Host guard: check the Caddyfile's `header_up Host localhost:<port>` |
| `explorer: cannot connect to the database` | `db` healthy? `EXPLORER_RO_PASSWORD` matches what `roles` set (rerun it after changing it) |
| New IP or hostname | Update `PUBLIC_HOST`, then `docker compose up -d --build` (rebuilds the client's URL) |
