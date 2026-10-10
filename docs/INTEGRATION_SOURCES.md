# Integration discovery contracts

Reviewed 3 October 2026. Catalog metadata is untrusted input. A listing, supported
format, authenticated connection and successful approved tool use are separate
facts. Nothing here establishes live-account compatibility.

## Source eligibility

| Source | Contract and current discovery policy |
| --- | --- |
| Vendor recommendations | Local metadata in `recommended_servers.json`: 50 recipes (40 hosted endpoints, 10 local commands), each with its own review date. 27 hosted endpoints' URL and sign-in method were checked against vendor documentation on 4 October 2026, live accounts untested. Eight (GitHub's two hosted ways, Notion, Linear, Sentry, Supabase, Stripe, Context7) were validated live with Row-Bot on Windows on 5 to 8 October 2026 and are marked `tested_with_row_bot`. Sign-in is declared only where any client can sign in or an API key header is documented. Developer fixtures are explicit `examples` or imports, never normal recommendations. |
| Featured skills | Local `skills.json`: 40 skills referenced at pinned commits in 17 official and maintainer repositories, each under an OSI licence checked at that commit (proprietary, unlicensed and share-alike skills were left out). Opens Skills Discover; adding one reads the pinned folder from GitHub after consent. |
| Official MCP Registry | A full local mirror of every latest v0.1 record, shipped as a compressed snapshot and indexed on this computer. Search sends no request. Only an explicit **Update** or **Update all** in Apps › Advanced › Catalogs (or **Update catalogs automatically**, off by default) reads the Registry, as `updated_since` deltas. Preview and new configuration publication recheck the exact name/version and recipe against current status. |
| ClawHub skills | Documented public v1 search/list/detail/download. Complete pinned bundles and publisher identity remain owned by Skills Hub. Installation rechecks current moderation and the exact version. Summaries are shown as plain text; downloads, stars and the official flag pass through as publisher signals. |
| GitHub skills | Existing bounded maintainer repository roots through the GitHub owner and documented repository contents/tree APIs. Explicit imports preserve subdirectories/revisions. No arbitrary repository search or upstream install CLI. |
| Hermes packages / MCP recipes | Existing pinned catalog and recipe adapters. Ordinary sources, not format authorities. Fresh package inspection/publication rejects unavailable, removed or changed catalog identities. The exact reviewed catalog migration redirect remains the only exception. |
| Row-Bot native marketplace | Saved marketplace metadata only. Its existing reviewed refresh and install lifecycle remain canonical. |
| Built in | Row-Bot's own accounts (Google, GitHub, X), channels and key-based tools, read from their owners; no service is contacted and no program is started. Each opens its owner's page. |
| Windows connectors | Only on Windows builds whose On-device Agent Registry (`odr.exe` in the Windows system folder) is present; hidden elsewhere. `odr.exe list` runs only on an explicit update of this catalog, and searching reads the saved list. Row-Bot never adds, removes or configures a connector. |
| Composio | A third-party hosted broker, `explicit_only` until the person turns it on in Apps › Advanced › Catalogs after its disclosure (a separate Composio account; requests and results for those apps pass through it). Its catalog cannot be listed without an account, so Row-Bot searches its own list of app names (`broker_apps.json`). Its remote code tools start off and its acting tools ask every time. |
| skills.sh | Discovery unavailable: documented v1 requires Vercel OIDC. Anonymous legacy responses do not establish a desktop contract. Existing imports and installed provenance remain available. |
| Glama | Discovery unavailable: directory reads require an API key; the data license requires visible Glama credit on each displaying view and a backlink for every listing. Access/license integration is not implemented. No telemetry endpoint is used. |
| PulseMCP | Discovery unavailable: B2B tenant/key access has no approved desktop integration. |
| Smithery | Discovery unavailable: documented bearer authentication conflicts with the observed anonymous response; desktop access remains unresolved. |
| browse.sh / LobeHub | Discovery unavailable pending an established skill catalog contract. Existing explicit imports remain available. LobeHub agent-prompt conversion is not evidence of Agent Skills compatibility. |
| ClawHub plugin packages | Discovery deferred after a bounded documentation assessment. `bundle-plugin` includes multiple formats; only actual Agent Plugins 1.0 declarative skills/MCP contents can use the existing portable owner. Native OpenClaw SDKs, hooks, settings and client-specific bundle mappings are not implemented. |

Primary contracts: [Registry aggregation](https://modelcontextprotocol.io/registry/registry-aggregators),
[Registry metadata terms](https://modelcontextprotocol.io/registry/terms-of-service),
[ClawHub API](https://docs.openclaw.ai/clawhub/http-api),
[OpenClaw bundle formats](https://docs.openclaw.ai/plugins/bundles),
[GitHub contents API](https://docs.github.com/en/rest/repos/contents),
[Hermes catalog](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/plugin-catalog.md),
[skills.sh API](https://www.skills.sh/docs/api), [Glama API](https://glama.ai/mcp/reference),
[PulseMCP API](https://www.pulsemcp.com/api/docs/v0.1),
[Smithery API](https://smithery.ai/docs/api-reference/servers/list-all-servers),
[Notion setup](https://developers.notion.com/guides/mcp/get-started-with-mcp),
[Linear setup](https://linear.app/docs/mcp).

The prior read-only probes observed Registry, ClawHub and anonymous Smithery
responses, and a 401 from documented skills.sh v1. They did not authorize accounts,
execute packages or validate service tools. Optional provider credentials are not
supported by this implementation; no shared desktop secret is shipped.

## Registry mirror: snapshot, index and updates

`registry_snapshot.jsonl.xz` is packaged with the application: every latest
Registry record (40,103 captured on 6 October 2026), normalized and sorted by name,
as xz-compressed JSON lines. A header records the source, capture time, the newest
`updatedAt` (the update watermark), the record count and the SHA-256 of the
records; the loader refuses a changed digest, schema or oversized file. The same
records and capture time always produce the same bytes.

The snapshot is a developer build step. It reads the public Registry read-only,
one page at a time with a pause between pages and backoff on throttling, and falls
back to a saved capture when the network is unavailable:

```powershell
uv run python scripts/build_mcp_registry_snapshot.py --sync
uv run python scripts/build_mcp_registry_snapshot.py --sync --since src/row_bot/mcp_client/registry_snapshot.jsonl.xz
uv run python scripts/build_mcp_registry_snapshot.py --input registry.json --captured-at 2026-10-02T07:47:45Z
```

At start-up Row-Bot builds a local SQLite FTS5 index under `catalogs/` in the data
folder when it is missing or a release ships a newer snapshot. Searching only reads
that index; it never builds, writes or contacts the Registry. Records that cannot
be reviewed (for example more than 16 declared remotes) stay listed with a reason
and no recipe, so they can never be imported. SVG icons are dropped at
normalization. Registry metadata is CC0; that does not license packages or imply
endorsement.

## Updating catalogs and measuring coverage

`POST /api/v1/integrations/sources/{id}/update` starts one background update of a
source that declares `network: explicit` (the Registry, Hermes packages and MCP
recipes, ClawHub and GitHub skills, and Windows connectors where present);
`GET /api/v1/integrations/sources` reports its state. The Registry update pages
`updated_since` deltas from the mirror's watermark (with `If-None-Match` when an
ETag was saved; a full resync when the mirror is over
180 days old), builds a new index generation and swaps it in; any failure keeps the
previous index. It also caches up to 200 new Registry icons per update, only from each
publisher's own domain (its namespace's domain, or GitHub's content hosts for
`io.github` namespaces), with a generic User-Agent; PNG, JPEG and GIF are decoded and
re-encoded, and a failed icon is not asked for again for 30 days. An optional schedule
(`GET`/`PUT /api/v1/integrations/catalog-schedule`, daily, weekly or monthly, for all or
chosen catalogs) is off by default; only while it is on does a scheduler job run the
same updates.

Skill listings that share a content hash, an upstream folder or the opening of a long
declared description are one skill: the official, then most used, copy leads and
every copy stays an attribution.

`scripts/integration_coverage.py` reports, per catalog entry, whether its install plan
is complete or the exact reason it is not, offline and in a throwaway data folder.

## Network, proxies and their limit

Every catalog request goes through one fetcher (`integrations/safe.py`): https on
port 443 only, no automatic redirects (each hop is checked again), credentials
never sent to another host, identity encoding, and size and time caps. A direct
connection that cannot reach one checked address moves on to the host's next
checked address within the same deadline.

- **Reviewed catalog hosts** (the Registry, Hermes, ClawHub and the GitHub API and
  download hosts) use the system or environment proxy when one is set
  (`HTTPS_PROXY`/`NO_PROXY`, the Windows Internet settings or macOS network
  settings; only `http`/`https` proxies; PAC scripts are not run). Without a proxy
  they connect directly, only to an address checked to be public.
- **Any other host** (Registry icons fetched during an update, Registry bundles
  after consent, skill or plugin imports from a link, well-known skill indexes) is
  never sent through a proxy. It connects directly, only to a checked public
  address, so these fail on networks that allow traffic only through a proxy.
- **Limitation:** through a proxy, Row-Bot cannot see or check the address the
  proxy connects to; the host allow-list is then the protection. Proxy credentials
  go only to the proxy; TLS stays end to end with the reviewed host.

## Search API handoff

`POST /api/v1/integrations/items/search` accepts `kind`, `query`, optional
`sources`, `refresh`, `include_incompatible`, `cursor` and
`limit` (1-96, default 50). Omit sources for the selected type's eligible catalogs;
source ids come from `GET /api/v1/integrations/sources`, and the server rejects
unknown ids.
`refresh=false` is passive/cache-only, including pasted query drafts. An explicit
Search uses `refresh=true`; Registry metadata still searches locally. Source
settings can request the named unavailable sources to inspect their typed
eligibility/reasons. Optional catalog credentials have no editor. The server
eligibility policy decides which sources can execute.

A four-slot coordinator applies eight-second source deadlines. It returns useful
partial outcomes, checks cancellation/disconnect, suppresses late search results
and propagates cancellation before source cache publication. In-flight blocking
transport reads can finish within their transport timeout; they cannot publish a
cancelled search revision. No forced process termination is involved.

Ranking is deterministic, one key for every source (and the same order inside the
Registry index): a featured app or a vendor-verified record first (a community
record never wins on its name alone); then an exact name or app name; every query
word in the name, publisher, app name, synonyms or jobs; featured order; an
installable plan with known authentication; freshness (90 days, a year);
source-provided popularity; then source precedence and stable ties (a merged
deployment is led by its most reviewed source, then its vendor-verified record). The
empty query lists featured app records only, never an alphabetical dump; the
Registry returns its top 200 matches and reports `truncated` beyond that. There is
no LLM and no popularity-as-safety score. The vendor badge comes only from rules: a
Registry namespace that is a vendor domain reversed or `io.github.<vendor org>`, or,
for reviewed recipes and the user's own configurations, a vendor endpoint host. A
Registry record attaches to an app and earns the badge through its namespace alone,
because any publisher can point a record at a vendor's endpoint.
Pagination uses the retained merged result, not another public search. Cursors
bind owner, query, type, source set and incompatible filter for 20 minutes;
expired cursors require a new search. Source adapters retain their bounded fetch
limits, so results do not claim exhaustive upstream coverage.

`IntegrationEntry` adds `attributions`, `evidence`
(`listed` or `inspected`), `tested_with_row_bot` and `auth_requirement`. Unknown
auth stays unknown. Uninspected rows stay `not_inspected`/`discover`, never ready.
Known incompatible rows appear only when searched for (or requested), with their
reason. One deployment (transport and endpoint, package and version, or the
Registry record when there is no recipe) or one pinned repository/subpath merges
into the record of the most reviewed source; labels never merge. Merged rows keep
every source's attribution.
`IntegrationSourceStatus` adds typed eligibility/access, enablement, snapshot
version/digest, fetched time and truncation. Installed inventory remains local.
Existing preview references remain owner-bound; setup, secrets, approvals,
profiles, idempotency and durability stay behind existing owners.

Settings › Apps and Settings › Skills read local results as the person types
(`GET /api/v1/integrations/items`); **Search online catalogs** is the one explicit
online search. Item pages show full-width detail and run setup as server plans.
Client AbortSignals and generation/identity checks suppress late search and
inspection output after navigation. Apps › Advanced › Catalogs shows provenance,
age, update state, opt-in brokers and exact unavailable-source reasons. Setup and
lifecycle controls delegate to the original owners; see
[implementation guidance](INTEGRATIONS.md) for authority and recovery.


## Registry declaration binding

The snapshot (schema 4, whose recipes carry their declared inputs) binds the
complete bounded delivery declarations to a SHA-256 setup digest, including remote
headers/variables, authentication extensions, package environment,
runtime/arguments, registry location and integrity fields. Header/environment
values and defaults are not copied into metadata, notes or a configuration; they
become declared inputs (`integrations/inputs.py`). Recipes come from HTTPS
streamable-HTTP or SSE remotes, from npm, PyPI and OCI packages on their default
registries (an image needs a fixed tag or digest and gets no folders, ports or
host network), and from MCP bundles named by their SHA-256. A secret on a command
line, a payment header, a custom registry, a package that runs as a web server and
unknown setup fields stay unsupported, with the reason shown; adding them is a
separate owner change.
An alternate supported recipe may still be selected, with the entire declared
variant set bound to its review. This is conservative: changes to unused variants
can also require review again.

Search identity includes the setup digest. Preview and configuration publication
recompute it from current exact-version metadata; any setup change invalidates
the earlier review. A snapshot of any other schema is refused, so an older saved
copy cannot authorize an import.
The bound input is at most 64 KiB with at most 16 remotes and 16 packages; oversized
declarations are rejected rather than truncated into a misleading identity.
Browser abort/stale-response handling is implemented.
Setup preserves unknown authentication and derives readiness from saved owners.
A recipe indicates representable declarations only: authentication, prepared
package dependencies and successful tool use need independent evidence.


## Evidence and remaining validation

Primary access contracts above were re-read on 3 October 2026. The six earlier
unauthenticated probes on that date remain point-in-time evidence: skills.sh v1
returned 401, while its legacy path and Smithery returned 200. Those responses do
not resolve documented authentication, licensing or desktop access requirements.
No accounts, provider credentials, runtime downloads/execution or service writes
were authorized by this metadata review. There is no guessed endpoint or HTML
fallback for unavailable catalogs.

The UX5 assessment records at least 20 publisher-distinct real candidates using
preserved dated public envelopes, exact versions, hashes and current parser
outcomes. Listing, metadata/package inspection, compatible recipe, authenticated,
successful approved tool use and lifecycle evidence are independent fields.
Parser acceptance alone is not package verification; service-name alternatives
remain separate publishers. Synthetic lifecycle tests are a separate evidence set.
Shipped records and their recipe candidates are not working integrations. Real
authorized accounts beyond the recipes marked tested, macOS/Linux hosts and
participant usability remain explicit pending gates.
