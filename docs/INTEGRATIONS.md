# Integrations implementation and author guidance

The canonical React route is `/app-v2/settings/integrations`, with `tab`, `type`,
`q`, `source` and `selected` state in the URL. Legacy Skills/Plugins/MCP routes
translate to that context. `application/client_integrations.py` is a passive
projection and explicit discovery/preview adapter, not a second integration database.
Skills, plugin state, MCP configuration, admissions, protected secrets and profiles
remain authoritative. The user guide is `docs-site/docs/settings/integrations.mdx`.

## Portable authors

Agent Plugins 1.0.0 `plugin.json` and `mcp.json` use their canonical schema URLs.
Declare skills under `skills/<name>/SKILL.md`; keep supporting references beside
the skill. Only declarative skills and MCP are adapted. Native Row-Bot v2 manifests
keep their strict validation. Native Hermes `register(ctx)`, foreign Python or
JavaScript entry points, hooks, sidecars and UI extensions are not imported.
Unknown plugin metadata is ignored with diagnostics; closed MCP components reject
unsupported fields without hiding healthy sibling components. Missing author,
version or license information remains undeclared rather than invented.

Package IDs derive from canonical source identity and upstream name; skill aliases
also include the parent ID. Versions do not change identity. Upstream files remain
unchanged. Root/data substitutions are bounded; each package has a separate data
directory and minimal process environment. MCP secrets, tool choices and managed
launch preparation live in parent-owned overrides. Typed plugin targets route
setup through the existing MCP owners and share the parent's admission target.
Never copy a child into global MCP configuration merely to authenticate it.

Standalone public skills retain the existing Skills Hub header normalization for
their local name, source and default availability. This keeps new skills off during
publication. Supporting files retain their exact bytes. Portable package files,
including their skill headers, remain unchanged. This is a deliberate compatibility
exception to exact upstream bytes for standalone `SKILL.md` headers.

Use contained relative paths, no symlinks/junctions or case-colliding filenames,
and explicit requirements. Dependency-free Node MCP servers can use the existing
managed Node runtime. Imported npm commands require exact SHA-512-pinned artifacts;
dependencies require a complete shrinkwrap and must have no install scripts.
This deliberately bounded subset avoids a new resolver, arbitrary bootstrap or
ambient global package execution. Other commands remain an explicit advanced
configuration responsibility. Package processes are not OS-sandboxed.

## Authority and recovery

Every mutating common path delegates to its existing owner with session authority,
reviewed revisions, idempotency and an exclusive affected-resource claim. Passive
status does not replay effects. Filesystem publication checkpoints include operation
identity and, for packages, the staged directory identity. Equal content hashes
alone do not establish that a particular command completed. Explicit reconciliation
can close an old command or recover a provable publication; ambiguous states do not
become implicit success. One prior managed revision is retained, not an unlimited
backup system. Rollback covers package files, not arbitrary persistent-data migrations.

MCP OAuth uses SDK 1.29 from the existing lock, guarded public HTTPS discovery,
PKCE, state/session/revision validation and a transport-proven callback origin.
Credentials stage under a private reference and publish only after successful
authorization. Refresh revalidates endpoint and credential ownership. The protected
store publishes a generation pointer after bounded chunks, preserving an existing
working generation if a write fails. General Accounts credentials are never reused.

## Source and recommendation evidence

See [current discovery contracts](INTEGRATION_SOURCES.md) for eligibility, snapshot
delivery, combined-search fields and unresolved production refresh distribution.

- Official MCP Registry v0.1: nested server metadata, lifecycle status and bounded
  cursor pagination; exact source IDs are preserved rather than deduplicating by name.
- ClawHub v1: resolve a version once, acquire its complete zip or a validated pinned
  public GitHub handoff, preserve resources, then scan the reviewed tree. Publisher
  handles remain part of search identity, acquisition and update pins: the live API
  permits different publishers to share a slug. An explicit
  moderation verdict found during an update check is retained and blocks reactivation;
  a network failure never becomes a moderation verdict.
- Hermes plugin catalog: repo/SHA/subdirectory plus publisher removal information.
  The acquisition owner is `plugins/hermes_catalog.py`. It permits one exact HTTPS
  catalog migration from `hermes-agent.nousresearch.com/docs/api/plugin-catalog.json`
  to `nousresearch.github.io/hermes-agent/docs/api/plugin-catalog.json`. The destination
  cannot redirect again; other paths, queries, credentials, ports and origins are
  refused. Package downloads still reject redirects. Both requests use fresh clients
  without ambient proxies or cookies, retaining the decoded-byte download limit.
  Invalid catalog documents never replace a saved cache; an invalid local cache can
  be repaired by an explicit refresh.
  Optional-MCP manifests are fetched individually from a captured repository pin.
  Recipes with install/bootstrap or foreign OAuth-client requirements are unavailable.
- Default vendor recommendations: hosted Notion MCP and Linear MCP, with dated
  setup-documentation evidence and live-account validation pending. Local text tools
  1.0.0 and Hello Tool 0.1.0 remain explicit developer examples/imports.
- The Hermes Snyk candidate at `2a41a07f81e45125bf82a19af1b13396ace4b81f`
  declares analytics off, but npm `snyk@1.1306.0` has a postinstall bootstrap and
  unpinned transitive dependencies. It is not a starter or an automatically prepared
  dependency. No private repository scans or third-party installs were performed.

The hub is type-first: Apps & tools (MCP), Skills and Plugins each have Discover
and Installed. One explicit Search fans out to eligible catalogs; typing, passive
navigation and Installed filtering stay local. Official Registry search always
uses its local dated snapshot. Catalog refresh is a separate development action.
Category and disabled catalog IDs are device-local browser preferences; server
eligibility remains authoritative. Source/publisher and every merged attribution
remain visible; unknown compatibility is not readiness. The normal journey uses
full-width details and focused owner controls, with legacy editors in Advanced.

MCP setup follows declared auth/runtime requirements, then tested tool acceptance
and existing policy. Skills review complete bounded files/scripts and availability;
packages review contents and required/optional child setup. Credentials configured,
authenticated, enabled, connected and successful tool use remain distinct facts.
Try in chat passively checks the current chat/profile/model and canonical dispatch
policy, then adds only a draft; it never sends or changes the profile/model.

Conflicting controls share the retained owner operation guard, including package
children. Rejected skill preferences use the canonical session-owned skill receipt;
uncertain commands cannot be replaced or replayed. Updates compare staged bytes and
capability declarations; hosted tool drift requires renewed catalog acceptance.
Off retains settings and secrets. Disconnect deletes only the bound local credential
and reports remote revocation as unverified. MCP removal retains credentials by
default; explicit bound cleanup uses resumable protected tombstones. Package removal
previews children and keeps data by default, with separate reviewed purge.

Fixtures record synthetic source identities and must never contain credentials or
private paths. Live-account and macOS/Linux/clean-machine checks remain distinct
from deterministic Windows verification. See the user guide for recovery limits.

## Deliberate boundaries

No cloud marketplace, background discovery/update, foreign SDK emulation, arbitrary
bootstrap runner, cross-plugin dependency resolver, Docker orchestration, hosted
OAuth broker, payments/ratings, automatic code translation or UI extensions were
added. The reviewed dependency subset and first-party portable starter replace an
unsafe one-click Snyk pilot while retaining the required skill/MCP bundle lifecycle.


## Continuation validation boundaries

The October 2026 continuation reproduced the Hermes HTTP 301 failure, then verified
public catalog refresh through the exact migration above. This is discovery evidence,
not evidence that any listed integration works. Deterministic tests cover rejected
redirects, redirect loops, size limits, stale/invalid caches, owner-bound skill
previews, uncertain install recovery, and stable subdirectory update identities.
Existing lifecycle suites remain responsible for authentication, tool review,
profile policy, parent/child ownership, updates, restore, disabling and removal.

Remaining live validation requires a disposable Notion workspace and explicit user
authorization to sign in, refresh credentials, discover/review tools and disconnect.
Any real tool write requires separate approval. An external Hermes package needs a
recorded catalog SHA, complete content/prerequisite/data-disclosure review, and its
actual supported OS/runtime; catalog metadata alone is insufficient. Windows native
browser/callback behavior, macOS desktop behavior and Linux server callback behavior
need their respective hosts. Installer, signing, notarization and release checks are
outside this continuation. Landing media refresh remains with the landing task.

## Apps & Skills model (row_bot.integrations)

The domain package composes the owners above; it never replaces them.

- `apps.py` and `apps.json`: curated app identities (98 featured, ranked), each with
  jobs, synonyms, example prompts, links, auth, vendor domains and GitHub orgs, and
  the vendor documentation that confirmed it. A record attaches only through a
  reviewed reference (curated recipe, Registry namespace or exact server, endpoint
  host, package or repository). Anything else is a community entry. The vendor badge
  comes only from rules: a Registry namespace that is a vendor domain reversed or
  `io.github.<vendor org>`, or a vendor endpoint. Shared hosting domains can never be
  vendor domains. To add an app, add one record.
- `index.py`: the local Registry mirror, a SQLite FTS5 index in `catalogs/` under the
  data folder. Start-up builds it from the shipped snapshot; an update builds a new
  generation and swaps one pointer. Searching only reads it.
- `catalogs.py`: explicit catalog updates (one background job per source that
  declares `network: explicit`) and the optional schedule, off by default.
- `icons.py`: bundled marks (`icons.json`, licence per mark), letter avatars, and
  Registry rasters cached only during an update (re-encoded PNG; SVG refused).
- `sources.py`: one adapter per catalog with server-side eligibility, the ranking key
  and source-neutral dedup. To add a source, add one adapter and register it; clients
  read the list from `GET /api/v1/integrations/sources`. `skills.json` is the featured
  skills library: references to pinned folders in official and maintainer
  repositories, with each licence checked; nothing third-party is bundled.
- `facts.py`: typed owner facts and status v2. `lifecycle` and `readiness` come with
  every `blocker` and exactly one `next_action`. Static facts are indexed by owner
  fingerprints. Live runtime state, requirements and unfinished changes are applied
  on each read, and unfinished owner commands are reconciled there: settled when
  proven, never repeated. An unproven skill outcome stays unfinished until someone
  checks it explicitly.
- `plans.py`: install plans and the runner.
  - Every step type exists in the contract. Steps not implemented yet are marked
    `unsupported` with a reason, and such a plan cannot start.
  - Starting requires the consent token from `POST /api/v1/integrations/plans/review`,
    bound to the session and the plan digest. The consent lists destinations,
    downloads and whether MCP itself will be turned on (`turns_on_mcp`), which can
    wake other connections that are marked on.
  - Changing access only applies a preset; it never turns a connection on. Servers
    whose tools must be chosen one by one cannot take a preset.
  - Preparing an npm package needs Row-Bot on this computer, as on its own route.
  - The runner records each owner command before sending it, so a retry replays or
    reconciles it.
  - A plan pauses only for a browser sign-in, a missing key, access to newly
    discovered tools, or a finished background step. A plan whose launch recipe
    changed while it waited fails with `plan_changed` instead of continuing.
  - The access review binds the chosen preset, so tools reviewed under one preset are
    never saved under another.
  - An item's unfinished plan is returned with its detail and review, so it can
    always be continued or cancelled. Each owner has its own plan per item.
- `presets.py`: Read only, Ask before changes (default) and Full access on the
  existing per-tool policy. Destructive, approval-declaring and unknown-effect tools
  stay approval-locked under every preset. Because the effect classifier treats every
  recognised change as destructive, Full access currently differs from Ask before
  changes only for browser-interaction tools. Approvals at invocation are unchanged.
- `safe.py`: the one catalog fetcher, link cleaner, preview cache and file writer.
  - Fetches are https only. Reviewed hosts use the system or environment proxy when
    one is set; every other host connects directly, never through a proxy.
  - A direct connection is made only to a checked public address. Through a proxy the
    address cannot be checked, so the reviewed host list is the guard.
  - Every redirect hop is rechecked, and credentials never cross hosts.
  - Responses are size-capped, never decompressed, and the whole fetch has a
    deadline. NAT64 and IPv4-compatible forms of private addresses are refused.
  - Files are written readable only by this account.

MCP owner functions take an explicit `target` (standalone or one package child);
there is no ambient target.
