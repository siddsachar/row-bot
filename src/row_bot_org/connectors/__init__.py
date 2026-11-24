"""Company connectors (B2): a SharePoint or OneDrive drive or folder (Microsoft Graph), or a Google Drive shared
drive or folder, synced into a company-brain space, each file's upstream permissions mirrored onto its document.

- ``upstream``: the two sources' HTTP calls (their documented hosts only, a timeout on each, no redirect followed but
  Graph's download to SharePoint) and what they say, as ``Item``, ``Page`` and ``mirror.Grant``.
- ``mirror``: a file's grants to its document's readers; anything unmappable makes it admins only.
- ``secrets``: a connector's secret sealed with the server key (S4's AES-256-GCM).
- ``store``: the SQL (migration 0011).
- ``sync``: ``ConnectorService``, the brain worker's connector stage (bounded, with backoff), and the connection test.
- ``web``: the admin routes.

Needs the ``org`` dependency group (psycopg, cryptography) and the server key (``ROW_BOT_ORG_GATEWAY_KEY``).
"""
