"""The training engine's entry point (O43, E14). Standard library only until the engine's
site-packages folder is added; never imports Row-Bot.

Row-Bot runs it with the engine's own **base interpreter** (never a virtual environment's
launcher) and ``-I -S``::

    python -I -S entry.py check --site <site-packages>
    python -I -S entry.py train --site <site-packages> --protocol <protocol.py> --model <model folder>
    python -I -S entry.py fit --site <site-packages> --model <folder> --rows <jsonl> --out <json> [...]
    python -I -S entry.py export --site <site-packages> --model <folder> --adapter <folder> --out <folder> [...]

Before anything else it

1. switches off every library's usage reporting and Hub access (the same switches the host
   sets: ``HF_HUB_DISABLE_TELEMETRY``, ``HF_HUB_OFFLINE``, ``UNSLOTH_DISABLE_STATISTICS``,
   ``UNSLOTH_AUTO_INSTALL=0``, ``USE_HUB_KERNELS=NO``, ``WANDB_MODE=disabled``, ...);
2. installs a network guard: an audit hook that refuses every connection and name lookup that
   isn't this computer, so a library that ignored the switches fails instead of phoning home;
3. keeps its standard output private (anything a library prints goes to standard error), and
   in ``train`` its standard input too (the process's own reads nothing);
4. adds the engine's site-packages folder (``site.addsitedir``, so its ``.pth`` files work).

``check`` prints one JSON line: whether every package imports and whether PyTorch sees the
graphics card. ``train`` speaks Row-Bot's training protocol (``trainer.py``).
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import threading

# E14, O27: set before any engine library is imported (the host sets them too).
SWITCHES = {
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_UPDATE_CHECK": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "HF_HUB_ENABLE_HF_TRANSFER": "0",
    "HF_DATASETS_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "UNSLOTH_DISABLE_STATISTICS": "1",
    "UNSLOTH_AUTO_INSTALL": "0",
    "USE_HUB_KERNELS": "NO",
    "WANDB_MODE": "disabled",
    "WANDB_DISABLED": "true",
    "TRACKIO_DISABLED": "1",
    "COMET_MODE": "DISABLED",
    "TOKENIZERS_PARALLELISM": "false",
}

_LOCAL_NAMES = {"localhost", "localhost.localdomain", "", None}
_blocked = {"count": 0}
_blocked_lock = threading.Lock()


def apply_switches() -> dict[str, str]:
    for key, value in SWITCHES.items():
        os.environ[key] = value
    return dict(SWITCHES)


def _loopback(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host in _LOCAL_NAMES:
        return True
    text = str(host).split("%", 1)[0]
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return text.lower() in _LOCAL_NAMES


def _refuse(what: str) -> None:
    with _blocked_lock:
        _blocked["count"] += 1
    sys.stderr.write(f"row-bot engine: network guard refused {what}\n")
    raise PermissionError("The training engine never uses the network")


def _hook(event: str, args: tuple) -> None:
    if event == "socket.connect":
        sock, address = args[0], args[1]
        family = getattr(sock, "family", None)
        if family is not None and int(family) not in (socket.AF_INET, socket.AF_INET6):
            return
        host = address[0] if isinstance(address, tuple) and address else address
        if not _loopback(host):
            _refuse("a connection")
    elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
                   "socket.gethostbyaddr"):
        if args and not _loopback(args[0]):
            _refuse("a name lookup")
    elif event == "socket.sendto":
        address = args[1] if len(args) > 1 else None
        host = address[0] if isinstance(address, tuple) and address else address
        if host is not None and not _loopback(host):
            _refuse("a datagram")


def install_guard() -> None:
    """Refuse every non-loopback connection from here on (Python's own sockets and every
    library built on them). This computer's own name stays local: PyTorch looks it up."""

    try:
        _LOCAL_NAMES.add(socket.gethostname())
    except OSError:
        pass
    sys.addaudithook(_hook)


def blocked() -> int:
    with _blocked_lock:
        return _blocked["count"]


def private_stdout():
    """Keep the real standard output for Row-Bot's messages; prints go to standard error."""

    writer = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return writer


def private_stdin():
    """Keep the real standard input for Row-Bot's messages; the process's own reads nothing.

    On Windows a thread waiting on the input pipe blocks anything that merely asks what kind of
    file standard input is, and a library's C runtime asks as its DLL loads (NumPy, PyTorch):
    the trainer would hang at ``import torch``. So only this private copy ever sees the pipe.
    """

    import io

    reader = io.BufferedReader(os.fdopen(os.dup(0), "rb", buffering=0))
    nothing = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nothing, 0)
    os.close(nothing)
    if os.name == "nt":
        import ctypes
        import msvcrt

        ctypes.windll.kernel32.SetStdHandle(-10, msvcrt.get_osfhandle(0))  # STD_INPUT_HANDLE
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - the process's own, for its life
    return reader


def add_site(site: str) -> None:
    import site as site_module

    if not os.path.isdir(site):
        raise SystemExit(f"row-bot engine: no site-packages at {site}")
    site_module.addsitedir(site)


def _arguments(argv: list[str]) -> tuple[str, dict[str, str]]:
    if not argv:
        raise SystemExit("row-bot engine: say check, train, fit or export")
    mode, options, index = argv[0], {}, 1
    while index < len(argv):
        key = argv[index]
        if not key.startswith("--") or index + 1 >= len(argv):
            raise SystemExit(f"row-bot engine: unexpected {key!r}")
        options[key[2:].replace("-", "_")] = argv[index + 1]
        index += 2
    return mode, options


def check(writer) -> int:
    """Import the engine's packages and ask for the graphics card; one JSON line on ``writer``."""

    report: dict[str, object] = {"ok": False, "python": ".".join(map(str, sys.version_info[:3]))}
    try:
        import torch

        report["torch"] = torch.__version__
        report["torch_cuda"] = torch.version.cuda
        report["cuda"] = bool(torch.cuda.is_available())
        packages: dict[str, str] = {}
        if report["cuda"]:
            report["gpu"] = torch.cuda.get_device_name(0)
            # Unsloth needs the graphics card to import at all, and patches the others: first,
            # as the trainer imports it.
            import unsloth

            packages["unsloth"] = unsloth.__version__
        import accelerate
        import bitsandbytes  # noqa: F401
        import datasets
        import peft
        import transformers
        import trl

        packages.update({"transformers": transformers.__version__, "trl": trl.__version__,
                         "peft": peft.__version__, "accelerate": accelerate.__version__,
                         "datasets": datasets.__version__})
        report["packages"] = packages
        report["ok"] = True
    except BaseException as error:  # noqa: BLE001 - reported to Row-Bot as a failed check
        import traceback

        report["error"] = type(error).__name__
        traceback.print_exc(file=sys.stderr)  # the host keeps the end of it in its log
    report["blocked_connections"] = blocked()
    writer.write((json.dumps(report) + "\n").encode("utf-8"))
    writer.flush()
    return 0 if report["ok"] else 3


def main(argv: list[str] | None = None) -> int:
    mode, options = _arguments(list(sys.argv[1:] if argv is None else argv))
    apply_switches()
    install_guard()
    writer = private_stdout()
    reader = private_stdin() if mode == "train" else None
    add_site(options.get("site", ""))
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    if mode == "check":
        return check(writer)
    import trainer  # the engine's trainer, beside this file

    if mode == "train":
        return trainer.serve(writer, reader, protocol_path=options["protocol"], model_dir=options["model"])
    if mode == "fit":
        return trainer.fit_main(writer, options)
    if mode == "export":
        return trainer.export_main(writer, options)
    raise SystemExit(f"row-bot engine: unknown mode {mode!r}")


if __name__ == "__main__":
    raise SystemExit(main())
