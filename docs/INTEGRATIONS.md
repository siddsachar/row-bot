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

- Official MCP Registry v0.1: nested server metadata, lifecycle status and bounded
  cursor pagination; exact source IDs are preserved rather than deduplicating by name.
- ClawHub v1: resolve a version once, acquire its complete zip or a validated pinned
  public GitHub handoff, preserve resources, then scan the reviewed tree. Publisher
  handles remain part of search identity, acquisition and update pins: the live API
  permits different publishers to share a slug. An explicit
  moderation verdict found during an update check is retained and blocks reactivation;
  a network failure never becomes a moderation verdict.
- Hermes plugin catalog: repo/SHA/subdirectory plus publisher removal information.
  Optional-MCP manifests are fetched individually from a captured repository pin.
  Recipes with install/bootstrap or foreign OAuth-client requirements are unavailable.
- Starters: shipped Local text tools 1.0.0 (MIT, no server network or telemetry),
  existing Hello Tool 0.1.0 at repository commit
  `9435afbc930799ec30a622a2eb3d234a05214f31`, and vendor-hosted Notion MCP
  (`https://mcp.notion.com/mcp`, vendor-managed endpoint, hosted terms).
- The Hermes Snyk candidate at `2a41a07f81e45125bf82a19af1b13396ace4b81f`
  declares analytics off, but npm `snyk@1.1306.0` has a postinstall bootstrap and
  unpinned transitive dependencies. It is not a starter or an automatically prepared
  dependency. No private repository scans or third-party installs were performed.

Fixtures record synthetic source identities and must never contain credentials or
private paths. Live-account and macOS/Linux/clean-machine checks remain distinct
from deterministic Windows verification. See the user guide for recovery limits.

## Deliberate boundaries

No cloud marketplace, background discovery/update, foreign SDK emulation, arbitrary
bootstrap runner, cross-plugin dependency resolver, Docker orchestration, hosted
OAuth broker, payments/ratings, automatic code translation or UI extensions were
added. The reviewed dependency subset and first-party portable starter replace an
unsafe one-click Snyk pilot while retaining the required skill/MCP bundle lifecycle.
