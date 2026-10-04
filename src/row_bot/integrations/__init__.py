"""Apps & Skills domain model: identities, sources, owner facts, plans and presets.

Owners (skills, MCP, plugins) stay authoritative. Modules here import owners
lazily so that owners may import the shared safety helpers in ``safe``.
"""
