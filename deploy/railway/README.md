# Row-Bot on Railway: the template

This is the definition of Row-Bot's "Deploy on Railway" template. Railway
templates are composed in Railway's dashboard, so this file is the source the
template is built from by hand, and the record of why each value is what it is.
Each person who deploys it gets one service in their own Railway account,
running the unchanged Row-Bot image, used from a browser.

Facts about Railway below were read in Railway's own docs on 5 Nov 2026; links
are given so they can be checked again.

## Is there config as code for templates?

No. Railway documents no file format for templates: a template is composed in
the dashboard (**Workspace › Templates › New Template**) or generated from a
project (**Project Settings › Generate Template from Project**, or
`railway templates create`). Railway's per-service config as code
(`railway.json` / `railway.toml`) is read from a service's repository at
deploy, so it doesn't apply to an image service, and Railway has deprecated it
(no new services; existing files are no longer read from 1 Dec 2026). Railway's
newer infrastructure as code (`.railway/railway.ts`) describes a project, not a
template, and doesn't cover generated domains. So the template is composed by
hand from the table below.

## The service

| Setting | Value | Why |
|---|---|---|
| Template name | Row-Bot | |
| Service name | `row-bot` | |
| Source | Docker image `ghcr.io/siddsachar/row-bot:latest` | `latest` moves to each stable release only; a prerelease never moves it (`docs/RELEASING.md`, `container.yml`). Railway can watch a `ghcr.io` tag for new versions. |
| Start command | none (the image's own: `row-bot serve`) | |
| Volume | one, mount path `/data` (**right-click the service › Attach Volume**) | Everything Row-Bot keeps lives in `/data`. |
| Public networking | HTTP, a generated Railway domain (`*.up.railway.app`), **target port 8080** | The image listens on `ROW_BOT_PORT=8080`. |
| Healthcheck path | none | See [Health check](#health-check). |
| Restart policy | Railway's default (On Failure, 10 retries) | Row-Bot exits non-zero when it can't start, with the reason in the log. |
| Replica limits | memory **4 GB**, CPU **2 vCPU**, one replica | See [Memory limit](#memory-limit). |

## Variables

| Variable | Value | Why |
|---|---|---|
| `ROW_BOT_HOSTED_BY` | `railway` | Row-Bot's pages name Railway and use Railway's words: the service's Variables for the setup code, "Manage service", and on Updates "You update it in Railway". |
| `ROW_BOT_PUBLIC_URL` | `https://${{RAILWAY_PUBLIC_DOMAIN}}` | The exact address people open. Row-Bot allows that host name, and X and Google sign-ins return to it. |
| `ROW_BOT_SETUP_CODE` | `${{secret(12, "0123456789ABCDEFGHJKMNPQRSTVWXYZ")}}` | A new code for each deploy, from Row-Bot's own alphabet (Crockford's base32: no I, L, O or U). Row-Bot accepts 12 to 64 letters or digits (`access/setup_code.py`); 12 carry 60 bits. Railway's `secret()` takes a length and an alphabet; its default alphabet isn't documented, so it's given. |
| `RAILWAY_RUN_UID` | `0` | Railway mounts volumes as root and tells images with their own user to set this ([Volumes › Permissions](https://docs.railway.com/volumes)). The container then starts as root; Row-Bot's first step gives `/data` to uid 10001 and drops to 10001 for good, before it reads a secret, writes `/data` or starts a program. |
| `PORT` | `8080` | Railway's docs expect a web service to listen on `PORT`; setting it to 8080 keeps Railway's defaults and the domain's target port in agreement. Row-Bot listens on `ROW_BOT_PORT` and ignores `PORT`. |
| `ROW_BOT_TRUSTED_PROXY_CIDRS` | **not set yet: decided from the founder's check** | See [Proxy addresses](#proxy-addresses). |

**Never set `ROW_BOT_SECRET_STORE_KEY` in Railway.** On 21 Oct 2026 Railway
disclosed that an attacker may have decrypted some customers' environment
variables ([Railway's disclosure](https://blog.railway.com/p/security-disclosure-wednesday-october-21st),
[preliminary report](https://blog.railway.com/p/preliminary-report-october-security-incident)).
Without the variable, Row-Bot makes its own key on the volume at its first start
(`/data/secret-store-key/`, readable only by uid 10001) and Row-Bot's backups
leave it out. Railway's variables then hold nothing that opens the saved
sign-ins. The setup code works once: after the owner signs in, the value left in
the variables lets nobody in. A new code the owner sets to recover access works
once, until it's used.

Leave `ROW_BOT_INSTALL_COUNT` unset (install stats on, the owner can turn them
off in **Settings › System › Privacy**), and don't set `ROW_BOT_ALLOWED_HOSTS`:
`ROW_BOT_PUBLIC_URL` alone allows the service's own host name.

## Root, the volume and uid 10001

Railway starts the container as root (`RAILWAY_RUN_UID=0`) on a volume whose
top root owns, with a `lost+found` in it. Inside its own image
(`ROW_BOT_CONTAINERIZED=1`, server mode), Row-Bot started as root:

1. makes `/data` and everything in it 10001's, only while the top of `/data`
   isn't 10001's already (so once, at the first start), top last, links never
   followed, nothing on another file system touched, and `/data` becomes 0700;
2. clears supplementary groups, sets gid and then uid to 10001 (real, effective
   and saved), and checks root can't come back;
3. makes the image's own folders under `/data` (`tmp`, the caches) as 10001.

Any failure stops the start with `Row-Bot didn't start: …`. The deploy log shows
`Started as root in its container: Row-Bot gave /data to uid 10001 and runs as
uid 10001` at the first start, and `… Row-Bot runs as uid 10001` after that.
The secrets stay out of the environment and Row-Bot's processes stay
non-dumpable, as with any host. `container.yml` runs the hosting smoke in this
shape (`--shape railway`) on amd64 and arm64 for every pull request and release.

A file root creates in `/data` later (say from `railway ssh`, which may run as
root) stays root's, because the top is 10001's by then. Don't write in `/data`
as root; if you did, `chown -R 10001:10001` it.

## Health check

Railway's deploy health check calls the service from the host name
`healthcheck.railway.app`, on `PORT`
([Healthchecks](https://docs.railway.com/deployments/healthchecks)). Row-Bot
answers only host names it knows, so it would answer 400 and the deploy would
fail after 5 minutes.

The template sets **no** health check path, because it buys nothing here:

- It's used only while a deploy goes live; Railway doesn't monitor with it
  afterwards.
- A service with a volume has a short gap on every redeploy anyway: Railway
  stops the old deployment before the new one mounts the volume.
- Without one, Railway marks the deployment active once the container starts.
  If Row-Bot can't start, it exits with the reason in the deploy log and Railway
  shows the deployment as crashed.

To add one later, set the path to `/readyz` and `ROW_BOT_ALLOWED_HOSTS` to
`${{RAILWAY_PUBLIC_DOMAIN}},healthcheck.railway.app` (that variable replaces
Row-Bot's default host list, so the service's own name must be in it too).

## Proxy addresses

Railway's edge proxy ends HTTPS and forwards plain HTTP to port 8080 with
`X-Real-IP`, `X-Forwarded-Proto: https` and `X-Forwarded-Host`
([Specs and limits](https://docs.railway.com/networking/public-networking/specs-and-limits));
whether it also sends `X-Forwarded-For` isn't documented (Railway staff say it
does, on Central Station).

**Railway documents no address range for its edge proxy.** A moderator on
Central Station says 100.0.0.0/8; that is neither confirmed nor narrow enough,
and Row-Bot must never trust a broad range. So `ROW_BOT_TRUSTED_PROXY_CIDRS`
stays out of the template until the founder's check below.

Without a trusted proxy Row-Bot refuses every request that carries forwarding
headers (`untrusted_forwarding_headers`), and with
`ROW_BOT_UNTRUSTED_FORWARDED_ACTION=ignore` a browser on the https address can't
sign in (its origin is https, Row-Bot sees http).

**Open: Row-Bot doesn't accept Railway's headers yet.** With a trusted proxy, Row-Bot
refuses a request carrying `X-Real-IP` (`unsupported_forwarding_headers`;
`access/request_context.py` treats it as a forwarding header it doesn't
support, and `tests/subsystem/access/test_request_context.py` asserts so).
Railway sends it on every request. The template can't work until Row-Bot
accepts `X-Real-IP` beside a validated `X-Forwarded-For` from a trusted proxy,
the way it accepts `CF-Connecting-IP` (recognised, never a client source).
That file is outside this stream; the change is for the access owner.

### The founder's check, on a real deploy

1. Deploy the template once (it works up to Row-Bot's own pages).
2. In a terminal with the Railway CLI linked to the project, open a shell in
   the service: `railway ssh`. Don't create files in `/data` there.
3. Start a header echo on another port:

   ```sh
   python -c "
   import http.server as h
   class E(h.BaseHTTPRequestHandler):
       def do_GET(s):
           body = ('peer ' + s.client_address[0] + '\n' + str(s.headers)).encode()
           s.send_response(200); s.send_header('Content-Type', 'text/plain'); s.end_headers(); s.wfile.write(body)
   h.HTTPServer(('0.0.0.0', 8081), E).serve_forever()"
   ```

4. In Railway, **Settings › Networking**, edit the domain's target port to
   `8081`. Open `https://<the domain>/` from a computer, then from a phone on
   mobile data, then again after a redeploy. Each shows the peer address and
   every header Railway sent.
5. Set the target port back to `8080`, and stop the echo (Ctrl+C).
6. Report every peer address seen, and the full header list once (the
   `X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Proto`, `X-Forwarded-Host` and
   `Host` values, with your own address blanked).

Then `ROW_BOT_TRUSTED_PROXY_CIDRS` is the narrowest range that covers the peer
addresses seen: one address as `/32` if it never changes. If they spread across
a wide private range, stop and report rather than trusting it.

## Memory limit

Railway bills memory and CPU by what the service uses, up to its replica limits
(on Hobby, up to 48 GB and 48 vCPU per service). The template caps the service
at **4 GB and 2 vCPU** (**Service › Settings › Deploy › Replica Limits**):

- the bill can't run away (a stuck browser, a big local model download);
- Row-Bot reads the limit (cgroup `memory.max`), so its memory guidance works:
  local speech-to-text, the search model and the browser together peak at about
  3.2 GB, and Row-Bot says so if a restart came from lack of memory, with
  "Raise the service's memory limit … in Railway";
- Settings › System's "Hosted by Railway" card shows the use against 4 GB.

If the template composer can't carry replica limits, the docs tell each owner to
set them after the first deploy (`docs-site/docs/getting-started/on-railway.mdx`).

## Plans, volume and costs

Railway's published plans and prices
([Plans](https://docs.railway.com/pricing/plans), [Pricing](https://railway.com/pricing)):

- **Hobby**, $5 a month with $5 of usage included, is the plan to use. Its volumes
  hold up to **5 GB**: Row-Bot's own data, the search model (641 MB), local
  speech-to-text (464 MB) and caches came to 1.7 GB in the first hour, which
  leaves about 3 GB for conversations, documents and workspaces. Volume
  backups aren't part of Hobby (Pro and up), so owners keep their own backups
  from **Settings › Data**.
- **The trial** (a one-time $5 for up to 30 days, 1 GB memory, 0.5 GB volume)
  is too small: the search model alone doesn't fit the volume, and Row-Bot needs
  2 GB of memory.
- Usage: memory $10 per GB-month, CPU $20 per vCPU-month, volume $0.15 per
  GB-month, egress $0.05 per GB. Row-Bot uses about 0.4 GB just started and well
  under a tenth of a core when idle, so light use should cost roughly the $5
  minimum to about $10 a month. The founder's one-day reading is the real figure.
- Owners can cap spending in **Workspace › Usage › Usage Limits**.

**Kickbacks** ([Template Kickbacks](https://docs.railway.com/templates/kickbacks)):
a published template earns 15% of its deployers' usage, and 25% with the support
bonus for answering deployers' questions in the template queue on Central
Station. Paid as Railway credits, or cash through Stripe Connect.

Railway's terms limit its use to your own internal, personal or business use,
not on behalf of or for the benefit of a third party
([Terms](https://railway.com/legal/terms)): fine for each person running their
own Row-Bot from the template; not a basis for running Row-Bot for others.

## Publishing

After the founder's check, and once Row-Bot accepts Railway's headers:

1. Compose the template from the tables above and deploy it once from the
   template itself. Check, and note for the record:
   - the deploy log says `Row-Bot gave /data to uid 10001 and runs as uid 10001`,
     and after a restart only `Row-Bot runs as uid 10001`;
   - the setup code in the service's Variables signs a browser in at the
     service's address, and the same code is refused afterwards;
   - `ROW_BOT_PUBLIC_URL` resolved to `https://<the domain>`;
   - the replica limits came from the template (4 GB, 2 vCPU), and
     **Settings › System** shows "Memory … of 4 GB";
   - a ChatGPT or Claude plan sign-in, and an X sign-in with the callback
     `https://<the domain>/api/v1/accounts/x/auth/callback`, work;
   - the usage page's cost after a day.
2. Publish it (**Templates › Publish**). The button is
   `https://railway.com/button.svg`, linking to
   `https://railway.com/new/template/<code>?utm_medium=integration&utm_source=button&utm_campaign=row-bot`.
3. Add the link to the docs page "Row-Bot On Railway" and, after a release, to
   the landing page.
