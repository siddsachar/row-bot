# Integration source fixtures

`registry-v01.json` is synthetic metadata shaped from the documented Registry
v0.1 server envelope (https://modelcontextprotocol.io/registry/registry-aggregators),
reviewed 2026-10-03. Names, version, host and descriptions are fixture-only; no
upstream package, account, credential or service response is embedded. Tests never
contact its example.test URL. Live metadata provenance is recorded separately in
the shipped bounded snapshot and `docs/INTEGRATION_SOURCES.md`.
