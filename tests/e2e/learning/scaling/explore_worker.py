"""The exploratory stage's child process (``explore.py``): the study's arms served at 65,536 tokens and run on its
held-out requests, in one scratch profile.

Row-Bot binds its data folder when it is imported, so ``explore.py`` starts this module with ``ROW_BOT_DATA_DIR`` set
to ``<scratch>/data``. It:

1. reads each arm's model from the finished study (its model ref in ``train/state.json`` and its GGUF in the study's
   server list), never writing there;
2. links Row-Bot's llama.cpp server files from the study's profile (hard links), never its ``run`` folder (the server
   rewrites its list, presets and log there in place, so a hard link would write into the study's);
3. keeps the study's small-context prompt profile up to the serving context (``--envelope study``, the default);
4. registers each GGUF on this profile's own server at the serving context under the study's own name (so the model
   ref is the study's), loads it, runs Row-Bot's probe, reads the card's memory and unloads it, under a booking;
5. runs every held-out request ``runs`` times for each arm (``runner.StudyRunner``, the study's own run), a model-call
   error once more, each run recorded under SA2 with its unrecovered calls, saved after every run.

It continues from its ``runs.jsonl`` when started again. Its kit is :class:`LiveKit`, or :class:`DryKit` for the dry
run (the scripted model on loopback, every arm's endpoint declared at the serving context).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.e2e.learning.scaling import dry_run, worker
from tests.e2e.learning.scaling.design import Design
from tests.e2e.learning.scaling.explore import LANE, ExploreDesign, is_overflow, read_lines, recode
from tests.e2e.learning.scaling.runner import Run, Spec, StudyRunner
from tests.e2e.learning.seeded.scratch import (
    GpuGate,
    ScratchError,
    after_quiet_hours,
    card_memory_mib,
    check_scratch,
    in_quiet_hours,
)

logger = logging.getLogger("row_bot.scaling")

LABEL = "scaling explore (64K, exploratory)"
#: The server's own folder for its list, presets, log and home (``llama_server.RUN_FOLDER``): never linked.
SERVER_RUN_FOLDER = "run"
SERVER_MARKER = "installed.json"
#: llama-server (one model at a time) refuses to load a model while the one it unloads is still stopping ("model
#: limit reached"), and the product's load then waits out its 10 minutes. The card's memory comes back before the
#: instance has exited, so the next model loads only once the unloaded one's instance process is gone (at most this
#: long).
STOP_SECONDS = 60.0


def _server_processes() -> list[tuple[str, list[str]]]:
    import psutil

    found = []
    for process in psutil.process_iter(["exe", "cmdline"]):
        try:
            found.append((str(process.info.get("exe") or ""), [str(item) for item in process.info.get("cmdline") or ()]))
        except Exception:  # noqa: BLE001 - a process that ended or that we may not read
            continue
    return found


def instance_running(name: str, folder: Path,
                     processes: Callable[[], Iterable[tuple[str, Sequence[str]]]] = _server_processes) -> bool:
    """Whether a llama-server instance started from ``folder`` (this profile's server files) still serves ``name``
    (its ``--alias``). Another Row-Bot's server, the person's own included, never counts."""

    root = str(folder.resolve()).lower()
    for exe, cmdline in processes():
        if exe.lower().startswith(root) and any(flag == "--alias" and value == name
                                                for flag, value in zip(cmdline, cmdline[1:])):
            return True
    return False


def new_work_quiet(now: datetime | None = None) -> str | None:
    """Why no new GPU work starts now: from 00:25 (work in hand ends before the person's quiet hours at 00:40) to
    05:00, and 05:00 to 08:00, left to the person's own Row-Bot (it may compare what it trained overnight)."""

    if in_quiet_hours(now, start=worker.NEW_WORK_STOPS):
        return "the person's quiet hours are near or here (no new GPU work from 00:25 to 05:00)"
    if after_quiet_hours(now):
        return "05:00-08:00 is left to the person's own Row-Bot"
    return None


def study_arms(study_out: Path, study_data: Path, arms: Sequence[str]) -> dict[str, tuple[str, str, Path]]:
    """Each arm as the study served it: its model ref, its name on Row-Bot's server and its GGUF (in the study's own
    profile; read only)."""

    trained = worker.JsonState(study_out / "train" / "state.json").data
    refs = {"start": (trained.get("start") or {}).get("ref")}
    refs.update({arm: entry.get("model_ref") for arm, entry in (trained.get("arms") or {}).items()
                 if entry.get("state") == "ready"})
    listed = worker.JsonState(study_data / "runtimes" / "learning" / "server" / SERVER_RUN_FOLDER / "models.json").data
    ggufs = {str(item.get("name")): str(item.get("gguf") or "") for item in listed.get("models") or ()}
    found: dict[str, tuple[str, str, Path]] = {}
    for arm in arms:
        ref = str(refs.get(arm) or "")
        if not ref:
            raise ScratchError(f"the study has no ready model for {arm}")
        name = ref.rsplit(":", 1)[-1]
        gguf = Path(ggufs.get(name) or "")
        if not ggufs.get(name) or not gguf.is_file():
            raise ScratchError(f"the study's server lists no GGUF for {arm} ({name})")
        try:
            gguf.resolve().relative_to(study_data.resolve())
        except ValueError:
            raise ScratchError(f"{arm}'s GGUF ({gguf}) isn't in the study's own profile") from None
        found[arm] = (ref, name, gguf)
    return found


def link_server(source_data: Path, data: Path) -> None:
    """Row-Bot's llama.cpp server files from ``source_data`` into ``data``: hard links (nothing in the source is
    written), small records copied, never the server's ``run`` folder; the marker last, so a stopped link starts over."""

    source = source_data / "runtimes" / "learning" / "server"
    target = data / "runtimes" / "learning" / "server"
    if (target / SERVER_MARKER).is_file():
        return
    if not (source / SERVER_MARKER).is_file():
        raise ScratchError(f"{source} holds no installed llama.cpp server")
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if relative.parts[0] == SERVER_RUN_FOLDER or relative == Path(SERVER_MARKER):
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix == ".json":
            shutil.copy2(item, destination)
            continue
        try:
            os.link(item, destination)
        except FileExistsError:
            pass
        except OSError:
            shutil.copy2(item, destination)
    shutil.copy2(source / SERVER_MARKER, target / SERVER_MARKER)


def keep_small_profile(context: int) -> None:
    """The study's prompt envelope up to ``context``: Row-Bot gives a window of this size the small-context profile
    it gave the study's 32K (this process only; see ``explore.py``)."""

    from row_bot import agent

    agent.SMALL_PROFILE_TOKENS = int(context)


# ── Kits ─────────────────────────────────────────────────────────────────────


class _MeasuringVerifier:
    """Row-Bot's own probe, reading the card's memory while the model it probed is loaded."""

    def __init__(self) -> None:
        from row_bot.learning.training import serving

        self.inner = serving.RowBotVerifier()
        self.memory: int | None = None

    def probe(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        found = self.inner.probe(*args, **kwargs)
        self.memory = card_memory_mib()
        return found

    def tool_turn(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        return self.inner.tool_turn(*args, **kwargs)


class LiveKit:
    """The real one: Row-Bot's own llama.cpp server in this profile and the shared card."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.data = Path(args.data)

    def prepare(self, study_data: Path) -> None:
        link_server(study_data, self.data)

    def boot(self, data: Path) -> None:
        return None

    def serve(self, arms: Mapping[str, tuple[str, str, Path]], context: int, gpu: GpuGate) -> dict[str, dict[str, Any]]:
        """Each GGUF on this profile's server at ``context`` under the study's name, probed and unloaded."""

        from row_bot.learning.training import serving

        found: dict[str, dict[str, Any]] = {}
        gpu.acquire(f"serving {', '.join(arms)} at {context:,} tokens (each loaded, probed and unloaded)",
                    expect="5 min, 14 GB")
        try:
            baseline = card_memory_mib()
            for arm, (ref, name, gguf) in arms.items():
                verifier = _MeasuringVerifier()
                outcome = serving.serve(serving.ROW_BOT, version=0, gguf=gguf, serve_context=context, folder=self.data,
                                        root=self.data, tag=name, tool_turn=False, verifier=verifier)
                if outcome.state != "ready" or outcome.model_ref != ref:
                    raise ScratchError(f"{arm} couldn't be served at {context:,} tokens ({outcome.reason or 'as '}"
                                       f"{outcome.model_ref if outcome.state == 'ready' else ''}, not {ref})")
                found[arm] = {"model_ref": outcome.model_ref, "name": name, "gguf": str(gguf), "context": int(context),
                              "vram_mib": verifier.memory, "baseline_mib": baseline, "probe": dict(outcome.probe),
                              "seconds": round(float(outcome.seconds), 1)}
                self._wait_stopped(name)
                found[arm]["after_unload_mib"] = card_memory_mib()
        finally:
            gpu.release()
        return found

    def _wait_stopped(self, name: str) -> None:
        deadline = time.monotonic() + STOP_SECONDS
        while instance_running(name, self.data / "runtimes" / "learning" / "server"):
            if time.monotonic() > deadline:
                raise ScratchError(f"{name}'s llama-server instance didn't stop within {STOP_SECONDS:.0f} s")
            time.sleep(0.5)

    def unload(self, name: str) -> None:
        """Between arms: the last arm's model unloaded, and its instance gone before the next one loads."""

        from row_bot.learning.training import llama_server

        server = llama_server.get(self.data)
        if server.model_status(name) == "loaded":
            server.unload(name)
        self._wait_stopped(name)

    def finish(self) -> None:
        from row_bot.learning.training import llama_server

        llama_server.shutdown()

    def gpu(self) -> GpuGate:
        return GpuGate(Path(self.args.gpu_booking) if self.args.gpu_booking else None, self.args.lane, label=LABEL,
                       quiet=new_work_quiet)


class DryKit(dry_run.DryKit):
    """The dry run's: the scripted model on loopback as every arm, each endpoint declared at the serving context."""

    def __init__(self, args: argparse.Namespace, context: int) -> None:
        super().__init__(args)
        self.context = int(context)

    def _endpoint(self, name: str) -> str:
        from row_bot.providers import custom
        from row_bot.providers.selection import model_ref

        endpoint = f"{dry_run.PREFIX}{name}"
        custom.save_custom_endpoint({"id": endpoint, "name": f"Dry run {name}", "base_url": self.model.base_url,
                                     "profile": "llama_cpp", "execution_location": "local", "auth_required": False,
                                     "manual_capabilities": {"context_window": self.context}})
        custom.refresh_custom_endpoint_models(endpoint)
        custom.probe_custom_endpoint(endpoint, dry_run.ALIAS)
        return model_ref(custom.custom_provider_id(endpoint), dry_run.ALIAS)

    def prepare(self, study_data: Path) -> None:
        return None

    def serve(self, arms: Mapping[str, tuple[str, str, Path]], context: int, gpu: GpuGate) -> dict[str, dict[str, Any]]:
        self.context = int(context)
        return {arm: {"model_ref": self._endpoint(arm), "name": f"{dry_run.PREFIX}{arm}", "gguf": str(gguf),
                      "context": int(context), "vram_mib": None, "baseline_mib": None, "probe": {"agent_ready": True},
                      "seconds": 0.0} for arm, (_ref, _name, gguf) in arms.items()}

    def unload(self, name: str) -> None:
        return None

    def finish(self) -> None:
        return None

    def gpu(self) -> GpuGate:  # type: ignore[override]
        return super().gpu(LABEL)


# ── The evaluation ───────────────────────────────────────────────────────────


def run_record(arm: str, request: Any, repeat: int, run: Run, *, reran: bool, overflowed: int) -> dict[str, Any]:
    """One run as recorded: the study's own fields under SA2 (an overflow out of budget, marked), how many attempts
    overflowed, and the unrecovered calls as (tool name, problem class) pairs only."""

    fields = worker._case_fields(run.case)  # noqa: SLF001 - the study's own record of a run
    return {"arm": arm, "task": request.id, "repeat": repeat, "cluster": worker.cluster(request), **recode(fields),
            "overflowed_attempts": int(overflowed), "reran_after_outage": bool(reran), "approvals": run.approvals,
            "unrecovered": [[tool, problem] for tool, problem in run.unrecovered]}


class ExploreEvaluation:
    """The arms served at the explored context, then every held-out request for each (see the module's docstring)."""

    def __init__(self, args: argparse.Namespace, kit: Any) -> None:
        self.args = args
        self.kit = kit
        self.data, self.out = Path(args.data), Path(args.out)
        saved = json.loads((self.out / "design.json").read_text(encoding="utf-8"))
        self.design = ExploreDesign.from_dict(saved["explore"])
        self.study = Design.from_dict(saved["study_design"])
        self.study_out, self.study_data = Path(saved["study_out"]), Path(saved["study_data"])
        self.folder = self.out / "evaluate"
        self.state = worker.JsonState(self.folder / "state.json")

    def run(self) -> int:
        arms = study_arms(self.study_out, self.study_data, self.design.arms)
        if self.design.envelope == "study":
            keep_small_profile(self.design.serve_context)
        worker.prepare_profile(self.data, SimpleNamespace(runtimes_from=""))
        self.kit.prepare(self.study_data)
        self.kit.boot(self.data)
        gpu = self.kit.gpu()
        served = self.serve(arms, gpu)
        specs = {arm: Spec(arm, str(served[arm]["model_ref"]), local=True) for arm in self.design.arms}
        self.check_served(specs)
        requests, owned, people = worker.owners(self.study, "held_out")
        ids = list(requests)[: self.design.tasks or None]
        self.state.data.update({"arms": {arm: spec.model_ref for arm, spec in specs.items()}, "tasks": len(ids),
                                "runs_per_task": self.design.runs or self.study.runs})
        self.state.save()
        self.runner = StudyRunner(data=self.data, arms=specs, owners=owned, gpu=gpu, task_tokens=self.study.task_tokens,
                                  seconds=self.study.seconds, unload=lambda arm: self.kit.unload(served[arm]["name"]))
        self.runner.boot(people)
        self.runner.on_run = self._spend
        try:
            for arm in self.design.arms:
                self.held_out(arm, requests, ids)
        finally:
            self.runner.finish()
            self._gpu()
            self.kit.finish()
        self.state.data["done"] = True
        self.state.save()
        return 0

    def serve(self, arms: Mapping[str, tuple[str, str, Path]], gpu: GpuGate) -> dict[str, dict[str, Any]]:
        """The arms on the server at the explored context (once: a resumed run keeps what it served)."""

        context = int(self.design.serve_context)
        saved = self.state.data.setdefault("serving", {})
        wanted = {arm: item for arm, item in arms.items()
                  if (saved.get(arm) or {}).get("gguf") != str(item[2]) or (saved.get(arm) or {}).get("context") != context}
        if wanted:
            started = time.monotonic()
            saved.update(self.kit.serve(wanted, context, gpu))
            seconds = self.state.data.setdefault("gpu_seconds", {})
            seconds["serving"] = round(float(seconds.get("serving", 0.0)) + time.monotonic() - started, 1)
            self.state.save()
        return saved

    def check_served(self, specs: Mapping[str, Spec]) -> None:
        """Row-Bot sees each arm's window as the explored context and gives it the envelope the design asks for."""

        from row_bot import agent, models

        for arm, spec in specs.items():
            size = int(models.get_context_size(spec.model_ref))
            small = bool(agent._small_context_profile(size))  # noqa: SLF001 - the runtime's own profile rule
            if size != self.design.serve_context or small != (self.design.envelope == "study"):
                raise ScratchError(f"{arm}: Row-Bot sees a {size:,}-token window (small-context profile {small}), not "
                                   f"{self.design.serve_context:,} with the {self.design.envelope} envelope")
            self.state.data["serving"][arm].update({"effective_context": size, "small_profile": small})
        self.state.save()

    def held_out(self, arm: str, requests: Mapping[str, Any], ids: Sequence[str]) -> None:
        """Every held-out request ``runs`` times (a model-call error once more, as the study did), saved after each."""

        path = self.folder / "runs.jsonl"
        done = {(item["arm"], item["task"], item["repeat"]) for item in read_lines(path)}
        for request_id in ids:
            request = requests[request_id]
            for repeat in range(self.design.runs or self.study.runs):
                if (arm, request_id, repeat) in done:
                    continue
                overflowed = 0
                for attempt in range(2):
                    run = self.runner.run_task(request.task, arm)
                    overflowed += int(is_overflow({"status": run.case.status, "detail": run.case.detail}))
                    if run.case.status != "error" or attempt:
                        break
                worker.append_lines(path, [run_record(arm, request, repeat, run, reran=attempt == 1,
                                                      overflowed=overflowed)])
                self._gpu()

    def _gpu(self) -> None:
        seconds = self.state.data.setdefault("gpu_seconds", {})
        for arm, value in self.runner.gpu_seconds.items():
            seconds[arm] = round(float(seconds.get(arm, 0.0)) + value, 1)
        self.runner.gpu_seconds.clear()
        self.state.save()

    def _spend(self, arm: str, case: Any) -> None:
        tokens = self.state.data.setdefault("tokens", {})
        tokens[arm] = int(tokens.get(arm, 0)) + int(case.tokens)


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The exploratory stage's evaluation (started by explore.py)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--lane", default=LANE)
    parser.add_argument("--gpu-booking", default="")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    check_scratch(Path(args.data))
    check_scratch(Path(args.out))
    design = ExploreDesign.from_dict(json.loads((Path(args.out) / "design.json").read_text(encoding="utf-8"))["explore"])
    kit = DryKit(args, design.serve_context) if design.dry_run else LiveKit(args)
    return ExploreEvaluation(args, kit).run()


__all__ = ["DryKit", "ExploreEvaluation", "LiveKit", "instance_running", "keep_small_profile", "link_server", "main",
           "new_work_quiet", "run_record", "study_arms"]


if __name__ == "__main__":
    sys.exit(main())
