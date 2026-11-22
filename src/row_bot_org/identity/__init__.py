"""Company sign-in for the Org server: an OpenID Connect relying party, principals, groups and sessions.

- ``config``: the identity provider settings (no dependencies beyond the standard library).
- ``secrets``: the random secrets sign-in hands out and their HMAC verifiers.
- ``oidc``: discovery, the authorization request, the token exchange and ID token validation (joserfc).
- ``limits``: the per-address limit on the two sign-in steps (standard library only).
- ``store``: the database side (migration 0002), through ``SECURITY DEFINER`` functions before a
  principal is known and ``principal_transaction`` after.
- ``web``: the ``/auth/*``, ``/api/me`` and ``/admin/`` landing routes; ``gate``: the gate every route passes (a session, and the same
  origin for anything that changes state), the access log and ``request_principal``, the dependency
  every later router uses.

Needs the ``org`` dependency group (joserfc, psycopg) except for ``config``, ``limits`` and ``gate``.
"""
