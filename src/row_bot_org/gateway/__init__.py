"""The model catalog and gateway: the company's model endpoints, held once on the server.

IT adds endpoints (OpenAI-compatible servers and Anthropic) and the models under them through the
admin API. Connected Row-Bots list the models they may use (``GET /api/models``) and call them through
the server (``/api/gateway/...``), which adds the endpoint's key, refuses models the person may not use
or calls over a hard budget, passes streams through, and records each call's tokens and cost.

- ``settings``: ``ROW_BOT_ORG_GATEWAY_KEY``, the server key that seals endpoint keys at rest.
- ``keys``: sealing and opening endpoint keys (AES-256-GCM).
- ``store``: the database calls (migration 0003).
- ``upstream``: the calls to endpoints, usage from their answers and streams.
- ``web``: the routes.

Keys never leave the server: no response, log record or error carries one. No request or response
body is ever logged.
"""
