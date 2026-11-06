# Row-Bot's Railway relay

A Cloudflare Worker for one page: `row-bot.ai/railway/` ("Continue with Railway"). That page signs
the person in to Railway in their browser and creates their own Row-Bot in their own Railway account.
Railway's GraphQL API (`https://backboard.railway.com/graphql/v2`) answers browsers only on
railway.com, so the page's API calls pass through this relay. Railway's sign-in (its token endpoint)
does accept browsers, so the sign-in itself never passes through here.

It is separate from the install count (`services/install-count/`) and shares nothing with it.

## What it does

`POST /graphql` from the page, with `Authorization: Bearer <the person's Railway token>` and a JSON
body of at most 64 KB, `{"query": …, "variables": …, "operationName": …}` and nothing else:

1. **Only the page's origin.** The request's `Origin` must be one of `ALLOWED_ORIGINS`
   (`https://row-bot.ai`; tests add a loopback page). Anything else gets an empty 403 with no CORS
   headers. `OPTIONS /graphql` answers the browser's preflight the same way, for `POST` only.
2. **Only the page's operations.** The query must be one query or mutation whose top-level fields
   are all on the list in `src/operations.js`:
   - reads: `me`, `regions`, `project`, `domains`, `deployment`;
   - changes: `projectCreate`, `serviceCreate`, `volumeCreate`, `serviceInstanceUpdate`,
     `serviceInstanceLimitsUpdate`, `serviceDomainCreate`, `environmentPatchCommit`,
     `serviceInstanceDeployV2`.

   Anything else (deleting, variables, billing, introspection, subscriptions, fragments, several
   operations in one request) is refused with 400 and never reaches Railway. The page can create a
   Row-Bot; nothing passing through here can delete or change anything else in the account.
3. **Passes on only the body and the token.** The body goes to Railway exactly as it came, with
   `Authorization` and a fixed `User-Agent: row-bot-railway-relay`. Nothing else of the request goes:
   no cookies, no address (`CF-Connecting-IP`, `X-Forwarded-For`), no `Referer`, no browser
   `User-Agent`, no other headers. Redirects aren't followed.
4. **Returns only the status and body.** Railway's answer comes back with its status and body, and
   none of its headers (no cookies).
5. **About 60 calls a minute from one address.** Cloudflare's rate limiter counts them; beyond that,
   429 "Too many requests. Wait a minute, then try again." A whole create, with its deploy polled
   every 3 seconds, stays well under it.

Anything else is refused: another path (404), another method (405), not JSON (415), no bearer token
(401), more than 64 KB (413).

## What it keeps

Nothing.

- No storage: `wrangler.toml` binds no KV, D1, R2, Durable Objects, queues or Analytics Engine.
- The rate limiter keeps only a counter per address, inside Cloudflare, for a minute; the address is
  its key there, and the Worker never writes it anywhere.
- No logs: the Worker has no `console` calls, and `wrangler.toml` turns Workers Logs and traces
  (`[observability]`), Logpush and source map upload off.
- The token is in the Worker's memory only for the one request it came with.

Cloudflare itself sees each connection (its address, TLS details, headers and time), as for any site
it serves; its own handling is set by Cloudflare's terms and the account's settings. Railway sees
the call coming from Cloudflare (Cloudflare marks requests made by Workers), never the person's
address.

## Deploy (the owner)

**Not deployed yet.** Deploying needs the owner's OK. You need Node 24 and the Cloudflare account
that runs the install count.

```sh
cd services/railway-relay
npm ci --ignore-scripts
npx wrangler telemetry disable
npx wrangler login
npx wrangler deploy
```

It prints `https://row-bot-railway.<your-subdomain>.workers.dev`. That address goes in two places in
`docs/railway/index.html`: the Content-Security-Policy's `connect-src` and the page's config
(`relay`). Then check, in the Cloudflare dashboard, that Workers Logs (observability) are off and no
Logpush job sends its events anywhere.

Check it without signing in to anything: a preflight from another origin and a refused operation.

```sh
curl -i -X OPTIONS https://row-bot-railway.<your-subdomain>.workers.dev/graphql \
  -H 'origin: https://example.com' -H 'access-control-request-method: POST'          # 403
curl -i -X POST https://row-bot-railway.<your-subdomain>.workers.dev/graphql \
  -H 'origin: https://row-bot.ai' -H 'content-type: application/json' \
  -H 'authorization: Bearer x' -d '{"query":"mutation { projectDelete(id: \"p\") }"}'  # 400
```

## Tests

`npm test` here, or `uv run python scripts/run_test_matrix.py railway-relay` from the repository
root (CI runs it on every pull request). The tests run the Worker in workerd with `fetch` mocked:
CORS for the page and refusals for anything else, the method, path, type, token and size limits,
the allow-list (each operation the page makes, and the ways around it that are refused), the rate
limit, and that only the body and token are passed on and only the status and body come back. Nothing leaves the
computer. The page's own tests (`tests/docs/railway_page_runtime_test.cjs`) check every call the page
makes against the same allow-list.
