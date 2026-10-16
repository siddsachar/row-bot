"""Learning's personal-model training (L2).

- ``hardware``: what this computer can train on (tiers, the driver, whether the card is free now).
- ``engine``, ``lockfile``, ``fetch``, ``weights``, ``downloads``: the training engine (with llama.cpp's
  export tools) and the starting model, downloaded only after the ask (L2b, L2c).
- ``jobs``, ``supervisor``, ``worker``, ``protocol``: training runs and their worker (L2a).
- ``recipes``, ``dataset``, ``planner``: what to train on and how (L2c).
- ``export``, ``serving``, ``releases``: a finished run becomes a version: exported, made ready in a
  local runtime and recorded with its identity (L2c).

Training code never reads the audit vault: ``row_bot.learning.training`` is one of the sealed
namespaces (``sealing.DENIED_PREFIXES``). The dataset asks ``evaluation`` to let the vault take its
share first and leaves out what it holds (O18).
"""
