"""Proactive Row-Bot (5.3.4): Row-Bot notices things on its own, and stays quiet unless they matter.

One pipeline turns what changed into *moments*, each one thing worth the person's attention::

    watchers (deterministic, local) -> candidates -> rules -> moments
    -> budget and timing (boundaries, quiet hours, desktop context) -> surface -> feedback

- ``model``: what a candidate, a moment and the desktop's state are.
- ``store``: ``proactive.db`` in the data folder: moments, the Activity log, watcher cursors, the person's
  rules and each kind's acted / dismissed / ignored counts.
- ``settings``: the person's choices (level, quiet hours, sources, kinds, desktop context, urgent kinds).
- ``desktop_context``: idle, locked, Focus / Do Not Disturb, mic or camera in use and the foreground app's
  kind, read locally for timing only; never titles, contents or pixels.
- ``timing``: the attention budget: daily caps, per-kind cooldowns, quiet hours and boundaries.
- ``feedback``: acted / dismissed / not now / less like this, and the rules they write.
- ``watchers``: the framework watchers run in; each keeps a cursor and speaks only on change.
- ``engine``: the scheduler's tick that runs all of it. Silence is its usual result.

Nothing here reaches the network by itself, and nothing about proactivity leaves the computer.
"""
