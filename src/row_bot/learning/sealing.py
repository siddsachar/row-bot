"""Seals for Learning's hidden tests (E10).

The audit vault (``audit.py``) and the hidden splits of the task packs
(``packs/hidden.py``) are *sealed modules*: code that drafts changes, generates
practice, trains or evolves strategies can't import them or call their readers.

A sealed module checks who is asking on every use:

- **Denied callers** (the drafting, practice, training and strategy code, by module
  name) get :class:`SealError` for any name in the module, and a first import from
  such a module fails the same way.
- **Readers** (named per seal: ``evaluation`` for the vault, the vault for the hidden
  splits) may use everything.
- **Everyone else** may use only the seal's open names (deletion and status, never
  task content).

The content-returning methods check their caller again (:func:`require_reader`), so a
function taken out of the module some other way (its ``__dict__``, say) still refuses
anyone but a reader. What this can't stop: code that deliberately reads the vault's
file with SQLite, or digs objects out with ``gc``. It is a guard against design-level
coupling (the improver quietly selecting on its own audit), not against hostile code
running in the same process.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, field

# Drafting (diagnosis, changes, ways of working), practice, training and the improver's
# strategies (PLAN.md Architecture): none of them may read hidden tests.
DENIED_PREFIXES: tuple[str, ...] = (
    "row_bot.learning.diagnosis",
    "row_bot.learning.changes",
    "row_bot.learning.behaviour",
    "row_bot.learning.practice",
    "row_bot.learning.training",
    "row_bot.learning.improver",
    "learning_engine",
)


class SealError(ImportError):
    """A sealed module refused a caller. ``caller`` and ``name`` say who and what."""

    def __init__(self, sealed: str, caller: str, name: str) -> None:
        self.sealed = sealed
        self.caller = caller
        self.name = name
        super().__init__(f"{sealed} is sealed: {caller or 'unknown code'} may not use {name!r}")


def denied(module_name: str) -> bool:
    """Whether code in ``module_name`` is drafting, practice, training or strategy code."""

    name = str(module_name or "")
    return any(name == prefix or name.startswith(prefix + ".") for prefix in DENIED_PREFIXES)


@dataclass
class Seal:
    """Who may use a sealed module, and what everyone else may use."""

    name: str
    readers: frozenset[str]
    open_names: frozenset[str] = frozenset()
    # Told about every refusal (the vault logs it as a look); must not raise.
    on_refused: Callable[[str, str], None] | None = field(default=None, repr=False)

    def allows(self, caller: str, attribute: str) -> bool:
        if caller == self.name:
            return True
        if denied(caller):
            return False
        return attribute in self.open_names or caller in self.readers

    def refuse(self, caller: str, attribute: str) -> SealError:
        hook = self.on_refused
        if hook is not None:
            try:
                hook(caller, attribute)
            except Exception:  # noqa: BLE001 - a refusal is reported even if logging it fails
                pass
        return SealError(self.name, caller, attribute)


def _module_of(frame: types.FrameType | None) -> str:
    return str(frame.f_globals.get("__name__", "")) if frame is not None else ""


def _importer(sealed_frame: types.FrameType | None) -> str:
    """The module whose import statement is running the sealed module's code (the
    import machinery's own frames skipped)."""

    frame = sealed_frame.f_back if sealed_frame is not None else None
    while frame is not None:
        module = _module_of(frame)
        if not (frame.f_code.co_filename.startswith("<frozen importlib") or module == "importlib"
                or module.startswith("importlib.")):
            return module
        frame = frame.f_back
    return ""


class _SealedModule(types.ModuleType):
    def __getattribute__(self, attribute: str) -> object:
        if attribute.startswith("__") and attribute.endswith("__"):
            return super().__getattribute__(attribute)
        seal: Seal = super().__getattribute__("__row_bot_seal__")
        caller = _module_of(sys._getframe(1))
        if not seal.allows(caller, attribute):
            raise seal.refuse(caller, attribute)
        return super().__getattribute__(attribute)


def seal_module(module_name: str, seal: Seal) -> None:
    """Seal an already-running module (call it at the end of the module's own code).

    A first import from denied code fails here, before the module is usable.
    """

    module = sys.modules[module_name]
    importer = _importer(sys._getframe(1))
    if denied(importer):
        raise seal.refuse(importer, "import")
    module.__row_bot_seal__ = seal  # type: ignore[attr-defined]
    module.__class__ = _SealedModule


def require_reader(seal: Seal, what: str, *, depth: int = 2) -> str:
    """Refuse unless the code calling the content method is a reader; returns its module name."""

    caller = _module_of(sys._getframe(depth))
    if caller != seal.name and (denied(caller) or caller not in seal.readers):
        raise seal.refuse(caller, what)
    return caller


__all__ = ["DENIED_PREFIXES", "Seal", "SealError", "denied", "require_reader", "seal_module"]
