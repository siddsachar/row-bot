"""A recording stand-in for pywebview, used to run the launcher's window script.

Put this directory first on ``PYTHONPATH``. ``start()`` runs the scenario named
by ``FAKE_WEBVIEW_SCENARIO`` from the module in ``FAKE_WEBVIEW_SCENARIO_FILE``
against the window script (``__main__``) and writes a JSON report to
``FAKE_WEBVIEW_REPORT``. No real window, browser engine or network is used.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import sys
from typing import Any, Callable

OPEN_DIALOG = 10
FOLDER_DIALOG = 20
SAVE_DIALOG = 30

screens: list[Any] = []
windows: list["Window"] = []
_ids = itertools.count(1)
log: list[list[Any]] = []


class Event:
    def __init__(self) -> None:
        self.handlers: list[Callable[..., Any]] = []

    def __iadd__(self, handler: Callable[..., Any]) -> "Event":
        self.handlers.append(handler)
        return self

    def fire(self, *args: Any) -> list[Any]:
        return [handler(*args) for handler in list(self.handlers)]


class Events:
    def __init__(self) -> None:
        for name in ("closed", "closing", "moved", "loaded", "before_load", "shown"):
            setattr(self, name, Event())


class Window:
    def __init__(self, title: str, url: str, options: dict[str, Any]) -> None:
        self.uid = f"window-{next(_ids)}"
        self.title = title
        self.url = url
        self.options = options
        self.events = Events()
        self.scripts: list[str] = []
        self.exposed: dict[str, Callable[..., Any]] = {}
        self.visible = not options.get("hidden", False)
        self.destroyed = False
        windows.append(self)

    def _note(self, action: str, *args: Any) -> None:
        log.append([self.uid, action, *args])

    def get_current_url(self) -> str:
        return self.url

    def load_url(self, url: str) -> None:
        self.events.before_load.fire()
        self.url = url

    def move(self, x: int, y: int) -> None:
        self._note("move", x, y)

    def show(self) -> None:
        self.visible = True
        self._note("show")

    def hide(self) -> None:
        self.visible = False
        self._note("hide")

    def restore(self) -> None:
        self._note("restore")

    def destroy(self) -> None:
        self.destroyed = True
        self._note("destroy")
        self.events.closed.fire()

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)

    run_js = evaluate_js

    def expose(self, *functions: Callable[..., Any]) -> None:
        for function in functions:
            self.exposed[function.__name__] = function

    def load(self) -> None:
        """The page finished loading (pywebview's ``loaded`` event)."""
        self.events.loaded.fire()

    def proof(self) -> dict[str, Any]:
        """The document proof the native bridge injected on load."""
        for script in reversed(self.scripts):
            marker = "const proof = "
            if marker in script:
                return json.loads(script.split(marker, 1)[1].split("; ", 1)[0])
        raise AssertionError("no native proof injected")

    def dispatch(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.exposed["native_client_dispatch"](self.proof(), operation, payload)


def create_window(title: str = "", url: str = "", *args: Any, **kwargs: Any) -> Window:
    if args:
        raise TypeError("unexpected positional window arguments")
    title = kwargs.pop("title", title)
    url = kwargs.pop("url", url)
    window = Window(title, url, kwargs)
    log.append([window.uid, "create", title, url, sorted(kwargs)])
    return window


def start(func: Callable[[], Any] | None = None, **_options: Any) -> None:
    script = sys.modules["__main__"]
    spec = importlib.util.spec_from_file_location(
        "fake_webview_scenarios", os.environ["FAKE_WEBVIEW_SCENARIO_FILE"]
    )
    assert spec and spec.loader
    scenarios = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scenarios)
    report: dict[str, Any] = {}
    try:
        getattr(scenarios, os.environ["FAKE_WEBVIEW_SCENARIO"])(sys.modules[__name__], script, report)
    except Exception as error:  # the test reads the failure from the report
        import traceback

        report["error"] = "".join(traceback.format_exception(error))
    report["log"] = log
    with open(os.environ["FAKE_WEBVIEW_REPORT"], "w", encoding="utf-8") as stream:
        json.dump(report, stream, default=repr)
