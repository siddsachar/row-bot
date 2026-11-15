# Row-Bot install stats collector

A Cloudflare Worker with a D1 database that counts the Row-Bot installations reporting in the
last 7 and 15 days (N6; "Install stats" in Row-Bot). It is on by default; Row-Bot sends nothing
once the person turns "Share install stats" off (Settings › System › Privacy) or sets
`ROW_BOT_INSTALL_COUNT=0`. The client side is `src/row_bot/install_count.py`; its `COLLECTOR_URL`
is the deployed collector, `https://row-bot-count.row-bot.workers.dev` (empty would make it dormant).

## What it receives

`POST /v1/ping` with exactly this JSON body (1 KB at most; anything else is refused with an empty
400, 413 or 415):

```json
{"schema": 1, "install": "<32 hex: a random 128-bit ID>", "version": "5.3.0", "os": "windows"}
```

`os` is `windows`, `macos`, `linux` or `docker`. Row-Bot sends it shortly after it starts (a
minute plus up to an hour of jitter), then at most once every 24 hours while it runs (0-60 minutes late, on purpose), from its server
process over HTTPS with the User-Agent `Row-Bot`.

`POST /v1/delete` with `{"schema": 1, "install": "<32 hex>", "secret": "<64 hex>"}` deletes every
row of that installation ("Delete what was sent…" in Row-Bot). The install ID is the first 128 bits
of SHA-256 over `row-bot-install-count:` and the secret's bytes, so the Worker checks a deletion
without storing any secret, and a ping carries nothing more than the four fields above.

`GET /v1/stats` with `Authorization: Bearer <STATS_TOKEN>` answers:

```json
{"active_7d": 0, "active_15d": 0, "by_version": {"5.3.0": {"active_7d": 0, "active_15d": 0}},
 "by_os": {"windows": {"active_7d": 0, "active_15d": 0}}, "as_of": "2026-11-20",
 "by_country": {"GB": {"today": 0, "yesterday": 0}}}
```

`by_country` is the installations reporting on that day (today so far, and yesterday) from each
country, as Cloudflare works it out from the connection (`request.cf.country`; `XX` when unknown).

`GET /v1/totals` needs no token and answers only the current totals, overall and by OS (no versions,
countries or days; rate-limited like a ping). The owner's reports read it:

```json
{"active_7d": 0, "active_15d": 0, "by_os": {"windows": {"active_7d": 0, "active_15d": 0}}, "as_of": "2026-11-20"}
```

Each installation counts once, under the version and OS of its latest day in the window. Show
these numbers as "installations reporting in the last 7/15 days", apart from GitHub downloads,
never as users, all installs or consenting installs.

## What it stores, and for how long

- `pings`: one row per installation per UTC day with an HMAC-SHA-256 of the install ID (the key is
  the `ID_HMAC_KEY` secret), the day, the version and the OS. Never the ID itself, an IP address,
  headers or the request body. Rows older than 35 days are deleted by the daily cron (03:17 UTC).
- `totals`: each day's numbers, overall, by version and by OS, with no IDs. Kept.
- `countries`: how many installations reported from each country each day (D22). The first ping of
  an installation on a day adds one to its country's count; the country is never written next to the
  ID or into `pings`, and "Delete what was sent" leaves these counts, which hold no IDs. Kept.

## Hardening

- A 1 KB body cap and a strict schema on every endpoint; answers carry no body (stats aside).
- Cloudflare's rate limiter: 20 requests a minute from one address. The address is the limiter's
  key inside Cloudflare for that minute; the Worker never writes it anywhere.
- Nothing logged: the Worker has no `console` calls, and `wrangler.toml` turns Workers Logs and
  traces (`[observability]`), Logpush and source map upload off.
- `wrangler.toml` holds no account ID or secret.

Cloudflare itself sees each connection (its IP address, TLS details, headers and time), as for any
site it serves; its own handling is set by Cloudflare's terms and the account's settings.

## Deploy (the owner)

You need Node 24 and a Cloudflare account; check Cloudflare's current plan limits for Workers, D1,
cron triggers and the rate limiter. Wrangler sends its own usage metrics to Cloudflare unless you
turn them off; the steps do that first.

1. Install the locked tools and sign in:

   ```sh
   cd services/install-count
   npm ci --ignore-scripts
   npx wrangler telemetry disable
   npx wrangler login
   ```

2. Create the database and put its ID in `wrangler.toml` (`database_id`) for your deploy. It isn't
   a secret, but keep it out of commits so the repository stays free of account details:

   ```sh
   npx wrangler d1 create row-bot-count
   npx wrangler d1 migrations apply row-bot-count --remote
   ```

3. Set the two secrets. Keep `STATS_TOKEN` in your password manager; don't rotate `ID_HMAC_KEY`
   without reason (rotating it makes every installation look new for 15 days, and "Delete counted
   data" can no longer find rows made under the old key until they expire):

   ```sh
   openssl rand -hex 32 | npx wrangler secret put ID_HMAC_KEY
   openssl rand -hex 32 | npx wrangler secret put STATS_TOKEN
   ```

   In PowerShell, `Get-Random` isn't a cryptographic source: make each value with
   `$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') })`
   and paste it when Wrangler asks.

4. Deploy, then check the Worker in the Cloudflare dashboard: Workers Logs (observability) off,
   and no Logpush job sending its events anywhere.

   ```sh
   npx wrangler deploy
   ```

   It prints `https://row-bot-count.<your-subdomain>.workers.dev`.

5. Check it without adding a row: a malformed ping is refused and the stats read zero.

   ```sh
   curl -i -X POST https://row-bot-count.<your-subdomain>.workers.dev/v1/ping \
     -H 'content-type: application/json' -d '{}'                       # 400
   curl -s https://row-bot-count.<your-subdomain>.workers.dev/v1/stats \
     -H "Authorization: Bearer $STATS_TOKEN"                           # {"active_7d":0,...}
   ```

6. Put the address into Row-Bot: `COLLECTOR_URL` in `src/row_bot/install_count.py`
   (`https://row-bot-count.<your-subdomain>.workers.dev`, no trailing slash). Choose the account's
   `workers.dev` subdomain before that (Workers & Pages › Account details › Subdomain): released
   installations keep the address they shipped with.

History, from the kept totals:

```sh
npx wrangler d1 execute row-bot-count --remote \
  --command "SELECT day, active_7d, active_15d FROM totals WHERE kind = 'all' ORDER BY day DESC LIMIT 30"
```

The legal side (EU ePrivacy and GDPR, UK PECR and the Data (Use and Access) Act 2025) is the
owner's review before 5.3.0; nothing here is legal advice.

## Tests

`npm test` here, or `uv run python scripts/run_test_matrix.py install-count` from the repository
root (CI runs it on every pull request). The tests run the Worker in workerd with a local D1 and
rate limiter: the schema and size limits, deduplication, the 7/15-day windows, the 35-day expiry,
deletion and the stats token. Nothing leaves the computer.
