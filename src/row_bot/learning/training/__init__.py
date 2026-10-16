"""Learning's personal-model training (L2). Only the hardware check exists so far (L1a).

Training code never reads the audit vault: ``row_bot.learning.training`` is one of the sealed
namespaces (``sealing.DENIED_PREFIXES``).
"""
