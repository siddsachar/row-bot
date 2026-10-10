# Integrations implementation and author guidance

The React routes are `/app-v2/settings/apps` and `/app-v2/settings/skills`, each with
its item pages; 5.0.0's Plugins, MCP, Accounts and Channels links redirect there.
`application/client_integrations.py` is a passive projection and explicit
discovery/preview adapter, not a second integration database. Skills, plugin state,
MCP configuration, admissions, protected secrets and profiles remain authoritative.
The user guides are `docs-site/docs/settings/apps.mdx` and `settings/skills.mdx`; the
developer guides are under `docs-site/docs/extending/`.

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
managed Node runtime. Package launches (`npx`, `uvx`, `docker run`, or an `.mcpb`
bundle) are prepared by `mcp_client/packages.py` from a lock the person reviews:
npm registry tarballs with SHA-512 integrity, taken from the publisher's
shrinkwrap or a lock-only `npm install --ignore-scripts` resolution in Row-Bot's
own cache; prebuilt PyPI wheels installed by uv with `--require-hashes` into a
private environment; container images pulled once and run by digest with no
folders, ports or host network; and bundles whose manifest, paths and signature
are checked before they are unpacked privately. Install scripts never run (a
package that declares one is named in the review as possibly not working), and
nothing is installed globally. Other commands remain an explicit advanced
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
The client is the person's own OAuth app when they give one; otherwise Row-Bot's
published client ID metadata document (`https://row-bot.ai/oauth/client-metadata.json`,
from `docs-site/static/oauth/`, loopback redirects only, no secret) when the
authorization server supports it, else dynamic registration for that connection.
Credentials stage under a private reference and publish only after successful
authorization. Refresh revalidates endpoint and credential ownership. The protected
store publishes a generation pointer after bounded chunks, preserving an existing
working generation if a write fails. General Accounts credentials are never reused.

## Source and recommendation evidence

See [current discovery contracts](INTEGRATION_SOURCES.md) for eligibility, snapshot
delivery, combined-search fields and catalog updates.

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
- Vendor recommendations: `recommended_servers.json` recipes carry dated evidence.
  GitHub's two hosted ways, Notion, Linear, Sentry, Supabase, Stripe and Context7
  were validated live with Row-Bot on Windows and are marked tested; the others
  rest on vendor documentation. Local text tools 1.0.0 (shipped under
  `plugins/bundled/`) and Hello Tool 0.1.0 remain explicit developer examples/imports.
- The Hermes Snyk candidate at `2a41a07f81e45125bf82a19af1b13396ace4b81f`
  declares analytics off, but npm `snyk@1.1306.0` has a postinstall bootstrap and
  unpinned transitive dependencies. It is not a starter or an automatically prepared
  dependency. No private repository scans or third-party installs were performed.

Settings › Apps (MCP servers, packages and the built-in accounts and channels,
one card per app) and Settings › Skills each list the person's own items first,
then featured ones, then everything, searched locally as they type. Only an
explicit **Search online catalogs** fans out to eligible catalogs. Official
Registry search always uses the local mirror; a catalog **Update** (Apps ›
Advanced › Catalogs, or the optional schedule) is the only other time a catalog
is contacted. Server eligibility remains authoritative. Source/publisher and
every merged attribution remain visible; unknown compatibility is not readiness.
The normal journey uses full-width item pages and server-run plans, with each
owner's retained editor under an item's Advanced settings.

MCP setup follows declared auth/runtime requirements, then tested tool acceptance
and existing policy. Skills review complete bounded files/scripts and availability;
packages review contents and required/optional child setup. Credentials configured,
authenticated, enabled, connected and successful tool use remain distinct facts.
In a chat, the composer's + › Apps switches and @mentions only narrow which apps a
turn may use (`scope.py`), and the agent's `suggest_apps` leaves a Connect card
that runs the app's normal consent and plan; neither changes the profile, model
or approvals.

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

No cloud marketplace, foreign SDK emulation, arbitrary bootstrap runner,
cross-plugin dependency resolver, Docker orchestration, Row-Bot-hosted OAuth
service, payments/ratings, automatic code translation or plugin UI extensions were
added. Catalogs update in the background only when the person turns the schedule
on (off by default). The published client ID metadata document is a static file
with no secret. Composio is a third-party hosted broker the person must turn on
after its disclosure. MCP Apps views are the only third-party UI, shown in a
sandboxed opaque-origin frame (`views.py`). The reviewed dependency subset and
first-party portable starter replace an unsafe one-click Snyk pilot while
retaining the required skill/MCP bundle lifecycle.


## Continuation validation boundaries

The October 2026 continuation reproduced the Hermes HTTP 301 failure, then verified
public catalog refresh through the exact migration above. This is discovery evidence,
not evidence that any listed integration works. Deterministic tests cover rejected
redirects, redirect loops, size limits, stale/invalid caches, owner-bound skill
previews, uncertain install recovery, and stable subdirectory update identities.
Existing lifecycle suites remain responsible for authentication, tool review,
profile policy, parent/child ownership, updates, restore, disabling and removal.

Live connections (sign-in where needed, a read, and the lifecycle steps each
recipe's evidence records) have since been validated on Windows for the recipes
marked `tested_with_row_bot` (see Source and recommendation evidence); every other
recipe still needs its own live account check with explicit user authorization. Any
real tool write requires separate approval. An external Hermes package needs a
recorded catalog SHA, complete content/prerequisite/data-disclosure review, and its
actual supported OS/runtime; catalog metadata alone is insufficient. macOS desktop
and Linux server sign-in callback behavior need their respective hosts. Installer,
signing, notarization and release checks are outside this continuation. Landing
media refresh remains with the landing task.

## Apps & Skills model (row_bot.integrations)

The domain package composes the owners above; it never replaces them.

- `apps.py` and `apps.json`: curated app identities (104, 98 of them featured and
  ranked), each with jobs, synonyms, example prompts, links, auth, vendor domains
  and GitHub orgs, and the vendor documentation that confirmed it. A record attaches
  only through a reviewed reference (curated recipe, Registry namespace or exact
  server, endpoint host, package or repository, or a first-party account or
  channel). Anything else is a community entry. The vendor badge comes only from
  rules: a Registry namespace that is a vendor domain reversed or
  `io.github.<vendor org>`, or a vendor endpoint. Shared hosting domains can never
  be vendor domains. To add an app, add one record.
- `index.py`: the local Registry mirror, a SQLite FTS5 index in `catalogs/` under the
  data folder. Start-up builds it from the shipped snapshot; an update builds a new
  generation and swaps one pointer. Searching only reads it.
- `catalogs.py`: explicit catalog updates (one background job per source that
  declares `network: explicit`) and the optional schedule, off by default, kept in
  `catalogs/state.json` with the Apps settings for views and brokers.
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
  - Anything that puts code on this computer (adding, updating or removing a plugin
    package, an npm, PyPI, container or bundle package, a Hermes recipe or a Registry
    bundle) needs the owner at this computer (`owner_local_only`).
  - The runner records each owner command before sending it, so a retry replays or
    reconciles it.
  - A plan pauses only for a browser sign-in, a missing input, access to newly
    discovered tools, a reviewed package lock, or a finished background step; a pause
    left for 30 minutes expires, keeping what was done. A plan whose launch recipe
    changed while it waited fails with `plan_changed` instead of continuing.
  - The access review binds the chosen preset, so tools reviewed under one preset are
    never saved under another.
  - An item's unfinished plan is returned with its detail and review, so it can
    always be continued or cancelled. Each owner has its own plan per item.
- `presets.py`: Read only, Ask before changes (default) and Full access on the
  existing per-tool policy (Off, Ask first, Use). Destructive, high-impact,
  approval-declaring and unknown-effect tools, and a broker's tools that run code or
  act for other apps, stay approval-locked under every preset. A routine change runs
  without asking only under Full access or a per-tool Use (`run_without_asking`).
  An app tool whose access says ask still asks under the Allow all approval mode.
  Tools discovered later wait to be accepted.
- `safe.py`: the one fetcher (catalogs, skill and plugin sources, npm metadata,
  runtime downloads, Registry bundles and icons), link cleaner, preview cache and
  file writer.
  - Fetches are https on port 443 only. Reviewed hosts use the system or environment
    proxy when one is set; every other host connects directly, never through a proxy.
  - A direct connection is made only to a checked public address. An address that
    cannot be connected to gives way to the host's next checked address (the last one
    gets the whole connect timeout), and is tried last for ten minutes. Through a
    proxy the address cannot be checked, so the reviewed host list is the guard.
  - Every redirect hop is rechecked, and credentials never cross hosts.
  - Responses are size-capped, never decompressed, and the whole fetch has a
    deadline. NAT64 and IPv4-compatible forms of private addresses are refused.
  - Files are written readable only by this account.
- `builtin.py`: Row-Bot's own accounts, channels and key-based tools as ways to
  connect an app, read from their owners (no service contact, no program started);
  each opens its owner's page.
- `inputs.py`: declared inputs (a key, a tenant, a folder) for every source; the
  template stays in the saved configuration and values are filled only at connect
  time, secrets from the keychain, never in a template, log or response.
- `scope.py`: which apps a chat turn may use (+ › Apps switches, @mentions) under the
  agent profile's ceiling, which app a tool belongs to, and `suggest_apps` results
  from local data only.
- `views.py`: MCP Apps views in chat, served once at `/app-views/{id}` under a CSP of
  the app's declared https domains, in an `allow-scripts`-only sandbox; a view's
  calls go through the chat's profile, the app's access and approvals.
- `brokers.py` and `broker_apps.json`: the Composio hosted broker, off until the
  person turns it on after its disclosure; found by Row-Bot's own list of app names.
- `windows_connectors.py`: On-device Agent Registry connectors, listed only on an
  explicit update and connected through the normal plan.
- `uploads.py`: privately staged skill or package archives and `.mcpb` bundles;
  nothing in them runs.
- `workflow_templates.py`: workflow templates, created switched off in Ask mode.

MCP owner functions take an explicit `target` (standalone or one package child);
there is no ambient target.
