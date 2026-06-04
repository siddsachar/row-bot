"""Deterministic offline scanner for imported public skill bundles."""

from __future__ import annotations

import pathlib
import re
from typing import Any

from .models import SkillBundle, SkillScanFinding, SkillScanResult
from .sources import parse_skill_markdown

MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 5_000_000
MAX_FILE_COUNT = 100
HIGH_TOKEN_WARNING = 4_000

ALLOWED_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".pdf", ".docx", ".xlsx", ".pptx",
}
EXECUTABLE_EXTENSIONS = {
    ".py", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".js", ".ts", ".mjs", ".cjs", ".exe", ".dll", ".dylib", ".so",
}
INSTALLER_METADATA_KEYS = {
    "install", "installer", "setup", "setup_commands", "commands",
    "postinstall", "preinstall", "scripts",
}

BLOCK_PATTERNS = {
    "secret_exfiltration": re.compile(
        r"\b(reveal|dump|exfiltrate|send|upload|steal)\b.{0,80}\b(secret|token|api key|password|credential|env)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "approval_bypass": re.compile(
        r"\b(bypass|ignore|disable|circumvent)\b.{0,80}\b(approval|sandbox|user consent|permission|higher-priority|system instruction)\b",
        re.IGNORECASE | re.DOTALL,
    ),
}
WARN_PATTERNS = {
    "shell_commands": re.compile(
        r"(```\s*(bash|sh|powershell|cmd)|\b(sudo|rm\s+-rf|curl\s+|wget\s+|pip\s+install|npm\s+install|powershell|cmd\.exe|bash\s+)\b)",
        re.IGNORECASE,
    ),
    "network_language": re.compile(r"\b(api|http|webhook|upload|download|exfiltrate|network|remote server)\b", re.IGNORECASE),
    "credentials_language": re.compile(r"\b(api key|token|credential|secret|environment variable|env var)\b", re.IGNORECASE),
    "destructive_language": re.compile(r"\b(delete|wipe|overwrite|destroy|remove files|format disk)\b", re.IGNORECASE),
    "regulated_workflow": re.compile(r"\b(crypto|trading|payment|finance|medical|legal)\b", re.IGNORECASE),
    "prompt_injection_language": re.compile(r"\b(ignore previous|ignore above|system prompt|developer message|hidden instructions)\b", re.IGNORECASE),
    "broad_automation": re.compile(r"\b(filesystem|browser|email|calendar|contacts|all files|entire drive)\b", re.IGNORECASE),
}


def scan_bundle(bundle: SkillBundle) -> SkillScanResult:
    findings: list[SkillScanFinding] = []
    total_bytes = 0
    skill_paths: list[str] = []

    if len(bundle.files) > MAX_FILE_COUNT:
        findings.append(_finding("block", "too_many_files", f"Bundle contains more than {MAX_FILE_COUNT} files."))

    for file in bundle.files:
        path = file.path.replace("\\", "/")
        suffix = pathlib.PurePosixPath(path).suffix.lower()
        total_bytes += file.size_bytes
        if _path_is_unsafe(path):
            findings.append(_finding("block", "path_traversal", "File path escapes the skill folder.", path))
        if file.kind == "symlink":
            findings.append(_finding("block", "symlink_escape", "Symlinks are not allowed in public skill imports.", path))
        if pathlib.PurePosixPath(path).name == "SKILL.md":
            skill_paths.append(path)
        if file.size_bytes > MAX_FILE_BYTES:
            findings.append(_finding("block", "file_too_large", f"File exceeds {MAX_FILE_BYTES} bytes.", path))
        if file.kind == "binary" and suffix not in ALLOWED_BINARY_EXTENSIONS:
            findings.append(_finding("block", "suspicious_binary", "Binary file type is not allowed for passive skills.", path))
        if path.startswith("scripts/") or "/scripts/" in path:
            findings.append(_finding("warn", "scripts_present", "scripts/ is copied passively and never executed.", path))
        if suffix in EXECUTABLE_EXTENSIONS:
            findings.append(_finding("warn", "executable_extension", "Executable-like file is copied passively.", path))

    if total_bytes > MAX_TOTAL_BYTES:
        findings.append(_finding("block", "bundle_too_large", f"Bundle exceeds {MAX_TOTAL_BYTES} bytes."))

    if len(skill_paths) != 1:
        code = "missing_skill_md" if not skill_paths else "ambiguous_skill_md"
        findings.append(_finding("block", code, "Imported skill folders must contain exactly one primary SKILL.md."))

    primary = bundle.primary_file()
    frontmatter: dict[str, Any] = {}
    instructions = ""
    if primary is None:
        findings.append(_finding("block", "missing_primary_skill", "Primary SKILL.md was not found."))
    else:
        try:
            frontmatter, instructions = parse_skill_markdown(primary.text)
        except Exception as exc:
            findings.append(_finding("block", "unparsable_skill_md", f"Could not parse SKILL.md: {exc}", primary.path))

    if primary is not None and not instructions.strip():
        findings.append(_finding("block", "empty_instructions", "SKILL.md has an empty instructions body.", primary.path))

    if "tools" in frontmatter:
        findings.append(_finding(
            "block",
            "tools_metadata",
            "Public skill imports cannot declare tools or become tool guides.",
            primary.path if primary else "",
        ))

    installer_keys = sorted(key for key in frontmatter if key in INSTALLER_METADATA_KEYS)
    if installer_keys:
        findings.append(_finding(
            "block",
            "installer_metadata",
            "Installer/setup metadata is not allowed because public skill imports are passive.",
            primary.path if primary else "",
            keys=installer_keys,
        ))

    body = instructions or bundle.instructions or ""
    for code, pattern in BLOCK_PATTERNS.items():
        if pattern.search(body):
            findings.append(_finding("block", code, "Instructions contain a blocked security pattern.", primary.path if primary else ""))
    for code, pattern in WARN_PATTERNS.items():
        if pattern.search(body):
            findings.append(_finding("warn", code, "Instructions contain language that deserves review.", primary.path if primary else ""))

    token_estimate = max(0, len(body) // 4)
    if token_estimate > HIGH_TOKEN_WARNING:
        findings.append(_finding("warn", "high_token_estimate", "Skill instructions are large.", details={"tokens": token_estimate}))

    findings.append(_finding("info", "token_estimate", f"Approximate instruction tokens: {token_estimate}.", details={"tokens": token_estimate}))
    findings.append(_finding("info", "file_count", f"Bundle contains {len(bundle.files)} file(s).", details={"files": len(bundle.files)}))
    findings.append(_finding("info", "source_trust", f"Source trust level: {bundle.metadata.get('trust_level', 'community')}."))
    if (
        frontmatter.get("dependencies")
        or frontmatter.get("python_dependencies")
        or frontmatter.get("platforms")
        or frontmatter.get("os")
    ):
        findings.append(_finding("info", "metadata_hints", "Skill metadata includes dependency or platform hints."))

    blocked = any(finding.severity == "block" for finding in findings)
    summary = {
        "blocks": sum(1 for finding in findings if finding.severity == "block"),
        "warnings": sum(1 for finding in findings if finding.severity == "warn"),
        "info": sum(1 for finding in findings if finding.severity == "info"),
        "files": len(bundle.files),
        "bytes": total_bytes,
    }
    return SkillScanResult(
        ok=not blocked,
        blocked=blocked,
        findings=findings,
        summary=summary,
        token_estimate=token_estimate,
    )


def _path_is_unsafe(path: str) -> bool:
    if not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return True
    parts = pathlib.PurePosixPath(path).parts
    return any(part == ".." for part in parts)


def _finding(
    severity: str,
    code: str,
    message: str,
    path: str = "",
    **details: Any,
) -> SkillScanFinding:
    return SkillScanFinding(
        severity=severity,  # type: ignore[arg-type]
        code=code,
        message=message,
        path=path,
        details=dict(details.get("details") or {k: v for k, v in details.items() if k != "details"}),
    )
