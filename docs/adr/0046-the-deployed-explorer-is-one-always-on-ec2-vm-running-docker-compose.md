---
status: proposed (2026-11-05; rewritten for EC2 2026-11-06)
---

# The deployed explorer is one always-on EC2 VM running Docker Compose

The public deployment is **one AWS EC2 VM** (`t3.small`, `us-east-1`) running the whole explorer
with Docker Compose, the same app `explorer/run.sh` starts (ADR 0048):

- **`web`:** Caddy. It gets the Let's Encrypt certificate for `<ip>.sslip.io`, puts one basic-auth
  login in front of the whole site, serves the built client at `/` and proxies `/agent/*` and
  `/bootstrap/*`.
- **`agent`:** the explorer agent, serving the Workspaces in `EXPLORER_DATA_DIR` (a volume).
- **`bootstrap`:** the bootstrap server behind Settings.
- **`db`:** Postgres 18 with a restored dump of the `mart` schema. It is on the Compose network
  only and is never published.

`agent`, `bootstrap` and `web` share one network namespace, so both Python servers stay bound to
127.0.0.1 behind their Host guards, as they are locally. The files and steps are in `deploy/`.

The reasons:

- **The agent keeps live state in memory** (Results, Board views, tab state, the daily run
  count), so there must be exactly one instance and it must not scale to zero.
- **Answers stream over SSE for up to `TURN_TIMEOUT_S`**, which rules out serverless functions.
- **Speed is the top requirement**, so cold starts are out, and the database sits next to the
  agent rather than across a network.
- **One origin** means no CORS setup and one login for the client, the agent and Settings.
  ADR 0007 still holds: the browser calls the agent directly, through Caddy, with no app proxy.
- **Time.** The user was short on time on 2026-11-05. A VM they control, with plain Compose,
  was the quickest path that met the points above.

## Considered Options

- **One always-on Fly.io machine in `syd`, with a hosted Postgres (Supabase).** This was the first
  draft of this ADR. It had the same shape, but it needed a separate database host and a Fly
  volume for Workspaces, and it was dropped on 2026-11-05 for the VM.
- **Vercel (client) + Fly (agent) with CORS.** Two deploys and several CORS origins, and Vercel
  Hobby's terms allow only "non-commercial personal use".
- **Serverless functions (Vercel, Lambda).** Blocked by one SSE connection per Turn, in-process
  state and a tool loop that runs in one handler. Moving would be a rewrite, and it would give up
  prompt-cache warmth and add cold starts.
- **Render free / scale-to-zero containers.** These cost $0, but a cold start of 30–60 s goes
  against the speed requirement.

## Consequences

- **A restart drops what is in memory.** It drops Turns in flight and the daily run count
  (`MAX_TURNS_PER_DAY`). Saved Conversations survive, because they're stored on the `/data` volume
  and their Results re-run when shown (ADR 0040). Deploy at quiet times.
- **The VM can't ingest.** To refresh the data, re-ingest on the owner's machine, make a new
  `pg_dump -n mart` and restore it on the VM. A new database for Settings to offer is restored the
  same way, with its own read-only login in `deploy/connections.env`.
- **Cost:** about US$27/month plus model use, at us-east-1 list prices: the instance about US$15,
  the 100 GB gp3 disk about US$8 and the Elastic IP about US$3.60. These are derived, so check
  the bill.
- **The hostname depends on the IP** (`<ip>.sslip.io`). A new Elastic IP means a new hostname, a
  new certificate and a rebuild of `web`, because the client has the URL baked in.
- **Everyone with the login shares everything:** the same Workspaces, the same saved
  Conversations and one Settings session. The hostname is public through Certificate
  Transparency logs, so the login stays on. `MAX_TURNS_PER_DAY` is the second guard on model
  spend.
- **Static files come from one VM in `us-east-1`**, not from a CDN.
- **Backups are manual:** an EBS snapshot, or a Data Lifecycle Manager policy.
