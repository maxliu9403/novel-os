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
    from .cover_models_v2 import CoverBriefV2
    from .cover_handoff import build_cover_concepts, resolve_cover_brief
    from .cover_prompt_compiler import COMPILER_VERSION, scene_to_cover_concept
    from .cover_store import CoverConflict, CoverStore
    from .cover_validator import validate_direction
    from .image_client import ImageClientError, build_image_generation_client
    from .studio_settings import resolve_cover_settings
except ImportError:  # pragma: no cover - used by `python core/orchestrator.py`
    from cover_models_v2 import CoverBriefV2
    from cover_handoff import build_cover_concepts, resolve_cover_brief
    from cover_prompt_compiler import COMPILER_VERSION, scene_to_cover_concept
    from cover_store import CoverConflict, CoverStore
    from cover_validator import validate_direction
    from image_client import ImageClientError, build_image_generation_client
    from studio_settings import resolve_cover_settings


def configure_cover_parser(subparsers) -> None:
    cover = subparsers.add_parser(
        "cover", help="Generate and manage portrait 2:3 novel cover candidates"
    )
    commands = cover.add_subparsers(dest="cover_command", required=True)

    generate = commands.add_parser("generate", help="Generate 3-5 cover candidates")
    generate.add_argument("--project", required=True, help="Novel project directory")
    generate.add_argument("--prompt", required=True, help="Prompt file, or - for stdin")
    generate.add_argument("--count", type=int, default=None, help="Candidate count (3-5)")
    generate.add_argument("--direction-id", default="", help="Approved v2 direction id")
    generate.add_argument(
        "--approved-direction-sha256", default="", help="Exact approved direction content hash"
    )

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
            brief = resolve_cover_brief(project, prompt_text=text)
            compiler_version = ""
            if args.direction_id or args.approved_direction_sha256:
                if not args.direction_id or not args.approved_direction_sha256:
                    raise ValueError("Both direction id and approved direction hash are required")
                direction_store = CoverStore(project)
                direction = direction_store.require_latest_direction(args.direction_id)
                if (
                    not isinstance(brief, CoverBriefV2)
                    or brief.source_prompt_sha256 != direction.brief_sha256
                ):
                    direction_store.mark_direction_stale(
                        args.direction_id, brief.source_prompt_sha256
                    )
                    raise CoverConflict("Cover direction is stale for the current Prompt")
                if direction.status != "approved":
                    raise CoverConflict("Cover direction must be approved before generation")
                if direction.direction_sha256 != args.approved_direction_sha256:
                    raise CoverConflict("Cover direction approval hash mismatch")
                brief_payload = direction_store.load_direction_brief(args.direction_id)
                if not brief_payload:
                    raise CoverConflict("Approved direction is missing its versioned cover brief")
                brief = CoverBriefV2.from_dict(
                    brief_payload,
                    source_prompt_sha256=direction.brief_sha256,
                    foundation_sha256=str(brief_payload.get("foundation_sha256") or ""),
                )
                findings = validate_direction(brief, direction)
                if findings:
                    raise CoverConflict("Cover direction is stale or invalid")
                concepts = [scene_to_cover_concept(brief, scene) for scene in direction.plans]
                compiler_version = COMPILER_VERSION
            else:
                if isinstance(brief, CoverBriefV2):
                    raise CoverConflict(
                        "Versioned cover facts require an approved art direction before generation"
                    )
                concepts = build_cover_concepts(brief, count=count)
            result = _generation_service(settings).generate(
                project.name, project, brief, concepts, compiler_version=compiler_version
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
            result = _mutation_service().select_candidate(
                project,
                args.cover_set,
                args.candidate,
                expected_revision=args.expected_revision,
                expected_active_revision=args.expected_active_revision,
                confirm_stale=args.confirm_stale,
            )
        else:
            result = _mutation_service().reject_candidate(
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
        image_client=build_image_generation_client(settings),
        media_store=LocalMediaStore(media_root),
        media_add=db.media_add,
    )


def _mutation_service() -> CoverService:
    media_root = Path(os.environ.get("NOVEL_OS_MEDIA_DIR", "./media"))
    return CoverService(media_store=LocalMediaStore(media_root))


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
