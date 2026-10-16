"""The ChatGPT lane's eval profile (D31, PLAN.md O16): its ChatGPT sign-in, read and refreshed, nothing else.

The ChatGPT subscription (Row-Bot's ``codex`` provider) signs in with OAuth, so its
credentials can't come from the environment like the API-key providers'. They come
from a dedicated **eval profile**: a Row-Bot data folder outside the repo, signed in
once with the eval-profile sign-in script, whose tokens sit in the OS credential store
under that folder's own service name (``secret_store.service_name_for(profile)``),
apart from the everyday profile.

- **Read**: a short bridge process bound to the eval profile (``python -m
  tests.e2e.live_eval.eval_profile read``) reads the codex bundle with Row-Bot's own
  reader and hands it back through a pipe. It never refreshes anything.
- **Run**: the worker still runs in a fresh per-run data folder and test mode; the
  bundle arrives on its stdin and lives in an in-memory keyring there.
- **Write back**: only refreshed tokens, through the same bridge with Row-Bot's own
  writer (new token generations; refused when the profile changed meanwhile), so a
  token rotation during a run never breaks the sign-in.
- Nothing else in the profile is read or written, and tokens never reach the run
  folder, the logs or a report.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .ledger import everyday_data_folder

PROFILE_ENV = "ROW_BOT_LIVE_EVAL_CODEX_PROFILE"
SIGN_IN_ENV = "ROW_BOT_LIVE_EVAL_CODEX_SIGN_IN"
DEFAULT_SIGN_IN = "the eval-profile sign-in script (.local/plans/self-improvement/sign-in-eval-profile.ps1)"
MAX_TOKENS_ENV = "ROW_BOT_LIVE_EVAL_CODEX_MAX_TOKENS"
DEFAULT_MAX_TOKENS = 1_500_000
# GPT-6.1 Sol when the live catalog lists it, otherwise GPT-5.6 Sol (D31).
PREFERRED_MODELS = ("gpt-6.1-sol", "gpt-5.6-sol")
FRAME = "::row-bot-eval-credentials:: "
_BRIDGE_TIMEOUT = 120
_REPO_ROOT = Path(__file__).resolve().parents[3]


class EvalProfileError(RuntimeError):
    """The eval profile can't be used (missing, the everyday folder, no sign-in)."""


@dataclass(frozen=True)
class CodexBundle:
    """A codex sign-in as the profile holds it, plus the profile's revision when it was read."""

    access_token: str
    refresh_token: str = ""
    id_token: str = ""
    account: str = ""
    expires_at: str = ""
    plan_type: str = ""
    revision: str = ""

    def secrets(self) -> tuple[str, ...]:
        return tuple(value for value in (self.access_token, self.refresh_token, self.id_token) if value)

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> CodexBundle:
        return cls(**{key: str(data.get(key) or "") for key in cls.__dataclass_fields__})

    def same_tokens(self, other: CodexBundle) -> bool:
        return (self.access_token, self.refresh_token, self.id_token) == (
            other.access_token, other.refresh_token, other.id_token)


def profile_folder(env: Mapping[str, str] | None = None) -> Path | None:
    """The eval profile named in the environment, or None when the lane isn't set up."""
    source = os.environ if env is None else env
    text = str(source.get(PROFILE_ENV, "") or "").strip()
    return Path(text).expanduser() if text else None


def sign_in_hint(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    script = str(source.get(SIGN_IN_ENV, "") or "").strip()
    return f"run {script}" if script else f"run {DEFAULT_SIGN_IN}"


def max_tokens(env: Mapping[str, str] | None = None) -> int:
    """The ChatGPT lane's token cap per run (default 1.5M)."""
    source = os.environ if env is None else env
    text = str(source.get(MAX_TOKENS_ENV, "") or "").strip()
    if not text:
        return DEFAULT_MAX_TOKENS
    value = int(float(text))
    if value <= 0:
        raise ValueError(f"{MAX_TOKENS_ENV} must be a positive number of tokens")
    return value


def check_profile(profile: Path) -> None:
    """Refuse a folder that is missing or is (inside) the everyday Row-Bot data folder."""
    if everyday_data_folder(profile):
        raise EvalProfileError("refusing the everyday Row-Bot data folder as the eval profile")
    if not profile.is_dir():
        raise EvalProfileError(f"the eval profile folder {profile} does not exist")


def signed_in(profile: Path) -> bool:
    """Whether the profile records a Row-Bot-owned ChatGPT sign-in (a local file check: no
    credential store, no network)."""
    try:
        config = json.loads((profile / "providers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    entry = ((config.get("providers") or {}).get("codex") or {}) if isinstance(config, dict) else {}
    reference = entry.get("oauth_bundle_ref") if isinstance(entry, dict) else None
    return (isinstance(reference, dict) and bool(reference.get("values"))
            and entry.get("auth_method") == "oauth_device" and entry.get("source") == "oauth_device")


def availability(env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """(available, reason) for the plan: decided from local files only."""
    profile = profile_folder(env)
    if profile is None:
        return False, f"set {PROFILE_ENV} to the eval profile folder (the ChatGPT lane, D31)"
    if everyday_data_folder(profile):
        return False, "refusing the everyday Row-Bot data folder as the eval profile"
    if not profile.is_dir():
        # The sign-in script makes the folder: no folder means no sign-in yet.
        return False, f"the eval profile has no ChatGPT sign-in ({profile} does not exist yet): {sign_in_hint(env)}"
    if not signed_in(profile):
        return False, f"the eval profile has no ChatGPT sign-in: {sign_in_hint(env)}"
    return True, "ChatGPT sign-in from the eval profile; model chosen from the live catalog at run start"


def choose_model(listed: list[str] | tuple[str, ...]) -> str:
    """GPT-6.1 Sol if the live catalog lists it, otherwise GPT-5.6 Sol."""
    return next((model for model in PREFERRED_MODELS if model in set(listed)), PREFERRED_MODELS[-1])


# ── The bridge (parent side) ───────────────────────────────────────────────

Runner = Callable[[Path, str, "dict[str, Any] | None"], "dict[str, Any]"]


def bridge_env(profile: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The bridge's environment: bound to the eval profile, out of test mode (it needs the real
    credential store), and nothing that would start services."""
    env = {key: value for key, value in (os.environ if base is None else base).items()
           if key not in {"ROW_BOT_TEST_MODE", "PYTEST_CURRENT_TEST", "PYTEST_XDIST_WORKER"}}
    env.update({"ROW_BOT_DATA_DIR": str(profile), "ROW_BOT_AUTO_START_OLLAMA": "0", "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1"})
    return env


def run_bridge(profile: Path, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the bridge in its own process; what it answers stays in this process's memory."""
    completed = subprocess.run(  # noqa: S603 - our own module with our own interpreter
        [sys.executable, "-m", "tests.e2e.live_eval.eval_profile", command, "--profile", str(profile)],
        cwd=_REPO_ROOT, env=bridge_env(profile), input=json.dumps(payload or {}), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=_BRIDGE_TIMEOUT, check=False,
    )
    lines = [line.rstrip("\r") for line in completed.stdout.split("\n") if line.startswith(FRAME)]
    if completed.returncode != 0 or not lines:
        # The bridge's stderr may name a path, never a token; keep only its last line.
        tail = (completed.stderr or "").strip().splitlines()[-1:] or ["no answer"]
        raise EvalProfileError(f"the eval-profile bridge failed: {tail[0][:200]}")
    return json.loads(lines[-1][len(FRAME):])


def read_bundle(profile: Path, *, runner: Runner = run_bridge) -> CodexBundle | None:
    """The profile's codex sign-in, or None when it has none."""
    check_profile(profile)
    if not signed_in(profile):
        return None
    answer = runner(profile, "read", None)
    if not answer.get("signed_in"):
        return None
    bundle = CodexBundle.from_payload(answer)
    _redact_later(bundle)
    return bundle


def write_back(profile: Path, before: CodexBundle, after: CodexBundle | None, *, runner: Runner = run_bridge) -> str:
    """Save tokens the run refreshed into the profile; nothing when they didn't change."""
    if after is None or after.same_tokens(before) or not after.access_token:
        return "unchanged"
    check_profile(profile)
    _redact_later(after)
    answer = runner(profile, "write", {**after.payload(), "revision": before.revision})
    if answer.get("written"):
        return "written"
    return f"not written: {answer.get('reason') or 'unknown'}"


def _redact_later(bundle: CodexBundle) -> None:
    from .report import register_secret

    for value in bundle.secrets():
        register_secret(value)


# ── The bridge (its own process, bound to the eval profile) ────────────────


def _answer(value: dict[str, Any]) -> None:
    sys.stdout.write(FRAME + json.dumps(value) + "\n")
    sys.stdout.flush()


def bridge_read() -> dict[str, Any]:
    """Read the codex sign-in with Row-Bot's own reader (never refreshes)."""
    from row_bot import secret_store
    from row_bot.providers.auth_store import read_provider_oauth_bundle_snapshot

    profile = Path(os.environ["ROW_BOT_DATA_DIR"])
    if secret_store.SERVICE_NAME != secret_store.service_name_for(profile):
        raise EvalProfileError("the bridge is not bound to the eval profile")
    values, entry, revision = read_provider_oauth_bundle_snapshot("codex")
    if entry.get("auth_method") != "oauth_device" or not (values.get("access_token") or values.get("refresh_token")):
        return {"signed_in": False}
    return {"signed_in": True, "access_token": values.get("access_token") or "",
            "refresh_token": values.get("refresh_token") or "", "id_token": values.get("id_token") or "",
            "account": values.get("account") or "", "expires_at": str(entry.get("expires_at") or ""),
            "plan_type": str(entry.get("plan_type") or ""), "revision": revision}


def bridge_write(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Save refreshed tokens with Row-Bot's own writer, only if the profile is as it was read."""
    from row_bot import secret_store
    from row_bot.providers import codex
    from row_bot.providers.config import ProviderConfigError

    profile = Path(os.environ["ROW_BOT_DATA_DIR"])
    if secret_store.SERVICE_NAME != secret_store.service_name_for(profile):
        raise EvalProfileError("the bridge is not bound to the eval profile")
    bundle = CodexBundle.from_payload(payload)
    try:
        codex.save_codex_oauth_tokens(codex.CodexTokenSet(
            access_token=bundle.access_token, refresh_token=bundle.refresh_token, id_token=bundle.id_token,
            expires_at=bundle.expires_at, account_id=bundle.account, plan_type=bundle.plan_type,
        ), expected_revision=bundle.revision or None)
    except ProviderConfigError as exc:
        return {"written": False, "reason": "the eval profile changed during the run" if "revision" in str(exc)
                else type(exc).__name__}
    return {"written": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The ChatGPT lane's eval-profile bridge (read / write back).")
    parser.add_argument("command", choices=("read", "write"))
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    profile = Path(args.profile)
    check_profile(profile)
    if Path(os.environ.get("ROW_BOT_DATA_DIR", "")).resolve() != profile.resolve():
        raise EvalProfileError("ROW_BOT_DATA_DIR must name the eval profile")
    if args.command == "read":
        _answer(bridge_read())
    else:
        _answer(bridge_write(json.loads(sys.stdin.read() or "{}")))
    return 0


# ── The worker's side ──────────────────────────────────────────────────────


class MemoryKeyring:
    """An in-memory credential store for the worker (test mode keeps the real one closed)."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if (service, account) not in self.values:
            raise KeyError("not found")
        del self.values[(service, account)]


def seed_worker(payload: Mapping[str, Any], *, emit: Callable[[CodexBundle], None] | None = None) -> CodexBundle:
    """In the worker: put the sign-in in an in-memory keyring and the worker's own data folder,
    and report every refresh (the parent writes the last one back to the profile)."""
    from row_bot import secret_store
    from row_bot.providers import codex

    from .report import register_secret

    if not secret_store._test_mode_active():
        raise EvalProfileError("the worker must run in test mode")
    bundle = CodexBundle.from_payload(payload)
    for value in bundle.secrets():
        register_secret(value)
    secret_store._set_backend_for_tests(MemoryKeyring())
    codex.save_codex_oauth_tokens(codex.CodexTokenSet(
        access_token=bundle.access_token, refresh_token=bundle.refresh_token, id_token=bundle.id_token,
        expires_at=bundle.expires_at, account_id=bundle.account, plan_type=bundle.plan_type,
    ))
    original = codex.save_codex_oauth_tokens
    report = emit or emit_refreshed
    current = [bundle]

    def save_and_report(token_set: Any, *args: Any, **kwargs: Any) -> Any:
        saved = original(token_set, *args, **kwargs)
        refreshed = _merged(current[0], CodexBundle(token_set.access_token, token_set.refresh_token,
                                                    token_set.id_token, token_set.account_id, token_set.expires_at,
                                                    token_set.plan_type))
        current[0] = refreshed
        for value in refreshed.secrets():
            register_secret(value)
        report(refreshed)
        return saved

    codex.save_codex_oauth_tokens = save_and_report
    return bundle


def _merged(before: CodexBundle, after: CodexBundle) -> CodexBundle:
    """A refresh may leave out what didn't change (the refresh token, the id token, the account)."""
    return CodexBundle(after.access_token, after.refresh_token or before.refresh_token,
                       after.id_token or before.id_token, after.account or before.account,
                       after.expires_at or before.expires_at, after.plan_type or before.plan_type)


def emit_refreshed(bundle: CodexBundle) -> None:
    """Hand refreshed tokens to the parent on stdout (a pipe it keeps in memory)."""
    _answer(bundle.payload())


def refreshed_from(stdout: str) -> CodexBundle | None:
    """The last refreshed bundle the worker reported, if any."""
    frames = [line.rstrip("\r") for line in str(stdout or "").split("\n") if line.startswith(FRAME)]
    return CodexBundle.from_payload(json.loads(frames[-1][len(FRAME):])) if frames else None


def scrub_worker_folder(data_dir: Path) -> None:
    """Drop the codex entry (fingerprints, generation names) from the worker's own provider settings."""
    path = data_dir / "providers.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(config, dict) and isinstance(config.get("providers"), dict) and "codex" in config["providers"]:
        config["providers"].pop("codex")
        path.write_bytes((json.dumps(config, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
