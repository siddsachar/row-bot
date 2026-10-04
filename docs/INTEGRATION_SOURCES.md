# Integration discovery contracts

Reviewed 3 October 2026. Catalog metadata is untrusted input. A listing, supported
format, authenticated connection and successful approved tool use are separate
facts. Nothing here establishes live-account compatibility.

## Source eligibility

| Source | Contract and current discovery policy |
| --- | --- |
| Vendor recommendations | Local metadata in `recommended_servers.json`. Notion and Linear hosted endpoints have dated vendor setup evidence; live account checks remain pending. Developer fixtures are explicit `examples` or imports, never normal recommendations. |
| Official MCP Registry | Local bounded snapshot of v0.1 metadata. Search sends no keyword request. Explicit development refresh reads at most five latest-version pages of 100 records. Preview and new configuration publication recheck the exact name/version and recipe against current status. |
| ClawHub skills | Documented public v1 search/list/detail/download. Complete pinned bundles and publisher identity remain owned by Skills Hub. Installation rechecks current moderation and the exact version. |
| GitHub skills | Existing bounded maintainer repository roots through the GitHub owner and documented repository contents/tree APIs. Explicit imports preserve subdirectories/revisions. No arbitrary repository search or upstream install CLI. |
| Hermes packages / MCP recipes | Existing pinned catalog and recipe adapters. Ordinary sources, not format authorities. Fresh package inspection/publication rejects unavailable, removed or changed catalog identities. The exact reviewed catalog migration redirect remains the only exception. |
| Row-Bot native marketplace | Saved marketplace metadata only. Its existing reviewed refresh and install lifecycle remain canonical. |
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

## Snapshot production and refresh

`registry_snapshot.json` is packaged with the application. It contains 500
normalized records from the beginning of the preserved 2 October 2026 07:47 UTC
latest-version Registry sample. This is a bounded, non-random subset, not a complete
catalog or a supported-integration count. It stores origin, API/schema version,
acquisition time, completeness/limit and SHA-256 of canonical normalized entries.
The loader verifies that digest and bounds before use. After seven days the status
is stale; stale metadata never certifies installability.

Reproduction is offline from a saved Registry envelope:

```powershell
uv run python scripts/build_mcp_registry_snapshot.py registry-input.json snapshot-output.json --captured-at 2026-10-02T07:47:45.431572+00:00
```

The same normalizer is used by explicit refresh. Failed refreshes preserve the
last intact saved snapshot; a corrupt saved snapshot falls back to the shipped
copy. New imports fail closed if exact current source status cannot be established.
Existing installed configurations and provenance are retained.

The settled delivery mechanism is release-bundled local metadata. **Production
refresh distribution remains blocked** on a maintained, upstream-supported
redistributable feed or distribution arrangement that allows direct desktop
access without an embedded shared credential. No new cloud service, scheduled
crawl or automatic update was added. `refresh_catalogs=true` remains an explicit
bounded development action, not a production feed commitment. Registry metadata
is CC0; that does not license packages or imply endorsement.

## Search API handoff

`POST /api/v1/settings/integrations/search` accepts `kind`, `query`, optional
`sources`, `refresh`, `refresh_catalogs`, `include_incompatible`, `cursor` and
`limit` (1-96, default 50). Omit sources for the selected type's eligible catalogs;
source ids come from `GET /api/v1/integrations/sources`, and the server rejects
unknown ids. The typed `POST /api/v1/integrations/items/search` takes the same
request.
`refresh=false` is passive/cache-only, including pasted query drafts. An explicit
Search uses `refresh=true`; Registry metadata still searches locally. Source
settings can request the named unavailable sources to inspect their typed
eligibility/reasons. Optional catalog credentials have no editor. Category and disabled catalog IDs
are persisted in device-local browser storage; queries and secrets are not. The
server eligibility policy still decides which sources can execute.

A four-slot coordinator applies eight-second source deadlines. It returns useful
partial outcomes, checks cancellation/disconnect, suppresses late search results
and propagates cancellation before source cache publication. In-flight blocking
transport reads can finish within their transport timeout; they cannot publish a
cancelled search revision. No forced process termination is involved.

Ranking is deterministic: exact name, a reviewed app identity, textual relevance
(including the app's synonyms and jobs), inspection evidence, then source
precedence and stable name/identity ties. There is no LLM, popularity-as-safety
score or first-source global 96-row cap.
Pagination uses the retained merged result, not another public search. Cursors
bind owner, query, type, source set and incompatible filter for 20 minutes;
expired cursors require a new search. Source adapters retain their bounded fetch
limits, so results do not claim exhaustive upstream coverage.

`IntegrationItem` adds `attributions`, `canonical_identity`, `evidence_stage`
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

The implemented shell selects type before discovery, uses one explicit Search,
and shows full-width detail/setup. Client AbortSignals and generation/identity
checks suppress late search and inspection output after navigation. Catalogs
shows provenance, age, device enablement and exact unavailable-source reasons.
Focused setup, first-use drafts and lifecycle controls delegate to the original
owners; see [implementation guidance](INTEGRATIONS.md) for authority and recovery.


## Registry declaration binding

Snapshot schema 2 binds the complete bounded delivery declarations to a SHA-256
setup digest, including remote headers/variables, authentication extensions,
package environment, runtime/arguments, registry location and integrity fields.
Header/environment values and defaults are not copied into metadata, notes or
a configuration. Only plain HTTPS transports and the existing pinned npm subset
can produce import recipes. Unsupported header/environment/argument mappings,
custom runtime or registry requirements, integrity constraints and unknown setup
extensions remain explicitly unsupported; adding them is a separate owner change.
An alternate supported recipe may still be selected, with the entire declared
variant set bound to its review. This is conservative: changes to unused variants
can also require review again.

Search identity includes the setup digest. Preview and configuration publication
recompute it from current exact-version metadata; any setup change invalidates
the earlier review. Older snapshots without declaration binding cannot authorize
an import and saved schema-1 snapshots fall back to the shipped schema-2 copy.
The bound input is at most 64 KiB with at most 16 remotes and 16 packages; oversized
declarations are rejected rather than truncated into a misleading identity.
Catalog preferences and browser abort/stale-response handling are implemented.
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
The shipped 500 records and 333 recipe candidates are not working integrations.
Production refresh distribution, real authorized accounts, Windows native OAuth,
macOS/Linux hosts and participant usability remain explicit pending gates.
