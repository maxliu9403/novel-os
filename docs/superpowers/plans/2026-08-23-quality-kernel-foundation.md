# Quality Kernel Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce revision-bound story contracts, immutable manuscript artifacts, canon proposals, evidence-backed quality reports, and receipt-based promotion without breaking existing projects or CLI runs.

**Architecture:** Keep Novel OS as a Python modular monolith. New runs write immutable artifact revisions and treat every agent state update as a proposal; only `PromotionService` may advance an artifact head and commit a canon delta. `StoryState`, legacy manuscript paths, and SQLite remain compatibility projections during this phase.

**Tech Stack:** Python 3.10+, dataclasses, JSON/JSONL, SHA-256, atomic `os.replace`, pytest, existing FastAPI/SQLModel adapters.

---

## Scope Boundaries

This plan implements the P0/P1 foundation only. It deliberately defers semantic rewrite loops, dependency-DAG invalidation, world simulation, and Web dashboards until the authority and evidence boundaries are stable.

The following invariants apply to every task:

- Agent output is a proposal, never canon by itself.
- Reports and findings are valid only for the exact `artifact_sha256` they evaluated.
- Invalid evidence cannot create a blocking finding.
- Promotion is idempotent and rejects a stale base revision or stale canon hash.
- Legacy stage files and `story_state.json` remain readable throughout migration.

## File Map

- Create `core/contracts.py`: versioned author, story, and chapter contracts.
- Create `core/artifacts.py`: content-addressed immutable revisions and artifact heads.
- Create `core/canon.py`: immutable canon delta proposals and explicit application.
- Create `core/proposals.py`: content-bound proposal persistence and reload.
- Create `core/canon_ledger.py`: append-only committed canon history.
- Create `core/quality/models.py`: evidence, findings, reports, and evaluation requests.
- Create `core/quality/evidence.py`: exact quote and character-span validation.
- Create `core/quality/lab.py`: deterministic quality facade and report policy.
- Create `core/promotion.py`: the sole artifact-head and canon commit authority.
- Create `core/model_router.py`: role-specific LLM selection with current settings as fallback.
- Modify `core/state_parser.py`: split parsing from explicit proposal application.
- Modify `core/orchestrator.py`: persist proposals and select clients by role.
- Modify `core/pipeline_models.py`: record revision, input, evaluation, and promotion provenance.
- Modify `core/pipeline_runner.py`: add revision capture, quality gate, and receipt promotion.
- Modify `api/db.py`, `api/services.py`, and `api/routes.py`: mirror immutable records and expose read APIs after the core path is stable.

### Task 1: Versioned Story And Chapter Contracts

**Files:**

- Create: `core/contracts.py`
- Create: `tests/test_contracts.py`
- Modify: `core/state_manager.py`
- Test: `tests/test_contracts.py`
- Test: `tests/test_state_tracking_updates.py`

- [ ] **Step 1: Write failing contract tests**

```python
from dataclasses import FrozenInstanceError

import pytest

from core.contracts import AuthorIntent, ChapterContract, StoryContract


def test_chapter_contract_rejects_missing_active_choice():
    with pytest.raises(ValueError, match="active_choice"):
        ChapterContract(
            chapter=1,
            goal="Win the lease",
            obstacle="The bank refuses",
            active_choice="",
            cost="Personal savings",
            irreversible_change="She signs the guarantee",
            local_payoff="The bid remains alive",
            ending_pressure="The rival submits a cash offer",
        )


def test_story_contract_round_trip_is_versioned_and_immutable():
    contract = StoryContract(
        title="What We Can Afford",
        genre="Contemporary women's fiction",
        intent=AuthorIntent(
            premise="A woman chooses shared ownership over rescue.",
            target_audience="Adult US commercial fiction",
        ),
        themes=("agency", "shared ownership"),
        non_negotiables=("No magical resolution",),
    )
    restored = StoryContract.from_dict(contract.to_dict())
    assert restored == contract
    assert restored.schema_version == 1
    with pytest.raises(FrozenInstanceError):
        restored.title = "Changed"
```

- [ ] **Step 2: Verify the tests fail because the module is missing**

Run: `python -m pytest tests/test_contracts.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'core.contracts'`.

- [ ] **Step 3: Implement frozen, validated, versioned contracts**

Implement these public types in `core/contracts.py`:

```python
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Mapping


def _required(name: str, value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _contract_id(data: Mapping[str, object]) -> str:
    encoded = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "contract:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AuthorIntent:
    premise: str
    target_audience: str
    language: str = "en-US"
    content_boundaries: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "premise", _required("premise", self.premise))
        object.__setattr__(self, "target_audience", _required("target_audience", self.target_audience))
        object.__setattr__(self, "language", _required("language", self.language))
        object.__setattr__(self, "content_boundaries", _strings(self.content_boundaries))

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["content_boundaries"] = list(self.content_boundaries)
        return data

    @property
    def contract_id(self) -> str:
        return _contract_id(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AuthorIntent":
        return cls(
            premise=value.get("premise", ""),
            target_audience=value.get("target_audience", ""),
            language=value.get("language", "en-US"),
            content_boundaries=_strings(value.get("content_boundaries")),
            schema_version=int(value.get("schema_version", 1)),
        )


@dataclass(frozen=True)
class StoryContract:
    title: str
    genre: str
    intent: AuthorIntent
    themes: tuple[str, ...] = ()
    non_negotiables: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required("title", self.title))
        object.__setattr__(self, "genre", _required("genre", self.genre))
        object.__setattr__(self, "themes", _strings(self.themes))
        object.__setattr__(self, "non_negotiables", _strings(self.non_negotiables))

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "genre": self.genre,
            "intent": self.intent.to_dict(),
            "themes": list(self.themes),
            "non_negotiables": list(self.non_negotiables),
            "schema_version": self.schema_version,
        }

    @property
    def contract_id(self) -> str:
        return _contract_id(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "StoryContract":
        return cls(
            title=value.get("title", ""),
            genre=value.get("genre", ""),
            intent=AuthorIntent.from_dict(value.get("intent", {})),
            themes=_strings(value.get("themes")),
            non_negotiables=_strings(value.get("non_negotiables")),
            schema_version=int(value.get("schema_version", 1)),
        )


@dataclass(frozen=True)
class ChapterContract:
    chapter: int
    goal: str
    obstacle: str
    active_choice: str
    cost: str
    irreversible_change: str
    local_payoff: str
    ending_pressure: str
    preserve_facts: tuple[str, ...] = ()
    allowed_knowledge: tuple[str, ...] = ()
    world_event_ids: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.chapter < 1:
            raise ValueError("chapter must be at least 1")
        for name in ("goal", "obstacle", "active_choice", "cost", "irreversible_change", "local_payoff", "ending_pressure"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        for name in ("preserve_facts", "allowed_knowledge", "world_event_ids"):
            object.__setattr__(self, name, _strings(getattr(self, name)))

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for name in ("preserve_facts", "allowed_knowledge", "world_event_ids"):
            data[name] = list(getattr(self, name))
        return data

    @property
    def contract_id(self) -> str:
        return _contract_id(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ChapterContract":
        return cls(
            chapter=int(value.get("chapter", 0)),
            goal=value.get("goal", ""),
            obstacle=value.get("obstacle", ""),
            active_choice=value.get("active_choice", ""),
            cost=value.get("cost", ""),
            irreversible_change=value.get("irreversible_change", ""),
            local_payoff=value.get("local_payoff", ""),
            ending_pressure=value.get("ending_pressure", ""),
            preserve_facts=_strings(value.get("preserve_facts")),
            allowed_knowledge=_strings(value.get("allowed_knowledge")),
            world_event_ids=_strings(value.get("world_event_ids")),
            schema_version=int(value.get("schema_version", 1)),
        )
```

Reject non-positive chapter numbers and blank required strings. Convert JSON lists to tuples in `from_dict`. Give every contract a stable `contract_id` computed from schema-versioned, sorted, compact JSON. Add optional `contract_id`, `canonical_revision_id`, and `last_evaluation_id` fields with empty-string defaults to `ChapterState`; old JSON must load unchanged. Store the current story contract ID in `StoryState.metadata["story_contract_id"]` only after a contract revision is created.

- [ ] **Step 4: Run focused and compatibility tests**

Run: `python -m pytest tests/test_contracts.py tests/test_state_tracking_updates.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add core/contracts.py core/state_manager.py tests/test_contracts.py
git commit -m "feat(quality): add versioned story contracts"
```

### Task 2: Immutable Artifact Revisions

**Files:**

- Create: `core/artifacts.py`
- Create: `tests/test_artifact_revisions.py`

- [ ] **Step 1: Write failing artifact-store tests**

```python
from pathlib import Path

import pytest

from core.artifacts import ArtifactStore, StaleArtifactHead


def test_same_text_reuses_content_blob_but_creates_traceable_revision(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    first = store.put_text(chapter=1, kind="draft", text="A choice.", source="scribe")
    second = store.put_text(
        chapter=1,
        kind="revised",
        text="A choice.",
        source="editor",
        parent_revision_id=first.revision_id,
    )
    assert first.sha256 == second.sha256
    assert first.revision_id != second.revision_id
    assert store.read_text(second.revision_id) == "A choice."


def test_head_promotion_rejects_stale_expected_revision(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    first = store.put_text(chapter=2, kind="final", text="One", source="import")
    second = store.put_text(chapter=2, kind="final", text="Two", source="repair")
    store.set_head(2, "final", first.revision_id, expected_revision_id=None)
    with pytest.raises(StaleArtifactHead):
        store.set_head(2, "final", second.revision_id, expected_revision_id="wrong")
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_artifact_revisions.py -q`

Expected: collection fails because `core.artifacts` does not exist.

- [ ] **Step 3: Implement the content-addressed store**

`ArtifactStore` must write blobs under `outputs/artifacts/sha256/<sha256>`, append immutable revision metadata to `outputs/artifacts/revisions.jsonl`, and atomically replace `outputs/artifacts/heads.json`. Implement `put_text`, `put_json`, `get_revision`, `read_text`, `get_head`, and `set_head`. A revision records chapter, kind, SHA, byte length, parent, source, provider, model, prompt SHA, contract revision IDs, and UTC timestamp. Add `story_contract` and `chapter_contract` kinds; persist their canonical JSON through `put_json` and advance separate contract heads.

- [ ] **Step 4: Verify GREEN and corruption handling**

Add tests proving that a modified blob raises `ArtifactIntegrityError`, duplicate revision IDs are rejected, and repeated identical head updates are idempotent.

Run: `python -m pytest tests/test_artifact_revisions.py -q`

Expected: all artifact revision tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add core/artifacts.py tests/test_artifact_revisions.py
git commit -m "feat(quality): store immutable artifact revisions"
```

### Task 3: Pure Canon Proposals

**Files:**

- Create: `core/canon.py`
- Create: `core/proposals.py`
- Create: `tests/test_canon_proposals.py`
- Modify: `core/state_parser.py`

- [ ] **Step 1: Write failing proposal-isolation tests**

```python
from copy import deepcopy

from core.canon import build_canon_proposal
from core.state_manager import StoryState


def test_build_proposal_does_not_mutate_story_state(tmp_path):
    state = StoryState(str(tmp_path))
    before = deepcopy(
        (state.characters, state.plot_threads, state.chapters, state.timeline)
    )
    proposal = build_canon_proposal(
        state,
        chapter=1,
        agent_name="scribe",
        source_artifact_sha="a" * 64,
        text="[SCRIBE_STATE_UPDATE]\nKey_Events: Door opens\n[/SCRIBE_STATE_UPDATE]",
    )
    assert (state.characters, state.plot_threads, state.chapters, state.timeline) == before
    assert proposal.source_artifact_sha == "a" * 64
    assert proposal.delta["key_events"] == ["Door opens"]
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_canon_proposals.py -q`

Expected: collection fails because `core.canon` does not exist.

- [ ] **Step 3: Implement proposal creation and explicit application**

Define immutable `CanonDeltaProposal` with proposal ID, chapter, agent, source artifact SHA, parsed delta, schema version, and timestamp. Derive `proposal_id` from canonical JSON excluding timestamp. `ProposalStore` writes one immutable JSON record to `outputs/state/proposals/<proposal_id>.json`, reloads by ID, and verifies both the record hash and source artifact SHA. Add pure `parse_agent_output()` to `state_parser.py`. Keep `ingest_agent_output()` as a documented legacy wrapper, and add `apply_canon_proposal(state, proposal, actual_artifact_sha)` that rejects SHA mismatch before delegating to the existing state mutation logic.

- [ ] **Step 4: Add an explicit non-mutating orchestrator path**

Add `self.state_update_mode: Literal["legacy_apply", "proposal_only"] = "legacy_apply"` to `NovelOrchestrator` and let `_run_agent_or_save_prompt()` resolve an optional call override against that property. Apply the same mode inside `_curate()`, `submit_draft()`, and `submit_edit()`; no independent agent path may call `ingest_agent_output()` when the mode is `proposal_only`. In proposal mode, save raw response, manuscript text, and proposals through `ProposalStore`, but never call `ingest_agent_output()` or `save_state()`. Book-pipeline calls must use `proposal_only`; legacy single-stage CLI calls retain `legacy_apply` until Task 7 completes the cutover. Add `test_proposal_only_agent_run_does_not_mutate_or_save_story_state`, `test_proposal_only_style_run_does_not_mutate_or_save_story_state`, and `test_legacy_agent_run_preserves_existing_state_update_behavior`.

- [ ] **Step 5: Verify proposal and legacy behavior**

Run: `python -m pytest tests/test_canon_proposals.py tests/test_state_tracking_updates.py tests/test_pipeline_orchestrator.py -q`

Expected: proposal tests and all legacy state tracking tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add core/canon.py core/proposals.py core/state_parser.py core/orchestrator.py tests/test_canon_proposals.py tests/test_pipeline_orchestrator.py
git commit -m "feat(quality): isolate canon delta proposals"
```

### Task 4: Evidence-Bound Quality Reports

**Files:**

- Create: `core/quality/__init__.py`
- Create: `core/quality/models.py`
- Create: `core/quality/evidence.py`
- Create: `tests/test_quality_evidence.py`

- [ ] **Step 1: Write failing evidence tests**

```python
import hashlib

from core.quality.evidence import verify_evidence
from core.quality.models import EvidenceSpan, QualityFinding


def test_quote_from_another_revision_cannot_block_candidate():
    text = "Maren signed the agreement."
    evidence = EvidenceSpan(
        artifact_sha256="b" * 64,
        quote="Maren signed the agreement.",
        start=None,
        end=None,
    )
    actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert verify_evidence(text, actual_sha, evidence) is False


def test_invalid_evidence_downgrades_blocking_finding():
    text = "Actual text"
    actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    finding = QualityFinding.create(
        artifact_sha256=actual_sha,
        category="causality",
        severity="critical",
        message="Choice is missing",
        evidence=(EvidenceSpan(actual_sha, "not present", None, None),),
        suggested_action="Show the decision",
        repair_class="chapter_structure",
    )
    assert finding.with_verified_evidence(text, actual_sha).blocking is False
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_quality_evidence.py -q`

Expected: collection fails because `core.quality` does not exist.

- [ ] **Step 3: Implement immutable evidence, finding, request, and report models**

Implement `EvidenceSpan`, `QualityFinding`, `EvaluationRequest`, and `EvaluationReport`. Validate SHA-256 shape, severity, repair class, dimension range, and status. `verify_evidence(text, actual_artifact_sha, evidence)` must recompute SHA-256 from the exact UTF-8 bytes, require both hashes to match, then validate the exact quote or `[start, end)` span. When quote and span both exist they must identify the same text. A report records rubric version, prompt SHA, evaluator provider/model, hard gates, semantic dimensions, findings, and creation time.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_quality_evidence.py -q`

Expected: all evidence and model tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add core/quality tests/test_quality_evidence.py
git commit -m "feat(quality): bind findings to artifact evidence"
```

### Task 5: Deterministic QualityLab And Role Model Routing

**Files:**

- Create: `core/quality/lab.py`
- Create: `core/model_router.py`
- Create: `tests/test_quality_lab.py`
- Create: `tests/test_model_router.py`
- Modify: `core/orchestrator.py`

- [ ] **Step 1: Write failing facade and routing tests**

```python
from core.model_router import ModelRouter
from core.quality.lab import QualityLab
from core.quality.models import EvaluationRequest


def test_role_override_wins_over_global_model(monkeypatch):
    monkeypatch.setenv("NOVEL_OS_MODEL", "writer-default")
    monkeypatch.setenv("NOVEL_OS_JUDGE_MODEL", "judge-model")
    client = ModelRouter().client_for("judge")
    assert client.model == "judge-model"


def test_quality_lab_rejects_report_for_different_revision(tmp_path):
    request = EvaluationRequest.for_text(chapter=1, text="Current chapter")
    report = QualityLab().evaluate_deterministic(request, continuity_findings=[])
    assert report.artifact_sha256 == request.artifact_sha256
    assert report.rubric_version == "quality.v1"
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_quality_lab.py tests/test_model_router.py -q`

Expected: collection fails because the new facade and router do not exist.

- [ ] **Step 3: Implement deterministic evaluation and fallback routing**

`QualityLab.evaluate_deterministic()` adapts current continuity findings into revision-bound `QualityFinding` objects, deduplicates stable signatures, verifies evidence, and produces hard gates without inventing a literary total score. `ModelRouter.client_for(role)` resolves `NOVEL_OS_<ROLE>_MODEL`, `NOVEL_OS_<ROLE>_PROVIDER`, `NOVEL_OS_<ROLE>_BASE_URL`, and `NOVEL_OS_<ROLE>_MAX_TOKENS`; absent role variables fall back to `NOVEL_OS_MODEL`, `NOVEL_OS_LLM_PROVIDER`, `NOVEL_OS_BASE_URL`, and `NOVEL_OS_MAX_TOKENS`, then existing `LLMClient` defaults. Add a judge base-URL/provider test using `openai_compatible`; never copy or log API keys.

- [ ] **Step 4: Route existing agents without changing default behavior**

Replace the single `_llm` cache in `NovelOrchestrator` with a role-keyed client cache. Map `scribe` to `writer`, `continuity_guardian` to `guardian`, and keep the current environment as fallback for every role. Add `runtime_provenance_for(agent_name) -> tuple[str, str]`; `PipelineRunner` must ask for the actual agent used by each stage rather than reading `orchestrator._llm`. Add tests that writer, guardian, and style stages each persist their own provider/model in `StageResult`.

- [ ] **Step 5: Verify routing and orchestrator compatibility**

Run: `python -m pytest tests/test_quality_lab.py tests/test_model_router.py tests/test_pipeline_orchestrator.py tests/test_claude_cli_backend.py -q`

Expected: all selected tests pass and current Sub2API/OpenAI-compatible defaults remain unchanged.

- [ ] **Step 6: Commit Task 5**

```bash
git add core/quality/lab.py core/model_router.py core/orchestrator.py tests/test_quality_lab.py tests/test_model_router.py
git commit -m "feat(quality): add quality facade and role model routing"
```

### Task 6: Canon Ledger And Receipt-Based Promotion

**Files:**

- Create: `core/canon_ledger.py`
- Create: `core/promotion.py`
- Create: `core/project_lock.py`
- Create: `tests/test_promotion.py`
- Create: `tests/test_canon_hash.py`

- [ ] **Step 1: Write failing stale-base and idempotency tests**

```python
import pytest

from core.promotion import PromotionRequest, PromotionService, StalePromotionBase


def test_promotion_rejects_stale_base_without_changing_head(project_with_candidate):
    service, request = project_with_candidate
    stale = request.with_base_artifact_sha("0" * 64)
    with pytest.raises(StalePromotionBase):
        service.promote(stale)
    assert service.artifacts.get_head(request.chapter, "final") is None


def test_repeated_idempotency_key_returns_same_receipt(project_with_candidate):
    service, request = project_with_candidate
    first = service.promote(request)
    second = service.promote(request)
    assert second.receipt_id == first.receipt_id
    assert second.new_artifact_sha == first.new_artifact_sha
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_promotion.py -q`

Expected: collection fails because `core.promotion` does not exist.

- [ ] **Step 3: Implement CanonLedger and PromotionService**

Define `canonical_canon_bytes(state)` as schema-versioned, sorted-key, compact UTF-8 JSON over metadata excluding `last_saved`, story bible, characters, Codex, relationships, collections, continuity exemptions, plot threads, chapters, timeline, and style profile. Exclude compile styles, binder, session logs, file paths, and timestamps because they are presentation or operational projections. A load/save with no semantic change must keep the same canon SHA; changing a character, chapter fact, relationship, thread, timeline event, exemption, or story rule must change it.

The ledger appends committed entries to `outputs/state/canon_ledger.jsonl` and validates `base_canon_sha`. `PromotionService.promote()` verifies candidate integrity, report SHA alignment, passing hard gates, proposal source SHA, and expected artifact/canon bases. It prepares a journal, applies the proposal to a temporary `StoryState`, atomically replaces canonical state and head projections, and writes a committed `PromotionReceipt`. Reusing an idempotency key returns the original committed receipt.

Serialize promotion under a per-project advisory lock in `core/project_lock.py` using `fcntl.flock` on `outputs/state/.promotion.lock`. Acquire the lock before re-reading heads, canon SHA, or receipts; release it only after committed receipt persistence. The journal states are `prepared`, `state_committed`, `head_committed`, and `committed`, and recovery resumes the same idempotency key from its durable state.

- [ ] **Step 4: Add canonical hash, crash, and concurrency tests**

Cover hash stability across no-op load/save, hash sensitivity to canon changes, failure before state replacement, and failure before committed receipt. Add a barrier-based two-process test where both requests share one base: exactly one different-idempotency promotion may commit, while two requests with the same idempotency key return one receipt. Recovery must either finish the same transaction or leave the old head and canon intact; it must never create two committed receipts.

- [ ] **Step 5: Verify GREEN**

Run: `python -m pytest tests/test_promotion.py tests/test_canon_hash.py tests/test_artifact_revisions.py tests/test_canon_proposals.py -q`

Expected: all promotion, artifact, and proposal tests pass.

- [ ] **Step 6: Commit Task 6**

```bash
git add core/canon_ledger.py core/promotion.py core/project_lock.py tests/test_promotion.py tests/test_canon_hash.py
git commit -m "feat(quality): promote revisions with canon receipts"
```

### Task 7: Pipeline Shadow Integration And Compatibility Gate

**Files:**

- Modify: `core/pipeline_models.py`
- Modify: `core/pipeline_runner.py`
- Modify: `core/orchestrator.py`
- Modify: `tests/test_pipeline_models.py`
- Modify: `tests/test_pipeline_runner.py`
- Modify: `tests/test_pipeline_integration.py`

- [ ] **Step 1: Write failing pipeline provenance tests**

Add tests named:

```text
test_stage_result_round_trip_preserves_revision_and_input_hashes
test_quality_gate_blocks_candidate_without_promotion_receipt
test_resume_reuses_valid_revision_without_rewriting
test_legacy_manifest_without_quality_fields_resumes
test_guardian_output_does_not_mutate_canon_before_promotion
test_style_output_does_not_mutate_canon_before_promotion
test_run_spec_rejects_unknown_quality_policy
test_evidence_policy_requires_promotion_receipt
test_resume_reloads_bound_canon_proposal_by_id
test_intake_persists_story_contract_revision
test_evidence_plan_requires_chapter_contract
test_scribe_prompt_contains_current_chapter_contract
test_writer_guardian_and_style_record_actual_role_model
```

Each test must use real temporary project files and the existing fake orchestrator pattern.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_pipeline_models.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py -q`

Expected: new assertions fail because stage results have no revision, evaluation, or receipt fields.

- [ ] **Step 3: Extend RunSpec and StageResult compatibly**

Add `quality_policy` to `RunSpec` with allowed values `legacy` and `evidence_v1`, defaulting missing legacy manifests to `legacy`. Add the CLI option `--quality-policy` to `run`; resume keeps the manifest value and cannot silently switch policy. Add empty-default fields for `input_hashes`, `revision_id`, `story_contract_revision_id`, `chapter_contract_revision_id`, `canon_proposal_ids`, `evaluation_report_ids`, and `promotion_receipt_id` to `StageResult`. Old manifests must deserialize without migration and retain their current retry semantics.

- [ ] **Step 4: Capture revisions and proposals in shadow mode**

Immediately after creating the orchestrator, `PipelineRunner` sets `orchestrator.state_update_mode = "proposal_only"`. Each agent stage records `canon_proposal_ids`; resume reloads those immutable proposal files, verifies their record hashes and source artifact SHA, and never reparses a different raw response. After each manuscript stage succeeds, capture an `ArtifactRevision` without moving the legacy file.

During intake, construct `AuthorIntent` and `StoryContract` from the resolved brief/foundation, persist canonical JSON as a `story_contract` revision, and record its ID in the manifest and `StoryState.metadata`. For `evidence_v1`, require the Architect chapter output to contain a machine-readable `[CHAPTER_CONTRACT]` JSON block; persist it as a `chapter_contract` revision, attach both current contract revision IDs to downstream artifacts, include the full ChapterContract in the Scribe prompt, and reject promotion if either contract head changed. Legacy runs may omit these blocks and continue with empty contract references.

Existing manifests default to `legacy` and retain old output behavior while recording a synthetic legacy receipt. New runs with `quality_policy="evidence_v1"` require a passing report and use `PromotionService`; `_copy_candidate_to_final()` and direct `chapter.status = "complete"` are forbidden on this path. `PipelineRunner` maps each phase to its agent and calls `runtime_provenance_for()` so StageResult records the model that actually produced or judged that artifact.

- [ ] **Step 5: Verify full Python regression suite**

Run: `python -m pytest`

Expected: all tests pass; the starting baseline is 426 tests before this plan adds coverage.

- [ ] **Step 6: Commit Task 7**

```bash
git add core/pipeline_models.py core/pipeline_runner.py core/orchestrator.py tests/test_pipeline_models.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py
git commit -m "feat(quality): gate pipeline promotion on evidence"
```

### Task 8: API Read Projection And Migration Documentation

**Files:**

- Modify: `api/db.py`
- Modify: `api/models.py`
- Modify: `api/services.py`
- Modify: `api/routes.py`
- Create: `tests/test_quality_api.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/WORKFLOWS.md`

- [ ] **Step 1: Write failing read-API tests**

Cover `GET /api/projects/{project_id}/chapters/{chapter}/quality`, artifact revision history, and promotion receipt lookup. Assert that API ingest cannot overwrite the canonical final head and that reports expose artifact/rubric/model provenance without API keys.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_quality_api.py tests/test_provenance_api.py tests/test_review_api.py -q`

Expected: quality routes return 404 before implementation.

- [ ] **Step 3: Add projection tables and read services**

Add append-oriented SQLModel rows for artifact revisions, evaluation reports, findings, and promotion receipts. The core file records remain authoritative in this phase. Existing mutable `Artifact` rows remain the UI compatibility projection.

- [ ] **Step 4: Route every Final or canon mutation through one authority**

Keep existing endpoint contracts, but route stage acceptance, direct Final text save, ProseMirror document save, snapshot restore, lazy Final document conversion performed by `get_final_doc()`, and consequence acceptance through `PromotionService`. Human text edits and legacy conversions first create an immutable candidate revision and an `author_edit` or `legacy_migration` decision receipt. A GET may return an in-memory converted document, but cannot persist canonical Final without a migration receipt. Consequence acceptance must create a canon proposal bound to that revision rather than calling `apply_to_state()` directly. A force override records reason, scope, actor, candidate SHA, and base SHA; it cannot bypass integrity, locking, receipt creation, or stale-base validation. Add one compatibility test for each mutation path and assert that none can create a canonical Final without a receipt.

- [ ] **Step 5: Document authority and migration boundaries**

Update architecture and workflow docs to state that artifact heads plus canon receipts are canonical, while legacy paths, StoryState snapshots, and SQLite rows are projections. Document `quality_policy`, role-specific model variables, retry behavior, and rollback using the previous receipt.

- [ ] **Step 6: Run complete backend verification**

Run: `python -m pytest`

Run: `python -m compileall -q core api`

Expected: both commands exit 0.

- [ ] **Step 7: Commit Task 8**

```bash
git add api/db.py api/models.py api/services.py api/routes.py tests/test_quality_api.py docs/ARCHITECTURE.md docs/WORKFLOWS.md
git commit -m "feat(api): expose revision-bound quality evidence"
```

## Completion Review

After Task 8, dispatch a final reviewer against the full branch diff. The implementation is ready for integration only when:

- every new behavior followed a witnessed RED/GREEN cycle;
- the full Python suite and compileall pass from a clean process;
- no direct pipeline path can commit Guardian output before promotion;
- every canonical final has an artifact revision and Promotion Receipt;
- legacy projects and manifests still load and resume;
- API responses expose provenance but never credentials;
- `git diff --check` exits 0 and the worktree contains no untracked runtime artifacts.
