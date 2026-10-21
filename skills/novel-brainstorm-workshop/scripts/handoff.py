#!/usr/bin/env python3
"""Read-only prompt intake checks and shell command rendering. Never starts a run."""

from __future__ import annotations

import argparse
import ast
import importlib
import json
from pathlib import Path
import re
import shlex
import sys


FIELDS = ("Title", "Author", "Genre", "Audience", "Language", "Tone", "POV", "Chapters", "Words")
FORMATS = ("markdown", "html", "docx", "epub", "pdf")
LAUNCH_ENV = (
    "NONINTERACTIVE", "PROJECT_NAME", "TITLE", "GENRE", "CHAPTERS", "WORDS",
    "APPROVAL", "EDIT_MODE", "QUALITY_POLICY", "MAX_RETRIES", "MAX_QUALITY_REPAIRS",
    "OUTPUT", "DRY_RUN",
)


def _repo_contract(repo: Path):
    """Inspect real launcher arguments without importing the orchestrator."""
    launcher = repo / "deploy.sh"
    source = repo / "core" / "orchestrator.py"
    if not launcher.is_file() or not source.is_file():
        raise ValueError("--repo must contain deploy.sh and core/orchestrator.py; launcher support is unverified")
    shell = launcher.read_text(encoding="utf-8")
    for suffix in LAUNCH_ENV:
        if "${NOVEL_OS_" + suffix not in shell:
            raise ValueError(f"Launcher does not expose NOVEL_OS_{suffix}; cannot generate a verified command")
    if "run_novel()" not in shell:
        raise ValueError("Launcher novel entry point is unrecognized")
    choices = {}
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "run_parser"
                and node.func.attr == "add_argument" and node.args
                and isinstance(node.args[0], ast.Constant)):
            continue
        choices[node.args[0].value] = next(
            (ast.literal_eval(item.value) for item in node.keywords if item.arg == "choices"), None
        )
    required = ("--project", "--prompt", "--title", "--genre", "--chapters", "--words",
                "--edit-mode", "--approval", "--quality-policy", "--max-retries",
                "--max-quality-repairs", "--output", "--dry-run")
    if any(flag not in choices for flag in required):
        raise ValueError("Repository run arguments are incompatible; command support is unverified")
    return launcher, choices


def _intake(repo: Path):
    core = repo / "core"
    if not (core / "prompt_intake.py").is_file():
        raise ValueError("Selected repository has no core/prompt_intake.py")
    # Legacy core modules use sibling imports. Refuse a cached module from a
    # different checkout instead of accidentally validating with the wrong code.
    for name in ("prompt_intake", "novel_classification", "narrative_format"):
        loaded = sys.modules.get(name)
        if loaded and Path(loaded.__file__).resolve().parent != core:
            raise ValueError("Core modules from another checkout are loaded; run this helper in a fresh process")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(core))
    return importlib.import_module("prompt_intake")


def _fields(text: str, intake) -> dict[str, str]:
    # Deliberately require a single obvious metadata block. The real intake
    # scans the whole prompt and later duplicate fields otherwise win silently.
    header = re.split(r"^##\s", text, maxsplit=1, flags=re.MULTILINE)[0]
    result = {}
    for name in FIELDS:
        pattern = rf"^\s*(?:[-*]\s*)?(?:\*\*)?{name}(?:\*\*)?\s*:[ \t]*(.*?)[ \t]*$"
        matches = re.findall(pattern, text, flags=re.MULTILINE | re.IGNORECASE)
        if len(matches) != 1 or not re.search(pattern, header, flags=re.MULTILINE | re.IGNORECASE):
            raise ValueError(f"Top metadata must contain exactly one {name}: field (no duplicates later)")
        value = matches[0].strip()
        if not value and name != "Author":
            raise ValueError(f"{name} must not be blank")
        result[name.lower()] = value
    for key in ("chapters", "words"):
        if not re.fullmatch(r"[1-9][0-9]*", result[key]):
            raise ValueError(f"{key} must be a positive integer without separators")
    # Ask the real intake which aliases it recognizes. A later "Primary
    # audience" or "Point of view" must not override the approved header.
    occurrences = {}
    for line in text.splitlines():
        match = intake._FIELD_RE.match(line)
        if match:
            key = match.group(1).lower().replace(" ", "_")
            key = intake._FIELD_ALIASES.get(key, key)
            occurrences[key] = occurrences.get(key, 0) + 1
    for key in result:
        if occurrences.get(key, 0) > 1:
            raise ValueError(f"Top metadata contains duplicate {key} fields or intake aliases")
    return result


def validate(prompt_path: Path, repo: Path) -> dict:
    repo = repo.resolve()
    _repo_contract(repo)
    text = prompt_path.resolve().read_text(encoding="utf-8")
    intake = _intake(repo)
    fields = _fields(text, intake)
    for name in ("NOVEL_CLASSIFICATION_JSON", "NARRATIVE_FORMAT_JSON"):
        if (len(re.findall(rf"\[{name}\]", text, re.IGNORECASE)) != 1
                or len(re.findall(rf"\[/{name}\]", text, re.IGNORECASE)) != 1):
            raise ValueError(f"Exactly one complete {name} block is required")
    # Counting markers is insufficient: reversed pairs otherwise make intake
    # infer a replacement contract, silently losing an approved series mode.
    if intake.parse_classification_block(text) is None:
        raise ValueError("NOVEL_CLASSIFICATION_JSON markers do not form a valid ordered block")
    if intake.parse_narrative_format_block(text) is None:
        raise ValueError("NARRATIVE_FORMAT_JSON markers do not form a valid ordered block")
    # Check both unmodified intake and the exact CLI overrides to expose the
    # intake's Words-vs-JSON override behavior. This only builds an in-memory
    # brief; ingest_prompt and project/author generation are never invoked.
    parsed = intake.build_brief(text)
    brief = intake.build_brief(text, overrides={
        "title": fields["title"], "genre": fields["genre"],
        "chapters": int(fields["chapters"]), "words": int(fields["words"]),
    })
    contract = brief["narrative_format"]
    if contract["confirmation_status"] != "confirmed":
        raise ValueError("Narrative format is pending confirmation; record the user's actual decision first")
    if (parsed["target_chapters"], parsed["target_words"]) != (brief["target_chapters"], brief["target_words"]):
        raise ValueError("Metadata targets and parsed narrative format disagree")
    warnings = [
        "Only prompt metadata and engine contracts checked; prose, story quality and actual word counts are not validated.",
        "Confirmation is a recorded declaration, not proof that a user approved the design.",
    ]
    if not fields["author"]:
        warnings.append("Author is intentionally blank; normal creation may generate a pen name. This check does not call a model.")
    volume_words = sum(volume["target_words"] for volume in contract["volumes"])
    if volume_words != brief["target_words"]:
        warnings.append("Volume word budgets differ from the book target; these are flexible planning estimates.")
    if contract["mode"] == "series_installment":
        warnings.append("This command generates only the current series installment; generate and launch other books separately.")
    return {
        "status": "metadata_and_contracts_valid", "prompt": str(prompt_path.resolve()),
        "title": brief["title"], "genre": brief["genre"],
        "chapters": brief["target_chapters"], "target_words": brief["target_words"],
        "mode": contract["mode"], "volumes": contract["volumes"],
        "series": contract["series"], "warnings": warnings,
    }


def shell_command(values: dict[str, str], launcher: Path, prompt: Path) -> str:
    """Quote data (including apostrophes, newlines and substitutions) as data."""
    assignments = [f"{key}={shlex.quote(str(value))}" for key, value in values.items()]
    return " \\\n".join([*assignments, f"{shlex.quote(str(launcher))} novel {shlex.quote(str(prompt))}"])


def command(prompt_path: Path, repo: Path, project: str, *, output=FORMATS,
            approval="auto", edit_mode="line", quality_policy="evidence_v1",
            max_retries=5, max_quality_repairs=2, method_mode=None) -> tuple[str, dict]:
    repo = repo.resolve()
    launcher, choices = _repo_contract(repo)
    if (not project.strip() or project in {".", ".."} or "/" in project
            or any(ord(char) < 32 or ord(char) == 127 for char in project)):
        raise ValueError("Project name must be one nonblank directory name without control characters")
    # deploy.sh strips the last dot suffix even for NOVEL_OS_PROJECT_NAME.
    if "." in project:
        raise ValueError("deploy.sh strips a dot suffix from project names; choose a name without dots to preserve it exactly")
    if not output or len(set(output)) != len(output):
        raise ValueError("Choose one or more distinct supported output formats")
    for flag, values in (("--output", output), ("--approval", [approval]),
                         ("--edit-mode", [edit_mode]), ("--quality-policy", [quality_policy])):
        if not choices[flag] or any(value not in choices[flag] for value in values):
            raise ValueError(f"Unsupported value for {flag}: {values}")
    if any(type(value) is not int or value < 0 for value in (max_retries, max_quality_repairs)):
        raise ValueError("Retry counts must be nonnegative integers")
    if method_mode is not None:
        if (method_mode not in (choices.get("--method-mode") or [])
                or "${NOVEL_OS_METHOD_MODE" not in launcher.read_text(encoding="utf-8")):
            raise ValueError("Repository does not support the selected method review mode")
    result = validate(prompt_path, repo)
    values = {
        "NOVEL_OS_NONINTERACTIVE": "1", "NOVEL_OS_PROJECT_NAME": project,
        "NOVEL_OS_TITLE": result["title"], "NOVEL_OS_GENRE": result["genre"],
        "NOVEL_OS_CHAPTERS": str(result["chapters"]), "NOVEL_OS_WORDS": str(result["target_words"]),
        "NOVEL_OS_APPROVAL": approval, "NOVEL_OS_EDIT_MODE": edit_mode,
        "NOVEL_OS_QUALITY_POLICY": quality_policy, "NOVEL_OS_MAX_RETRIES": str(max_retries),
        "NOVEL_OS_MAX_QUALITY_REPAIRS": str(max_quality_repairs),
        "NOVEL_OS_OUTPUT": " ".join(output), "NOVEL_OS_DRY_RUN": "0",
    }
    if method_mode is not None:
        values["NOVEL_OS_METHOD_MODE"] = method_mode
    return shell_command(values, launcher, prompt_path.resolve()), result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "command"):
        item = sub.add_parser(name)
        item.add_argument("prompt", type=Path)
        item.add_argument("--repo", type=Path, required=True, help="Trusted Novel OS checkout (read-only)")
        if name == "command":
            item.add_argument("--project", required=True)
            item.add_argument("--output", nargs="+", default=list(FORMATS))
            item.add_argument("--approval", default="auto")
            item.add_argument("--edit-mode", default="line")
            item.add_argument("--quality-policy", default="evidence_v1")
            item.add_argument("--max-retries", type=int, default=5)
            item.add_argument("--max-quality-repairs", type=int, default=2)
            item.add_argument("--method-mode", choices=("off", "advisory"))
    args = parser.parse_args(argv)
    try:
        if args.action == "validate":
            print(json.dumps(validate(args.prompt, args.repo), ensure_ascii=False, indent=2))
        else:
            rendered, result = command(args.prompt, args.repo, args.project,
                output=args.output, approval=args.approval, edit_mode=args.edit_mode,
                quality_policy=args.quality_policy, max_retries=args.max_retries,
                max_quality_repairs=args.max_quality_repairs, method_mode=args.method_mode)
            print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
            print(rendered)
    except (ValueError, OSError, KeyError, TypeError, ImportError, SyntaxError) as exc:
        print(f"Handoff validation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
