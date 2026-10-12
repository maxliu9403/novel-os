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
    cover.add_argument("--workspace-id", default=os.environ.get("NOVEL_OS_COVER_WORKSPACE_ID", "default"),
                       help="Owner workspace ID for snapshot-bound directions")
    commands = cover.add_subparsers(dest="cover_command", required=True)

    generate = commands.add_parser("generate", help="Generate 3-5 cover candidates")
    generate.add_argument("--project", required=True, help="Novel project directory")
    generate.add_argument("--prompt", default="", help="Legacy Prompt file, or - for stdin; bound directions use their snapshot")
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
    select.add_argument("--quality-acceptance-reason", default="", help="Optional legacy selection note; never required to select a completed cover")

    reject = commands.add_parser("reject", help="Reject a ready cover candidate")
    _add_candidate_arguments(reject)

    retry = commands.add_parser("retry", help="Retry one failed cover candidate")
    _add_candidate_arguments(retry)

    for name in ("propose-typography", "approve-typography", "apply-typography"):
        parser = commands.add_parser(name, help={
            "propose-typography": "Create an immutable local typography preview without an image call",
            "approve-typography": "Approve the exact reviewed typography amendment hash",
            "apply-typography": "Apply an approved preview and review it without an image call",
        }[name])
        parser.add_argument("--project", required=True)
        parser.add_argument("--cover-set", required=True)
        parser.add_argument("--candidate", required=True)
        if name != "approve-typography":
            parser.add_argument("--expected-revision", type=int, required=True)
        if name == "propose-typography":
            parser.add_argument("--proposal-json", required=True, help="UTF-8 overlay or contain_panel proposal JSON")
        else:
            parser.add_argument("--amendment-id", required=True)
            parser.add_argument("--expected-amendment-sha256", required=True)
        if name == "approve-typography":
            parser.add_argument("--confirm-layout", action="store_true",
                                help="Confirm the exact preview and proposed layout have been reviewed")

    review = commands.add_parser("review-analysis",
        help="Create a source-verified analysis revision from an independent review")
    review.add_argument("--project", required=True, help="Novel project directory")
    review.add_argument("--analysis-id", required=True, help="Parent analysis ID to review")
    review.add_argument("--expected-analysis-sha256", required=True,
        help="Exact parent analysis hash; reject a changed or mismatched parent")
    review.add_argument("--review-json", required=True,
        help="UTF-8 JSON file containing the independent source-cited review")

    for name, help_text in (("inspect-source", "Inspect the exact project skeleton"),
                            ("analyze", "Analyze every segment of an immutable skeleton snapshot"),
                            ("plan", "Plan cinematic directions from a complete analysis"),
                            ("approve", "Approve exact source, recipes and bounded image calls")):
        parser = commands.add_parser(name, help=help_text)
        parser.add_argument("--project", required=True)
        if name in {"inspect-source", "analyze"}:
            parser.add_argument("--source-type", choices=("project_input", "workshop_current", "workshop_prepared", "workshop_archived"), default="project_input")
            parser.add_argument("--session-id", default="")
            parser.add_argument("--archive-revision", type=int)
            parser.add_argument("--expected-content-sha256", default="")
        if name == "analyze":
            parser.add_argument("--force", action="store_true")
        if name == "plan":
            parser.add_argument("--analysis-id", required=True)
            parser.add_argument("--count", type=int, default=4)
            parser.add_argument("--freshness-policy", choices=("require_current_head", "pinned_snapshot"), default="require_current_head")
        if name == "approve":
            parser.add_argument("--direction-id", required=True)
            parser.add_argument("--approved-direction-sha256", required=True)
            parser.add_argument("--expected-brief-sha256", required=True)
            parser.add_argument("--confirm-source", action="store_true")
            parser.add_argument("--extra-image-attempts", type=int, choices=(0, 1), default=0)


def _add_candidate_arguments(parser) -> None:
    parser.add_argument("--project", required=True, help="Novel project directory")
    parser.add_argument("--cover-set", required=True, help="Cover set ID")
    parser.add_argument("--candidate", required=True, help="Candidate ID")
    parser.add_argument("--expected-revision", type=int, required=True)


def run_cover_command(args) -> int:
    try:
        project = Path(args.project).resolve()
        command = args.cover_command
        if command in {"inspect-source", "analyze", "review-analysis", "plan", "approve"}:
            _run_skeleton_command(args, project)
            return 0
        if command in {"propose-typography", "approve-typography", "apply-typography"}:
            _run_typography_command(args, project)
            return 0
        if command == "list":
            payload = [item.to_dict() for item in reversed(CoverStore(project).list())]
            _print_json(payload)
            return 0

        if command == "generate":
            settings = resolve_cover_settings()
            count = settings.count if args.count is None else args.count
            if not 3 <= count <= 5:
                raise ValueError("Cover candidate count must be between 3 and 5")
            if args.direction_id and CoverStore(project).load_direction(args.direction_id).source_binding:
                flow = _workflow(project, getattr(args, "workspace_id", "default"))
                brief, concepts, generation = flow.generation_arguments(args.direction_id, args.approved_direction_sha256, reserve=False)
                if args.prompt:
                    import hashlib
                    if hashlib.sha256(_read_prompt(args.prompt).encode("utf-8")).hexdigest() != brief.source_prompt_sha256:
                        raise CoverConflict("Explicit --prompt differs from the approved snapshot")
                brief, concepts, generation = flow.generation_arguments(args.direction_id, args.approved_direction_sha256)
                result = _generation_service(settings).generate(project.name, project, brief, concepts, **generation)
                _print_json(result.to_dict())
                return 0
            if not args.prompt:
                raise ValueError("Legacy generation requires --prompt")
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
            _workflow(project, getattr(args, "workspace_id", "default")).validate_candidate(args.cover_set, args.candidate, for_generation=True)
            result = _generation_service(settings).retry_candidate(
                project.name,
                project,
                args.cover_set,
                args.candidate,
                expected_revision=args.expected_revision,
            )
        elif command == "select":
            _workflow(project, getattr(args, "workspace_id", "default")).validate_candidate(args.cover_set, args.candidate)
            result = _mutation_service().select_candidate(
                project,
                args.cover_set,
                args.candidate,
                expected_revision=args.expected_revision,
                expected_active_revision=args.expected_active_revision,
                confirm_stale=args.confirm_stale,
                quality_acceptance_reason=getattr(args, "quality_acceptance_reason", ""),
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


def _run_typography_command(args, project):
    flow = _workflow(project, getattr(args, "workspace_id", "default"))
    flow.validate_candidate(args.cover_set, args.candidate)
    positional = (project.name, project, args.cover_set, args.candidate)
    if args.cover_command == "propose-typography":
        proposal = json.loads(Path(args.proposal_json).read_text(encoding="utf-8"))
        if not isinstance(proposal, dict):
            raise ValueError("Typography proposal JSON must contain an object")
        result = _mutation_service().propose_typography_amendment(*positional,
            expected_revision=args.expected_revision, proposal=proposal)
    elif args.cover_command == "approve-typography":
        result = _mutation_service().approve_typography_amendment(*positional, args.amendment_id,
            expected_amendment_sha256=args.expected_amendment_sha256, confirm_layout=args.confirm_layout)
    else:
        result = _review_service().apply_typography_amendment(*positional, args.amendment_id,
            expected_revision=args.expected_revision, expected_amendment_sha256=args.expected_amendment_sha256,
            source_guard=lambda: flow.validate_candidate(args.cover_set, args.candidate))
        result = result.to_dict()
    _print_json(result)


def _review_service() -> CoverService:
    """Local image review never resolves or constructs an image generation client."""
    db.configure(os.environ.get("NOVEL_OS_DB") or "sqlite:///./novel_os.db")
    media_root = Path(os.environ.get("NOVEL_OS_MEDIA_DIR", "./media"))
    return CoverService(media_store=LocalMediaStore(media_root), media_add=db.media_add,
                        visual_evaluator=_visual_evaluator())


def _generation_service(settings) -> CoverService:
    db.configure(os.environ.get("NOVEL_OS_DB") or "sqlite:///./novel_os.db")
    media_root = Path(os.environ.get("NOVEL_OS_MEDIA_DIR", "./media"))
    return CoverService(
        image_client=build_image_generation_client(settings),
        media_store=LocalMediaStore(media_root),
        media_add=db.media_add,
        visual_evaluator=_visual_evaluator(),
    )


def _visual_evaluator():
    from core.cover_quality import build_llm_visual_evaluator, unavailable_evaluator
    from core.llm_client import LLMClient, LLMError
    from core.studio_settings import resolve_cover_director_settings
    try:
        settings = resolve_cover_director_settings()
        return build_llm_visual_evaluator(LLMClient(provider=settings.provider or None,
            model=settings.model or None, base_url=settings.base_url or None,
            api_key=settings.api_key or None, timeout_seconds=settings.timeout_seconds,
            reasoning_effort=settings.reasoning_effort or None))
    except (ValueError, LLMError) as exc:
        return unavailable_evaluator(str(exc))


def _workflow(project, workspace_id="default"):
    from core.cover_sources import CoverSourceContext
    from core.cover_workflow import CoverWorkflow
    if not (project / "outputs/state/story_state.json").is_file():
        raise ValueError("A valid novel project directory is required")
    projects_root = Path(os.environ.get("NOVEL_OS_PROJECTS_DIR") or project.parent).resolve()
    workshop_root = projects_root.parent / "workshop_v2"
    if workspace_id != "default":
        if workspace_id in {".", ".."} or any(c in workspace_id for c in "/\\\x00"):
            raise ValueError("Invalid workspace ID")
        workshop_root /= workspace_id
    def project_resolver(project_id):
        if project_id != project.name:
            raise ValueError("CLI source does not belong to the selected project")
        return project
    store = CoverStore(project)
    return CoverWorkflow(CoverSourceContext(store.root, f"{workspace_id}:project:{project.name}",
        project_resolver=project_resolver, workshop_resolver=lambda session_id: workshop_root / session_id), store)


def _run_skeleton_command(args, project):
    from core.cover_sources import CoverSourceSelector, preview_source, capture_source
    from core.cover_analysis import CoverStoryAnalyzer
    from api.routes import get_cover_art_director
    flow = _workflow(project, getattr(args, "workspace_id", "default"))
    if args.cover_command in {"inspect-source", "analyze"}:
        selector = CoverSourceSelector(source_type=args.source_type,
            project_id=project.name if args.source_type == "project_input" else "",
            session_id=args.session_id, archive_revision=args.archive_revision,
            expected_content_sha256=args.expected_content_sha256)
        if args.cover_command == "inspect-source":
            _print_json(preview_source(flow.context, selector).to_dict())
            return
        snapshot = capture_source(flow.context, selector)
        director = get_cover_art_director()
        analysis = CoverStoryAnalyzer(complete=director._complete, model=director.model,
            **getattr(director, "analysis_configuration", {})).analyze(
            flow.context, snapshot, force=args.force)
        _print_json(analysis.to_dict())
    elif args.cover_command == "review-analysis":
        from core.cover_analysis_reviews import start_review_revision

        review = json.loads(Path(args.review_json).read_text(encoding="utf-8"))
        if not isinstance(review, dict):
            raise ValueError("Review JSON must contain an object")
        director = get_cover_art_director()
        analyzer = CoverStoryAnalyzer(complete=director._complete, model=director.model,
            **getattr(director, "analysis_configuration", {}))
        analysis = start_review_revision(flow.context, parent_analysis_id=args.analysis_id,
            expected_analysis_sha256=args.expected_analysis_sha256,
            external_review=review, analyzer=analyzer)
        _print_json(analysis.to_dict())
    elif args.cover_command == "plan":
        _print_json(flow.plan(args.analysis_id, get_cover_art_director(), count=args.count,
                             freshness_policy=args.freshness_policy).to_dict())
    else:
        _print_json(flow.approve(args.direction_id, expected_brief_sha256=args.expected_brief_sha256,
            approved_direction_sha256=args.approved_direction_sha256, confirm_source=args.confirm_source,
            extra_image_attempts=args.extra_image_attempts).to_dict())


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
