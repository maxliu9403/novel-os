"""Command-line adapter for project cover generation and management."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api import db
from api.cover_service import CoverService, CoverServiceError
from api.media import LocalMediaStore

try:
    from .cover_handoff import build_cover_concepts, parse_cover_handoff
    from .cover_store import CoverConflict, CoverStore
    from .image_client import ImageClientError, ImageGenerationClient
    from .studio_settings import resolve_cover_settings
except ImportError:  # pragma: no cover - used by `python core/orchestrator.py`
    from cover_handoff import build_cover_concepts, parse_cover_handoff
    from cover_store import CoverConflict, CoverStore
    from image_client import ImageClientError, ImageGenerationClient
    from studio_settings import resolve_cover_settings


def configure_cover_parser(subparsers) -> None:
    cover = subparsers.add_parser(
        "cover", help="Generate and manage 2048x3072 novel cover candidates"
    )
    commands = cover.add_subparsers(dest="cover_command", required=True)

    generate = commands.add_parser("generate", help="Generate 3-5 cover candidates")
    generate.add_argument("--project", required=True, help="Novel project directory")
    generate.add_argument("--prompt", required=True, help="Prompt file, or - for stdin")
    generate.add_argument("--count", type=int, default=None, help="Candidate count (3-5)")

    listing = commands.add_parser("list", help="List persisted cover sets")
    listing.add_argument("--project", required=True, help="Novel project directory")

    select = commands.add_parser("select", help="Select a ready cover candidate")
    _add_candidate_arguments(select)
    select.add_argument("--expected-active-revision", type=int, required=True)
    select.add_argument("--confirm-stale", action="store_true")

    reject = commands.add_parser("reject", help="Reject a ready cover candidate")
    _add_candidate_arguments(reject)

    retry = commands.add_parser("retry", help="Retry one failed cover candidate")
    _add_candidate_arguments(retry)


def _add_candidate_arguments(parser) -> None:
    parser.add_argument("--project", required=True, help="Novel project directory")
    parser.add_argument("--cover-set", required=True, help="Cover set ID")
    parser.add_argument("--candidate", required=True, help="Candidate ID")
    parser.add_argument("--expected-revision", type=int, required=True)


def run_cover_command(args) -> int:
    try:
        project = Path(args.project).resolve()
        command = args.cover_command
        if command == "list":
            payload = [item.to_dict() for item in reversed(CoverStore(project).list())]
            _print_json(payload)
            return 0

        if command == "generate":
            settings = resolve_cover_settings()
            count = settings.count if args.count is None else args.count
            if not 3 <= count <= 5:
                raise ValueError("Cover candidate count must be between 3 and 5")
            text = _read_prompt(args.prompt)
            brief = parse_cover_handoff(text)
            concepts = build_cover_concepts(brief, count=count)
            result = _generation_service(settings).generate(
                project.name, project, brief, concepts
            )
        elif command == "retry":
            settings = resolve_cover_settings()
            result = _generation_service(settings).retry_candidate(
                project.name,
                project,
                args.cover_set,
                args.candidate,
                expected_revision=args.expected_revision,
            )
        elif command == "select":
            result = CoverService().select_candidate(
                project,
                args.cover_set,
                args.candidate,
                expected_revision=args.expected_revision,
                expected_active_revision=args.expected_active_revision,
                confirm_stale=args.confirm_stale,
            )
        else:
            result = CoverService().reject_candidate(
                project,
                args.cover_set,
                args.candidate,
                expected_revision=args.expected_revision,
            )
        _print_json(result.to_dict())
        return 0
    except (
        CoverConflict,
        CoverServiceError,
        FileNotFoundError,
        ImageClientError,
        OSError,
        ValueError,
    ) as exc:
        print(f"Cover command failed: {exc}", file=sys.stderr)
        return 2


def _generation_service(settings) -> CoverService:
    db.configure(os.environ.get("NOVEL_OS_DB") or "sqlite:///./novel_os.db")
    media_root = Path(os.environ.get("NOVEL_OS_MEDIA_DIR", "./media"))
    return CoverService(
        image_client=ImageGenerationClient(settings),
        media_store=LocalMediaStore(media_root),
        media_add=db.media_add,
    )


def _read_prompt(value: str) -> str:
    if value == "-":
        text = sys.stdin.read()
    else:
        text = Path(value).read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("Cover prompt is empty")
    return text


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
