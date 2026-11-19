"""The replication's child processes (``replicate.py``), one at a time, each in its own scratch profile
``<scratch>/<child>/data``.

Row-Bot binds its data folder when it is imported, so ``replicate.py`` starts this module with ``ROW_BOT_DATA_DIR`` set
to that profile:

- ``teacher``: the eval profile's ChatGPT sign-in in this process's memory and the teacher's model chosen once
  (``worker.LiveKit.teacher``, as the study's teacher arm), then every fresh request ``runs`` times. It never books the
  card.
- ``local``: Row-Bot's llama.cpp server files hard-linked from the study's profile (``explore_worker.link_server``,
  never its ``run`` folder), then for each local arm in §4's order: its GGUF's hash checked against ``models.json``, the
  arm's model alone on this profile's server list at the arm's context under the study's own name (so the model ref is
  the study's and Row-Bot sees the arm's window), loaded, probed and unloaded under a booking, the prompt envelope set
  (65,536: the study's small-context profile kept; 32,768: the product's own, as the study served it), and every fresh
  request ``runs`` times. It never touches the eval profile.

A run is the study's (``runner.StudyRunner``): a model-call error is rerun once at once, and each run is recorded under
SA2 with its unrecovered calls (``explore_worker.run_record``), saved after every run; a child started again continues
from its ``runs.jsonl``. Before every run the replication's ChatGPT-lane tokens are checked against the cap (exit 75);
a usage or rate limit stops at once (exit 76). Between a local arm's runs the card goes back for the person's quiet
hours (no new GPU work from 00:25 to 08:00), their own training and another lane that waits or opened an EXCLUSIVE
window. Its kit is :class:`LiveKit`, or :class:`DryKit` for the dry run (the scripted model on loopback, ports
8140-8149, every arm its own endpoint declared at the arm's context).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.e2e.learning.scaling import dry_run, explore_worker, worker
from tests.e2e.learning.scaling.explore import is_overflow, read_lines
from tests.e2e.learning.scaling.replicate import (
    CONTEXT_OF,
    LABEL,
    LANE,
    LOCAL,
    MODEL_OF,
    MODELS,
    ReplicationDesign,
    check_models,
    sha256_file,
)
from tests.e2e.learning.scaling.runner import ProviderLimit, Spec, StudyRunner, yield_card
from tests.e2e.learning.scripted_model import ScriptedModel
from tests.e2e.learning.seeded.scratch import GpuGate, ScratchError, card_memory_mib, check_scratch, exclusive_open

logger = logging.getLogger("row_bot.scaling")

EXIT_TOKEN_CAP = worker.EXIT_TOKEN_CAP
EXIT_PROVIDER_LIMIT = worker.EXIT_PROVIDER_LIMIT
EXIT_REFUSED = 2
#: The dry run's loopback ports.
PORTS = range(8140, 8150)


# ── The card ─────────────────────────────────────────────────────────────────


def gives_back(gpu: GpuGate) -> bool:
    """Whether a local arm gives the card back between runs: the study's rule (a quiet hour, the participant's own
    training, another lane that wrote it waits) or another lane's open EXCLUSIVE window (§8)."""

    if yield_card(gpu):
        return True
    try:
        text = gpu.booking.read_text(encoding="utf-8") if gpu.booking else ""
    except OSError:
        text = ""
    return any(lane != gpu.lane for lane in exclusive_open(text))


class ReplicationRunner(StudyRunner):
    """The study's runner, booking the card as the replication's lane and giving it back by :func:`gives_back`."""

    def card(self, arm: str) -> None:
        if self._arm and self._arm != arm:
            self.unload(self._arm)
        self._arm = arm
        if not self.arms[arm].local:
            self.gpu.release()
            return
        if self.gpu.held is not None and gives_back(self.gpu):
            self.gpu.release()
        self.gpu.acquire(f"replication: {arm} runs",
                         expect="about 2 h, one arm's runs (the card goes back between runs when asked), up to 17 GB")


# ── The prompt envelope ──────────────────────────────────────────────────────

_PRODUCT_SMALL_PROFILE: int | None = None


def set_envelope(context: int) -> int:
    """The study's small-context profile at ``context``: kept up to it for a window above the product's own threshold
    (the exploratory tool's ``--envelope study``), the product's own threshold otherwise (as the study served 32K)."""

    global _PRODUCT_SMALL_PROFILE
    from row_bot import agent

    if _PRODUCT_SMALL_PROFILE is None:
        _PRODUCT_SMALL_PROFILE = int(agent.SMALL_PROFILE_TOKENS)
    agent.SMALL_PROFILE_TOKENS = max(_PRODUCT_SMALL_PROFILE, int(context))
    return int(agent.SMALL_PROFILE_TOKENS)


def sees(model_ref: str) -> tuple[int, bool]:
    """The window Row-Bot sees for ``model_ref`` and whether it gives it the small-context profile."""

    from row_bot import agent, models

    size = int(models.get_context_size(model_ref))
    return size, bool(agent._small_context_profile(size))  # noqa: SLF001 - the runtime's own profile rule


# ── Kits ─────────────────────────────────────────────────────────────────────


class LiveKit(explore_worker.LiveKit):
    """The real one: the eval profile's sign-in for the teacher, Row-Bot's own llama.cpp server in this profile and
    the shared card for the local arms."""

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)
        self.study_kit = worker.LiveKit(args)

    def teacher(self, out: Path) -> str:
        return self.study_kit.teacher(out)

    def serve_arm(self, arm: str, model: tuple[str, str, Path], context: int, gpu: GpuGate) -> dict[str, Any]:
        """``arm``'s model alone on this profile's server list at ``context`` (so Row-Bot's endpoint declares that
        window), under the study's name, loaded, probed and unloaded."""

        from row_bot.learning.training import llama_server, serving

        ref, name, gguf = model
        server = llama_server.get(self.data)
        gpu.acquire(f"serving {arm} at {context:,} tokens (loaded, probed and unloaded)", expect="2 min, 17 GB")
        try:
            baseline = card_memory_mib()
            for entry in server.models():
                if entry.name != name:
                    server.unregister(entry.name)
            verifier = explore_worker._MeasuringVerifier()  # noqa: SLF001 - the exploratory tool's own probe
            outcome = serving.serve(serving.ROW_BOT, version=0, gguf=gguf, serve_context=context, folder=self.data,
                                    root=self.data, tag=name, tool_turn=False, verifier=verifier)
            if outcome.state != "ready" or outcome.model_ref != ref:
                raise ScratchError(f"{arm} couldn't be served at {context:,} tokens ({outcome.reason or 'as '}"
                                   f"{outcome.model_ref if outcome.state == 'ready' else ''}, not {ref})")
            found = {"model_ref": outcome.model_ref, "name": name, "gguf": str(gguf), "context": int(context),
                     "listed": [entry.to_dict() for entry in server.models()], "vram_mib": verifier.memory,
                     "baseline_mib": baseline, "probe": dict(outcome.probe), "seconds": round(float(outcome.seconds), 1)}
            self._wait_stopped(name)
            found["after_unload_mib"] = card_memory_mib()
        finally:
            gpu.release()
        return found

    def gpu(self) -> GpuGate:
        return GpuGate(Path(self.args.gpu_booking) if self.args.gpu_booking else None, self.args.lane, label=LABEL,
                       quiet=explore_worker.new_work_quiet)


class DryModel(ScriptedModel):
    """The scripted model on the first free port of 8140-8149 (and no other)."""

    def __init__(self) -> None:  # never binds port 0 first, as ScriptedModel's own would
        self.lock = threading.Lock()
        self.expression = "1 + 1"
        self.requests = 0
        self.agent_requests = 0
        self.tools_seen: set[str] = set()
        for port in PORTS:
            try:
                self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
                break
            except OSError:
                continue
        else:
            raise OSError("no free port in 8140-8149")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)


class DryKit(dry_run.DryKit):
    """The dry run's: the scripted model as the teacher and every local arm, each arm its own endpoint declared at the
    arm's context."""

    def boot(self, data: Path) -> None:
        if self.model is None:
            self.model = DryModel().__enter__()

    def _endpoint(self, name: str, context: int | None = None) -> str:
        from row_bot.providers import custom
        from row_bot.providers.selection import model_ref

        endpoint = f"{dry_run.PREFIX}{name}"
        record = {"id": endpoint, "name": f"Dry run {name}", "base_url": self.model.base_url, "profile": "llama_cpp",
                  "execution_location": "local", "auth_required": False}
        if context:
            record["manual_capabilities"] = {"context_window": int(context)}
        custom.save_custom_endpoint(record)
        custom.refresh_custom_endpoint_models(endpoint)
        custom.probe_custom_endpoint(endpoint, dry_run.ALIAS)
        return model_ref(custom.custom_provider_id(endpoint), dry_run.ALIAS)

    def prepare(self, study_data: Path) -> None:
        return None

    def serve_arm(self, arm: str, model: tuple[str, str, Path], context: int, gpu: GpuGate) -> dict[str, Any]:
        _ref, _name, gguf = model
        return {"model_ref": self._endpoint(arm, context), "name": f"{dry_run.PREFIX}{arm}", "gguf": str(gguf),
                "context": int(context), "vram_mib": None, "baseline_mib": None, "probe": {"agent_ready": True},
                "seconds": 0.0}

    def unload(self, name: str) -> None:  # type: ignore[override]
        return None

    def finish(self) -> None:
        return None

    def gpu(self) -> GpuGate:  # type: ignore[override]
        return super().gpu(LABEL)


def kit_for(args: argparse.Namespace, design: ReplicationDesign) -> Any:
    return DryKit(args) if design.dry_run else LiveKit(args)


# ── The runs ─────────────────────────────────────────────────────────────────


class ArmRuns:
    """One child's arms: every fresh request ``runs`` times per arm, saved after each run (see the module's
    docstring). ``task`` is the child (``teacher`` or ``local``), its state and runs under ``<out>/<task>``."""

    def __init__(self, args: argparse.Namespace, kit: Any, task: str,
                 hasher: Callable[[Path], str] = sha256_file) -> None:
        self.args = args
        self.kit = kit
        self.task = task
        self.hasher = hasher
        self.data, self.out = Path(args.data), Path(args.out)
        saved = json.loads((self.out / "design.json").read_text(encoding="utf-8"))
        self.design = ReplicationDesign.from_dict(saved["replication"])
        self.study_out, self.study_data = Path(saved["study_out"]), Path(saved["study_data"])
        self.folder = self.out / task
        self.state = worker.JsonState(self.folder / "state.json")
        self.runner: Any = None
        #: Each local arm's model name on the server, for unloading it.
        self.names: dict[str, str] = {}

    # ── Set-up ──────────────────────────────────────────────────────────

    def start(self, arms: Mapping[str, Spec], gpu: GpuGate) -> tuple[dict[str, Any], list[str]]:
        """The fresh requests and the runner (once per process, after the profile is prepared)."""

        requests, owned, people = worker.owners(self.design.scaling(), "held_out")
        self.runner = ReplicationRunner(data=self.data, arms=arms, owners=owned, gpu=gpu,
                                        task_tokens=self.design.task_tokens, seconds=self.design.seconds)
        self.runner.boot(people)
        self.runner.on_run = self.spend
        self.state.data.update({"tasks": len(requests), "runs_per_task": self.design.runs})
        self.state.save()
        return requests, list(requests)

    def spend(self, arm: str, case: Any) -> None:
        """Every run's tokens, saved at once (a resumed child counts what it spent before)."""

        cloud = max(0, int(case.tokens) - int(case.local_tokens))
        self.state.data["cloud_tokens"] = int(self.state.data.get("cloud_tokens", 0)) + cloud
        tokens = self.state.data.setdefault("tokens", {}).setdefault(arm, {"cloud": 0, "local": 0})
        tokens["cloud"] += cloud
        tokens["local"] += int(case.local_tokens)
        self.state.save()

    def remaining(self, arm: str, ids: Sequence[str]) -> int:
        done = {(item["task"], item["repeat"]) for item in read_lines(self.folder / "runs.jsonl") if item["arm"] == arm}
        return sum(1 for request_id in ids for repeat in range(self.design.runs) if (request_id, repeat) not in done)

    # ── The runs of one arm ─────────────────────────────────────────────

    def held_out(self, arm: str, requests: Mapping[str, Any], ids: Sequence[str]) -> None:
        """Every fresh request ``runs`` times (a model-call error once more, as the study did), each recorded under SA2
        and saved; the token cap is checked before every run."""

        path = self.folder / "runs.jsonl"
        done = {(item["arm"], item["task"], item["repeat"]) for item in read_lines(path)}
        started = time.monotonic()
        try:
            for request_id in ids:
                request = requests[request_id]
                for repeat in range(self.design.runs):
                    if (arm, request_id, repeat) in done:
                        continue
                    worker.cloud_check(self.state, self.args)
                    overflowed, attempt = 0, 0
                    for attempt in range(2):
                        run = self.runner.run_task(request.task, arm)
                        overflowed += int(is_overflow({"status": run.case.status, "detail": run.case.detail}))
                        if run.case.status != "error" or attempt:
                            break
                    worker.append_lines(path, [explore_worker.run_record(arm, request, repeat, run, reran=attempt == 1,
                                                                         overflowed=overflowed)])
                    self.gpu()
        finally:
            wall = self.state.data.setdefault("wall_seconds", {})
            wall[arm] = round(float(wall.get(arm, 0.0)) + time.monotonic() - started, 1)
            self.gpu()

    def gpu(self) -> None:
        seconds = self.state.data.setdefault("gpu_seconds", {})
        for arm, value in self.runner.gpu_seconds.items():
            seconds[arm] = round(float(seconds.get(arm, 0.0)) + value, 1)
        self.runner.gpu_seconds.clear()
        self.state.save()

    # ── The two children ────────────────────────────────────────────────

    def teacher(self) -> int:
        """The teacher on every fresh request: the ChatGPT lane, no GPU."""

        worker.prepare_profile(self.data, SimpleNamespace(runtimes_from=""))
        self.kit.boot(self.data)
        gpu = self.kit.gpu()
        model_ref = self.kit.teacher(self.out)
        requests, ids = self.start({"teacher": Spec("teacher", model_ref, local=False)}, gpu)
        try:  # recorded for the report only: the teacher is served as the study served it
            size, small = sees(model_ref)
        except Exception:  # noqa: BLE001
            logger.warning("couldn't read the teacher's window", exc_info=True)
            size, small = None, None
        self.state.data.update({"arm": "teacher", "model_ref": model_ref, "effective_context": size,
                                "small_profile": small})
        self.state.save()
        try:
            self.held_out("teacher", requests, ids)
        finally:
            self.runner.finish()
        self.state.data["done"] = True
        self.state.save()
        return 0

    def local(self) -> int:
        """The local arms in §4's order, each served at its context just before its runs."""

        teacher = worker.JsonState(self.out / "teacher" / "state.json").data
        if not teacher.get("done"):
            raise ScratchError("the local arms run after the teacher (§4): the teacher hasn't finished")
        models = explore_worker.study_arms(self.study_out, self.study_data, MODELS)
        worker.prepare_profile(self.data, SimpleNamespace(runtimes_from=""))
        self.kit.prepare(self.study_data)
        self.kit.boot(self.data)
        gpu = self.kit.gpu()
        requests, ids = self.start({}, gpu)
        try:
            for arm in LOCAL:
                if not self.remaining(arm, ids):
                    continue
                self.arm(arm, models[MODEL_OF[arm]], gpu)
                self.held_out(arm, requests, ids)
        finally:
            self.runner.finish()
            self.gpu()
            self.kit.finish()
        self.state.data["done"] = True
        self.state.save()
        return 0

    def arm(self, arm: str, model: tuple[str, str, Path], gpu: GpuGate) -> None:
        """Before ``arm``'s runs: its file's hash checked, the last arm's model unloaded, its model served alone at its
        context, the envelope set, and Row-Bot seeing the arm's window with the small-context profile."""

        context = CONTEXT_OF[arm]
        check_models(self.out, {MODEL_OF[arm]: model[2]}, before=f"serving {arm}", hasher=self.hasher, record=False)
        self.runner.finish()
        started = time.monotonic()
        served = self.kit.serve_arm(arm, model, context, gpu)
        serving = self.state.data.setdefault("serving_seconds", {})
        serving[arm] = round(float(serving.get(arm, 0.0)) + time.monotonic() - started, 1)
        threshold = set_envelope(context)
        self.runner.arms[arm] = Spec(arm, str(served["model_ref"]), local=True)
        self.names[arm] = str(served["name"])
        self.runner.unload = lambda which: self.kit.unload(self.names[which])
        size, small = sees(served["model_ref"])
        served.update({"effective_context": size, "small_profile": small, "small_profile_tokens": threshold})
        self.state.data.setdefault("serving", {})[arm] = served
        self.state.save()
        if size != context or not small:
            raise ScratchError(f"{arm}: Row-Bot sees a {size:,}-token window (small-context profile {small}), not "
                               f"{context:,} with the study's small-context profile")


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One child of the replication (started by replicate.py)")
    parser.add_argument("task", choices=("teacher", "local"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--eval-profile", default="")
    parser.add_argument("--gpu-booking", default="")
    parser.add_argument("--lane", default=LANE)
    parser.add_argument("--cap-tokens", type=int, required=True)
    parser.add_argument("--spent-before", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    profile = Path(args.eval_profile) if args.eval_profile else None
    check_scratch(Path(args.data), profile)
    check_scratch(Path(args.out), profile)
    design = ReplicationDesign.from_dict(json.loads((Path(args.out) / "design.json").read_text(
        encoding="utf-8"))["replication"])
    if args.task == "local" and args.eval_profile:
        logger.error("replication local refused: the local arms never touch the eval profile")
        return EXIT_REFUSED
    runs = ArmRuns(args, kit_for(args, design), args.task)
    try:
        return runs.teacher() if args.task == "teacher" else runs.local()
    except SystemExit as stop:
        if stop.code in (EXIT_TOKEN_CAP, EXIT_PROVIDER_LIMIT):
            return int(stop.code)
        raise
    except ProviderLimit:
        logger.warning("the ChatGPT lane answered with a usage or rate limit: stopping at once")
        return EXIT_PROVIDER_LIMIT
    except ScratchError as error:
        logger.error("replication %s refused: %s", args.task, error)
        return EXIT_REFUSED


__all__ = ["ArmRuns", "DryKit", "DryModel", "LiveKit", "ReplicationRunner", "gives_back", "kit_for", "main",
           "set_envelope"]


if __name__ == "__main__":
    sys.exit(main())
