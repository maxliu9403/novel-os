# Commercial Story Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the distilled 35-60 female-reader corpus findings into an enforceable Novel OS story-design, originality, free-trial, and chapter-quality system without exposing source prose to generation or using advertising metrics.

**Architecture:** The workshop emits one approved `COMMERCIAL_STORY_JSON` contract from a compact local pattern reference. Prompt Intake validates and persists that contract, Architect must preserve it byte-semantically in the story foundation, and two deep modules enforce originality plus reader-value progression through deterministic, artifact-bound reports. Raw corpus files, titles, names, prose, and nearest-neighbor retrieval never enter runtime prompts.

**Tech Stack:** Python 3.11 standard library, existing Novel OS `ArtifactStore`, `PipelineRunner`, `StoryState`, `ModelRouter`, and Quality evidence models, pytest, Markdown skill references, JSON schema-style frozen dataclasses.

**Spec:** [2026-08-31-commercial-free-trial-corpus-study.md](/Users/max/OpenSource/Novel-OS/docs/research/2026-08-31-commercial-free-trial-corpus-study.md)

## Global Constraints

- The target audience remains user-confirmed. The default reader profile is women ages 35-60 seeking recognition, anger, pity, regret, agency, belonging, and hope; a different user-confirmed audience overrides that profile.
- Remove mandatory online audience research from `novel-brainstorm-workshop`. No web search, source URL ledger, pending research query list, or conversion claim is required by the default workflow.
- The corpus snapshot is identified only by aggregate metadata and SHA-256 `a5458ce1332e5b74c52889e4a5aed5b9809f69e6e1a8f09cde25c5ab974ee49f`.
- Runtime generation never receives raw corpus prose, sample titles, names, paths, dialogue, signature metaphors, or a nearest matching story.
- Originality is enforced on abstract premise dimensions and ordered action patterns. It does not treat ordinary genre conventions as copied expression and does not claim copyright adjudication.
- New skill-generated prompts carry one schema-v1 `COMMERCIAL_STORY_JSON` block. Existing prompts without the block remain readable and use the current pipeline behavior; no historical project or completed run is backfilled.
- Activated contracts are immutable after Prompt Intake. Architect may expand characters, world, and scenes but must echo the approved commercial contract with the same `contract_id`.
- `evidence_v1` chapter contracts activated by a commercial story use schema version 2. Schema-v1 chapter artifacts remain readable.
- No advertising clicks, conversion rates, revenue, A/B data, or fabricated performance predictions enter contracts or quality gates.
- Quality findings block only when their contract/artifact bindings and exact evidence are valid. Scores never replace evidence.
- Reader-value intent and reader-value delivery are separate authorities: chapter contracts describe intent; only a passed, candidate-bound commercial report may contribute delivered value to canon during chapter promotion.
- No Web UI, cover generation, H5 schema, database migration, publication-copy logic, or manuscript backfill is part of this implementation.
- Preserve unrelated working-tree files and commit the current cover compiler fix separately before executing this plan.

## Lifecycle

```text
workshop review
  -> COMMERCIAL_STORY_JSON in approved Prompt
  -> intake validates and persists contract
  -> outline echoes contract in foundation
  -> foundation.originality
  -> foundation.commit
  -> chapter.plan
  -> chapter.design_check
  -> chapter.write/edit/validate/style
  -> chapter.commercial_check (bounded repair when needed)
  -> chapter.promote
  -> commercial.free_trial_review (once, after configured free chapter 3 or 4)
  -> remaining chapter cycles
  -> commercial.book_review
  -> ending.review / book.check / publication.copy / compile
```

## Shared Controlled Vocabulary

These values are the complete schema-v1 vocabulary. Adding a value later requires a schema revision; implementers must not invent synonyms while parsing.

```python
READER_JOBS = {
    "recognition", "anger", "pity", "regret", "belonging", "agency", "hope",
}
LIFE_STAGES = {
    "pregnancy", "active_parenting", "caregiving", "bereavement",
    "marriage_transition", "career_midpoint", "empty_nest", "illness",
    "retirement_transition", "second_start",
}
INVISIBLE_LABOR = {
    "childcare", "eldercare", "domestic_management", "emotional_management",
    "professional_ghostwork", "family_finance", "reputation_management",
    "medical_coordination", "community_service",
}
SACRED_ASSETS = {
    "child_safety", "professional_identity", "home", "bodily_autonomy",
    "inheritance_memory", "career_work", "financial_security",
    "shared_commitment", "custody_stability", "community_role",
}
RESOURCE_DIMENSIONS = {
    "time", "space", "name", "labor", "body", "money", "memory",
    "relationship", "system", "future",
}
BENEFICIARY_ROLES = {
    "affair_partner", "favored_child", "sibling", "parent", "colleague",
    "employer", "community_insider", "institution", "secret_family",
    "unknown_third_party",
}
PROOF_TYPES = {
    "document", "timestamp", "key_or_access", "financial_record",
    "medical_record", "message_log", "child_behavior", "witness",
    "version_history", "physical_object", "technical_metadata",
}
DEADLINE_TYPES = {
    "public_event", "legal_effective_date", "medical_window",
    "financial_close", "child_harm_threshold", "anniversary",
    "numbered_repeat", "contract_renewal", "departure_window",
}
AGENCY_SOURCES = {
    "professional_skill", "records_habit", "ownership_right",
    "process_knowledge", "trusted_ally", "legal_knowledge",
    "caregiving_network", "community_role", "financial_control",
}
ACTION_COSTS = {
    "housing", "income", "reputation", "custody_stability",
    "relationship_loss", "medical_option", "career_access",
    "community_belonging", "physical_safety",
}
BELONGING_ANCHORS = {
    "self", "child", "work", "friend", "community", "family_of_choice",
    "romance",
}
RELATIONSHIP_SHAPES = {
    "spouse_triangle", "extended_family_intrusion", "parent_child_displacement",
    "workplace_erasure", "caregiver_abandonment", "community_betrayal",
    "second_chance_romance", "intimate_mystery", "institutional_adversary",
    "fantasy_bond",
}
FREE_ARC_ACTIONS = {
    "recognize", "verify", "document", "test_boundary", "protect",
    "withdraw", "disclose", "leave", "accept_cost", "counter_move",
}
SATISFACTION_TYPES = {
    "boundary", "evidence", "competence", "identity", "relationship",
    "consequence", "none",
}
HOOK_TYPES = {
    "decision", "consequence", "evidence", "relationship_shift", "deadline",
    "arrival",
}
```

`PremiseEngine.boundary_transfer` is one `RESOURCE_DIMENSIONS` value. `protagonist_desire_beyond_escape`, `resource_change`, and observable consequences remain story-specific nonblank text. `ReaderContract.emotional_jobs` has exactly the seven `READER_JOBS` keys with integer priorities from 0 through 5; at least three must be nonzero. `reader_jobs` on a chapter is an ordered tuple of one through three unique values.

Child-voice and institutional-plausibility checks use `pass`, `fail`, or `not_applicable`. `not_applicable` requires a nonblank reason and no evidence span. `pass` or `fail` requires an `EvidenceSpan` bound to the exact candidate SHA with character offsets and an exact matching quote. Delivery claims for agency, resource change, local payoff, hook, and reader jobs never allow `not_applicable`.

## File Map

- Create `skills/novel-brainstorm-workshop/references/commercial-story-design.md`: distilled mechanisms, reader jobs, premise variables, conflict ladder, free-trial arc, and contract authoring rules.
- Create `skills/novel-brainstorm-workshop/references/originality-isolation.md`: source isolation, structural-distance rules, rejected imitation workflows, and review questions.
- Modify `skills/novel-brainstorm-workshop/SKILL.md`: remove default online research and route commercial fiction through the new references.
- Modify `skills/novel-brainstorm-workshop/references/prompt-contract.md`, `retention-opening.md`, `quality-gates.md`, and `genre-adapters.md`: define the new block and aligned agent responsibilities.
- Create `core/commercial_story.py`: strict commercial story contract values, canonical IDs, parsing, and activation rules.
- Create `core/story_originality.py`: abstract fingerprints, catalog loading, sibling-reference discovery, similarity decisions, and immutable reports.
- Create `core/data/commercial_pattern_catalog.v1.json`: abstract high-risk formula combinations only; no source prose, titles, names, or paths.
- Create `core/commercial_quality.py`: chapter-design sequence checks, artifact-bound reader-value reports, free-window review, and whole-book commercial review.
- Modify `core/prompt_intake.py`: parse and persist approved commercial contracts.
- Modify `core/contracts.py`: add backward-compatible schema-v2 chapter fields.
- Modify `core/orchestrator.py`: foundation binding, chapter contract v2, agent prompts, and bounded commercial repair entry point.
- Modify `core/story_foundation.py`: hydrate the approved contract and fingerprint into StoryState.
- Modify `core/state_manager.py` and `core/state_parser.py`: persist one structured reader-value update per chapter.
- Modify `core/canon.py`, `core/canon_ledger.py`, `core/canon_reconciliation.py`, and `core/state_codec.py` only where their explicit field allowlists require the new state value.
- Modify `core/pipeline_runner.py`: add originality, chapter design, commercial chapter, and whole-book checkpoints with correct input hashes and resume behavior.
- Create `tests/commercial_fixtures.py`: one canonical commercial story payload plus named variants and pipeline builders shared by the new tests.
- Modify `tests/test_skills.py`, `test_prompt_intake.py`, `test_contracts.py`, `test_pipeline_orchestrator.py`, `test_pipeline_runner.py`, `test_pipeline_integration.py`, `test_state_tracking_updates.py`, and relevant canon tests.
- Create `tests/test_commercial_story.py`, `tests/test_story_originality.py`, and `tests/test_commercial_quality.py`.

---

## P0: Story Design And Originality

### Task 1: Replace Online Research With The Distilled Local Reader Profile

**Files:**
- Create: `skills/novel-brainstorm-workshop/references/commercial-story-design.md`
- Create: `skills/novel-brainstorm-workshop/references/originality-isolation.md`
- Modify: `skills/novel-brainstorm-workshop/SKILL.md`
- Modify: `skills/novel-brainstorm-workshop/references/prompt-contract.md`
- Modify: `skills/novel-brainstorm-workshop/references/retention-opening.md`
- Modify: `skills/novel-brainstorm-workshop/references/quality-gates.md`
- Modify: `skills/novel-brainstorm-workshop/references/genre-adapters.md`
- Test: `tests/test_skills.py`

**Interfaces:**
- Produces one discoverable skill path that reads `commercial-story-design.md` for commercial/retention fiction and `originality-isolation.md` whenever samples or a corpus inform selection.
- Produces a prompt-level block named `[COMMERCIAL_STORY_JSON]...[/COMMERCIAL_STORY_JSON]`.
- Replaces `audience_research` with `audience_profile` for new prompts; legacy prompt content remains valid input text.

- [ ] **Step 1: Add failing skill-contract tests**

```python
def test_brainstorm_skill_uses_local_commercial_profile_without_default_web_research():
    skill = _read(BRAINSTORM / "SKILL.md")
    commercial = _read(BRAINSTORM / "references/commercial-story-design.md")
    originality = _read(BRAINSTORM / "references/originality-isolation.md")
    assert "commercial-story-design.md" in skill
    assert "originality-isolation.md" in skill
    assert "[COMMERCIAL_STORY_JSON]" in commercial
    assert "a5458ce1332e5b74c52889e4a5aed5b9809f69e6e1a8f09cde25c5ab974ee49f" in commercial
    assert "run online audience research" not in skill.casefold()
    assert "never retrieve the nearest matching story" in originality.casefold()
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `./venv/bin/pytest -q tests/test_skills.py`

Expected: FAIL because the two references and routing rules are absent.

- [ ] **Step 3: Write the two references and simplify the entrypoint**

The commercial reference must encode the nine premise variables, seven reader jobs from `READER_JOBS`, five conflict levels, three/four-chapter free arc, sustainable satisfaction formula, and these fixed quality budgets:

```json
{
  "consecutive_humiliation_scenes_max": 2,
  "identical_hook_type_max": 1,
  "unseeded_rescue_max": 0,
  "child_voice_age_check": true,
  "institutional_plausibility_check": true,
  "protagonist_causes_major_turn": true
}
```

The originality reference must require mechanism-only learning and explicitly exclude raw sample retrieval, title/name replacement, signature-object reuse, distinctive reversal chains, scene-order reuse, and sentence imitation. Replace Phase 0.5 in `SKILL.md` with audience confirmation plus application of the local reader profile. Remove source URLs, online queries, browser fallback, and `research_status` requirements from the default Prompt contract. `genre-adapters.md` must map all eight studied execution styles (domestic ethics, motherhood, professional erasure, departure/revenge, illness/grief, romance repair, suspense/crime, and speculative fantasy) to the shared contract fields without adding genre-specific schema keys.

- [ ] **Step 4: Validate the skill package and tests**

Run:

```bash
./venv/bin/pytest -q tests/test_skills.py
python /Users/max/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/novel-brainstorm-workshop
```

Expected: all tests pass and `quick_validate.py` exits 0.

- [ ] **Step 5: Commit**

```bash
git add skills/novel-brainstorm-workshop tests/test_skills.py
git commit -m "feat: distill commercial story workshop profile"
```

### Task 2: Add The Immutable Commercial Story Contract

**Files:**
- Create: `core/commercial_story.py`
- Create: `tests/commercial_fixtures.py`
- Create: `tests/test_commercial_story.py`
- Modify: `core/prompt_intake.py`
- Modify: `tests/test_prompt_intake.py`

**Interfaces:**
- Produces frozen values `ReaderContract`, `PremiseEngine`, `ConflictStep`, `FreeTrialArc`, `QualityBudgets`, and `CommercialStoryContract`. The block has exact root keys `schema_version`, `reader_contract`, `premise_engine`, `conflict_ladder`, `free_trial_arc`, and `quality_budgets`; `contract_id` is computed by intake and is not authored inside the Prompt block.
- Produces `parse_commercial_story_block(prompt: str) -> CommercialStoryContract | None` and `commercial_story_block(contract: CommercialStoryContract) -> str`.
- `CommercialStoryContract.contract_id` is `commercial-story:<sha256 canonical JSON>`.
- Prompt Intake adds `commercial_story_contract` and `commercial_story_contract_id` to `brief.json` only when the block exists.
- `tests/commercial_fixtures.py` produces `commercial_story_payload()`, `commercial_story_fixture()`, and `commercial_story_fixture_variant()`. The canonical fixture uses `career_midpoint`, `professional_ghostwork`, `professional_identity`, boundary transfer `name`, beneficiary `colleague`, proof `technical_metadata`, deadline `public_event`, agency source `process_knowledge`, action cost `career_access`, relationship shape `spouse_triangle`, belonging anchors `self/child/work`, and free-arc actions `verify/test_boundary/withdraw/accept_cost`. The variant changes at least six controlled dimensions.

- [ ] **Step 1: Write strict parsing and invariant tests**

```python
def test_commercial_story_contract_round_trips_with_stable_identity():
    contract = commercial_story_fixture()
    restored = CommercialStoryContract.from_dict(json.loads(json.dumps(contract.to_dict())))
    assert restored == contract
    assert restored.contract_id == contract.contract_id
    assert restored.contract_id.startswith("commercial-story:")

def test_contract_requires_five_conflict_levels_and_two_belonging_anchors():
    payload = commercial_story_payload()
    payload["conflict_ladder"] = payload["conflict_ladder"][:4]
    with pytest.raises(ValueError, match="five conflict levels"):
        CommercialStoryContract.from_dict(payload)
    payload = commercial_story_payload()
    payload["premise_engine"]["belonging_anchors"] = ["self"]
    with pytest.raises(ValueError, match="belonging_anchors"):
        CommercialStoryContract.from_dict(payload)

def test_prompt_intake_persists_the_approved_contract(tmp_path):
    prompt = "# Built From Her Records\n\n" + commercial_story_block(commercial_story_fixture())
    result = ingest_prompt(tmp_path / "project", "-", stdin_text=prompt)
    assert result.brief["commercial_story_contract_id"].startswith("commercial-story:")
    assert result.brief["commercial_story_contract"]["schema_version"] == 1
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_commercial_story.py tests/test_prompt_intake.py`

Expected: collection fails because `commercial_story` and its parser do not exist.

- [ ] **Step 3: Implement exact enums and contract validation**

Use only the values in **Shared Controlled Vocabulary**. `ReaderContract` contains exact fields `audience_age_band`, `life_contexts`, and `emotional_jobs`; age band is a nonblank user-confirmed string, and `life_contexts` is one through five unique nonblank strings. `PremiseEngine` contains `protagonist_life_stage`, `protagonist_desire_beyond_escape`, `invisible_labor`, `sacred_asset`, `boundary_transfer`, `beneficiary_role`, `proof_type`, `deadline_type`, `agency_source`, `agency_seeded_in_chapter`, `action_cost`, `belonging_anchors`, and `relationship_shape`. Each `ConflictStep` contains exact fields `level`, `resource_dimension`, `protagonist_action`, and `observable_consequence`. `FreeTrialArc` contains exact fields `chapter_count`, `recognition_event`, `pattern_proof`, `first_boundary_test`, `local_payoff`, `irreversible_choice`, `visible_cost`, `next_concrete_expectation`, and `action_sequence`. `QualityBudgets` contains only the six keys and exact values from Task 1. Require conflict levels exactly 1 through 5, free-trial chapter count 3 or 4, agency seeded by chapter 2, two or more unique belonging anchors, three or more ordered free-arc actions, and the exact quality budget values in Task 1.

Parsing accepts zero or one block and rejects duplicate blocks, duplicate JSON keys at every nesting level, unknown fields, any authored `contract_id`, and noncanonical enum values. It never reads the corpus directory.

- [ ] **Step 4: Run focused tests and intake regression**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_commercial_story.py tests/test_prompt_intake.py tests/test_create_genres_premise.py`

Expected: all tests pass; prompts without the block retain byte-compatible brief behavior except for absent optional fields.

- [ ] **Step 5: Commit**

```bash
git add core/commercial_story.py core/prompt_intake.py tests/commercial_fixtures.py tests/test_commercial_story.py tests/test_prompt_intake.py
git commit -m "feat: persist approved commercial story contracts"
```

### Task 3: Bind The Approved Contract Into Architect Foundation Canon

**Files:**
- Modify: `core/orchestrator.py`
- Modify: `core/story_foundation.py`
- Modify: `tests/commercial_fixtures.py`
- Modify: `tests/test_pipeline_orchestrator.py`
- Modify: `tests/test_pipeline_runner.py`
- Modify: `tests/test_foundation_canon.py`

**Interfaces:**
- Activated Architect foundations include `commercial_story_contract` with the same canonical dictionary and ID stored by intake.
- `NovelOrchestrator._parse_story_foundation(text, num_chapters, approved_commercial_story=None)` rejects omission or mutation only when an approved contract exists.
- `apply_story_foundation()` stores the contract under `state.story_bible["commercial_story_contract"]` and its ID under `state.metadata["commercial_story_contract_id"]`.
- Adds `architect_foundation_text(commercial_story_contract: Mapping[str, Any], chapter_count: int = 3) -> str` to the shared test fixtures; it emits a complete valid tagged foundation with three numbered chapters, one protagonist, one main plot thread, style, and enforced ending contract.

- [ ] **Step 1: Add failing echo, mutation, and hydration tests**

```python
def test_architect_foundation_must_echo_approved_commercial_contract():
    approved = commercial_story_fixture()
    text = architect_foundation_text(commercial_story_contract=approved.to_dict())
    parsed = NovelOrchestrator._parse_story_foundation(text, 3, approved)
    assert parsed["commercial_story_contract"] == approved.to_dict()
    assert parsed["commercial_story_contract_id"] == approved.contract_id
    mutated = json.loads(json.dumps(approved.to_dict()))
    mutated["premise_engine"]["sacred_asset"] = "home"
    with pytest.raises(ValueError, match="approved commercial story contract"):
        NovelOrchestrator._parse_story_foundation(
            architect_foundation_text(commercial_story_contract=mutated), 3, approved
        )
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_pipeline_orchestrator.py tests/test_foundation_canon.py -k commercial`

Expected: FAIL because the parser does not bind an approved contract.

- [ ] **Step 3: Extend the Architect contract without duplicating skill prose**

Load the approved contract from `outputs/input/brief.json`, include its canonical JSON once in the Architect prompt, require an exact semantic echo inside `STORY_FOUNDATION_JSON`, and validate with `CommercialStoryContract.from_dict()`. Do not inject `commercial-story-design.md` or corpus data into the Architect prompt; the approved contract is the complete runtime input.

- [ ] **Step 4: Verify foundation replay and canon hashes**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_pipeline_orchestrator.py tests/test_pipeline_runner.py tests/test_foundation_canon.py tests/test_canon_hash.py`

Expected: all tests pass, including idempotent foundation replay and prompts without a commercial contract.

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py core/story_foundation.py tests/commercial_fixtures.py tests/test_pipeline_orchestrator.py tests/test_pipeline_runner.py tests/test_foundation_canon.py
git commit -m "feat: bind commercial design to story foundation"
```

### Task 4: Add Structural Originality Fingerprints And A Pipeline Gate

**Files:**
- Create: `core/data/commercial_pattern_catalog.v1.json`
- Create: `core/story_originality.py`
- Modify: `tests/commercial_fixtures.py`
- Create: `tests/test_story_originality.py`
- Modify: `core/pipeline_runner.py`
- Modify: `tests/test_pipeline_runner.py`
- Modify: `tests/test_pipeline_integration.py`

**Interfaces:**
- Produces `StoryFingerprint.from_contract(contract)`, `OriginalityReferenceSet`, `OriginalityFinding`, and `OriginalityReport`.
- Produces `evaluate_story_originality(project: Path, contract: CommercialStoryContract) -> OriginalityReport`.
- Writes `outputs/input/story-fingerprint.json` and `outputs/quality/story-originality-report.json` atomically.
- Adds stage `foundation.originality` after `outline` and before `foundation.commit` for activated contracts.
- Adds `high_overlap_candidate()` and `high_overlap_reference()` fixture builders. They share eight weighted dimensions and three of four ordered free-arc actions while differing in names and descriptive prose.

- [ ] **Step 1: Add failing similarity tests**

```python
def test_exact_structural_signature_blocks_without_storing_source_prose(tmp_path):
    candidate = StoryFingerprint.from_contract(commercial_story_fixture())
    report = compare_fingerprints(candidate, [candidate])
    assert report.status == "blocked"
    assert report.findings[0].code == "duplicate_story_signature"
    assert "dialogue" not in json.dumps(report.to_dict()).casefold()

def test_shared_genre_with_different_mechanism_passes():
    first = StoryFingerprint.from_contract(commercial_story_fixture())
    second = StoryFingerprint.from_contract(commercial_story_fixture_variant())
    report = compare_fingerprints(first, [second])
    assert report.status == "pass"

def test_high_dimension_and_action_sequence_overlap_blocks():
    report = compare_fingerprints(high_overlap_candidate(), [high_overlap_reference()])
    assert report.dimension_similarity >= 0.84
    assert report.action_sequence_similarity >= 0.75
    assert report.status == "blocked"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_story_originality.py`

Expected: collection fails because fingerprint and report types are absent.

- [ ] **Step 3: Implement the abstract catalog and comparison**

The catalog contains only `pattern_id`, controlled category values, ordered free-arc action codes, source count, and risk note. Seed it with the three high-similarity formula clusters documented in the study: password/network clue chains, ashes/fire relationship chains, and pregnancy-medical-result departure chains. Do not store titles, directory names, raw paths, quotes, summaries, or unique object descriptions.

Use this fixed weighted comparison: life stage `.08`, invisible labor `.10`, sacred asset `.12`, boundary transfer `.12`, beneficiary role `.06`, proof type `.11`, deadline type `.07`, agency source `.10`, action cost `.08`, belonging-anchor Jaccard similarity `.08`, and relationship shape `.08`. Exact category equality contributes its full weight; inequality contributes zero. Block an exact signature. Block when weighted dimension similarity is at least `0.84` and longest-common-subsequence action similarity is at least `0.75`. Warn at dimension similarity `0.72` without the sequence threshold. Compare against the catalog and valid sibling fingerprints under the same projects root, excluding the current project instance and any fingerprint whose stored contract ID/hash fails validation. Capture the sorted reference IDs and hashes once at stage start and bind them into `reference_set_sha256`.

- [ ] **Step 4: Integrate resume-safe stage behavior**

`foundation.originality` input hashes include foundation SHA, catalog SHA, and sibling reference-set SHA. A changed foundation, catalog, or sibling reference invalidates the stage and all downstream checkpoints. A blocked report prevents `foundation.commit`; a warning is durable but non-blocking.

- [ ] **Step 5: Run originality and pipeline tests**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_story_originality.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py`

Expected: activated projects create both artifacts; legacy projects keep the old stage order; stale fingerprints rerun deterministically.

- [ ] **Step 6: Commit**

```bash
git add core/data/commercial_pattern_catalog.v1.json core/story_originality.py core/pipeline_runner.py tests/commercial_fixtures.py tests/test_story_originality.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py
git commit -m "feat: gate structurally duplicate story designs"
```

---

## P1: Chapter Contracts And Reader-Value Progression

### Task 5: Evolve ChapterContract To Schema Version 2

**Files:**
- Modify: `core/contracts.py`
- Modify: `tests/commercial_fixtures.py`
- Modify: `tests/test_contracts.py`
- Modify: `core/orchestrator.py`
- Modify: `tests/test_pipeline_orchestrator.py`

**Interfaces:**
- Schema-v2 `ChapterContract` adds `reader_jobs`, `belonging_anchors`, `resource_dimension`, `resource_change`, `satisfaction_type`, `hook_type`, `humiliation_scene`, `protagonist_causes_turn`, `seeded_resource_ids`, and `used_resource_ids`.
- Schema-v1 values still deserialize with neutral defaults; activated commercial stories under `evidence_v1` require schema v2.
- Controlled values are exactly `READER_JOBS`, `RESOURCE_DIMENSIONS`, `SATISFACTION_TYPES`, and `HOOK_TYPES` from the shared vocabulary.
- Adds shared test builders `legacy_chapter_contract_payload()`, `chapter_contract_v2_payload()`, and `chapter_contract_v2(**overrides)`. The v2 baseline is chapter 1, reader jobs `recognition/anger`, no belonging anchor delivery yet, resource dimension `name`, satisfaction `boundary`, hook `consequence`, protagonist-caused turn true, resource seed/use `license_record`, and humiliation false.

- [ ] **Step 1: Add failing v1 compatibility and v2 strictness tests**

```python
def test_chapter_contract_v1_remains_readable_with_neutral_commercial_fields():
    restored = ChapterContract.from_dict(legacy_chapter_contract_payload())
    assert restored.schema_version == 1
    assert restored.reader_jobs == ()
    assert restored.belonging_anchors == ()
    assert restored.resource_dimension == ""
    assert restored.seeded_resource_ids == ()

def test_chapter_contract_v2_requires_reader_value_fields():
    payload = chapter_contract_v2_payload()
    payload["hook_type"] = "phone_interrupt"
    with pytest.raises(ValueError, match="hook_type"):
        ChapterContract.from_dict(payload)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_contracts.py`

Expected: FAIL because schema v2 and new fields are unsupported.

- [ ] **Step 3: Implement backward-compatible contract parsing**

Keep `AuthorIntent` and `StoryContract` at schema 1. Let only `ChapterContract` accept schemas 1 and 2. Include every v2 field in canonical identity. Require one through three unique `reader_jobs` in declared order, zero through two unique `belonging_anchors`, `protagonist_causes_turn` to be a real boolean, and every resource ID to match `[a-z][a-z0-9_]{2,63}`.

- [ ] **Step 4: Update the Architect chapter prompt**

When an activated contract is present, require schema-v2 JSON and explain each field using the current book's approved premise and conflict ladder. Do not include raw corpus examples. For unactivated stories, retain the current schema-v1 prompt.

- [ ] **Step 5: Run contract and orchestration tests**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_contracts.py tests/test_pipeline_orchestrator.py tests/test_artifact_revisions.py`

Expected: all tests pass and v1 artifact IDs remain stable.

- [ ] **Step 6: Commit**

```bash
git add core/contracts.py core/orchestrator.py tests/commercial_fixtures.py tests/test_contracts.py tests/test_pipeline_orchestrator.py
git commit -m "feat: add reader value chapter contracts"
```

### Task 6: Add The Deterministic Chapter Design Gate

**Files:**
- Create: `core/commercial_quality.py`
- Create: `tests/test_commercial_quality.py`
- Modify: `core/pipeline_runner.py`
- Modify: `tests/test_pipeline_runner.py`
- Modify: `tests/test_pipeline_integration.py`

**Interfaces:**
- Produces `CommercialFinding`, `ChapterDesignReport`, and `validate_chapter_design(contract, prior_contracts, story_contract) -> ChapterDesignReport`.
- Adds `chapter.design_check` immediately after `chapter.plan` and before `chapter.write`.
- Writes `outputs/quality/commercial/chapter_NNN_design.json` bound to story and chapter contract revision IDs.

- [ ] **Step 1: Add failing budget tests**

```python
def test_design_gate_blocks_unseeded_resource_and_passive_turn():
    contract = chapter_contract_v2(used_resource_ids=("license_record",), protagonist_causes_turn=False)
    report = validate_chapter_design(contract, (), commercial_story_fixture())
    assert {item.code for item in report.blockers} == {
        "unseeded_resource",
        "protagonist_does_not_cause_turn",
    }

def test_design_gate_blocks_repeated_hook_and_third_humiliation_scene():
    prior = (
        chapter_contract_v2(chapter=1, hook_type="arrival", humiliation_scene=True),
        chapter_contract_v2(chapter=2, hook_type="arrival", humiliation_scene=True),
    )
    current = chapter_contract_v2(chapter=3, hook_type="arrival", humiliation_scene=True)
    report = validate_chapter_design(current, prior, commercial_story_fixture())
    assert "identical_hook_budget" in {item.code for item in report.blockers}
    assert "humiliation_budget" in {item.code for item in report.blockers}
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_commercial_quality.py`

Expected: collection fails because the evaluator is absent.

- [ ] **Step 3: Implement sequence-aware validation**

Read prior contracts from ArtifactStore heads, never from Markdown parsing. Check resource seeding, protagonist causality, humiliation streak, consecutive hook repetition, reader-job validity, belonging anchors, and free-trial chapter roles. Every chapter anchor must belong to the approved story anchors; `belonging` requires at least one chapter anchor, and a declared chapter anchor requires the `belonging` reader job. For a three-chapter free arc, require `recognition` in chapter 1, `pattern_proof` plus `first_boundary_test` in chapter 2, and `local_payoff`, `irreversible_choice`, `visible_cost`, plus `next_concrete_expectation` in chapter 3. For a four-chapter free arc, assign those groups to chapters 1, 2, 3 (`first_boundary_test` plus `local_payoff`), and 4 (`irreversible_choice`, `visible_cost`, `next_concrete_expectation`). Intermediate free chapters include at least one of `anger`, `pity`, or `regret`; the last includes `agency` or `hope`. A resource may be seeded and used in the same chapter, but prior chapters plus the current chapter's `seeded_resource_ids` must contain every current `used_resource_id`.

- [ ] **Step 4: Integrate the stage and invalidation graph**

Bind the report to current story/chapter contract revisions and the ordered prior contract IDs. A blocker marks `chapter.design_check` blocked before a Scribe call. Retrying `chapter.plan` with a changed contract invalidates the report and later chapter stages.

- [ ] **Step 5: Run focused and pipeline tests**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_commercial_quality.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py`

Expected: all tests pass; a rejected plan causes zero Scribe calls.

- [ ] **Step 6: Commit**

```bash
git add core/commercial_quality.py core/pipeline_runner.py tests/test_commercial_quality.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py
git commit -m "feat: enforce commercial chapter design budgets"
```

### Task 7: Prepare Canon For Verified Reader-Value Outcomes

**Files:**
- Modify: `core/state_manager.py`
- Modify: `core/state_parser.py`
- Modify: `core/canon.py`
- Modify: `core/canon_ledger.py`
- Modify: `core/canon_reconciliation.py`
- Modify: `core/state_codec.py`
- Modify: `tests/commercial_fixtures.py`
- Modify: `tests/test_state_tracking_updates.py`
- Modify: `tests/test_canon_proposals.py`
- Modify: `tests/test_canon_hash.py`

**Interfaces:**
- `ChapterState.reader_value_updates` is a list of strict dictionaries applied only from `continuity_guardian` proposals created by the passed `chapter.commercial_check` lifecycle in Task 9.
- Adds `verified_reader_value_update(**overrides)` to `tests/commercial_fixtures.py`; it returns the exact nine-field shape below with stable report/SHA values.
- Each stored value has this exact shape; evidence spans remain in the immutable commercial report rather than being copied into mutable state:

```json
{
  "report_id": "commercial-chapter-report:<sha256>",
  "candidate_sha256": "<64 lowercase hex>",
  "reader_jobs": ["recognition", "anger"],
  "belonging_anchors": [],
  "resource_dimension": "name",
  "resource_change": "Megan withdraws permit approval",
  "satisfaction_type": "boundary",
  "hook_type": "consequence",
  "protagonist_caused_turn": true
}
```

- [ ] **Step 1: Add failing parser, replay, and canonical-hash tests**

```python
def test_verified_reader_value_update_is_replayed_as_canon(tmp_path):
    source_sha = "a" * 64
    update = verified_reader_value_update()
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="continuity_guardian",
        source_artifact_sha=source_sha,
        delta={"reader_value_updates": [update]},
    )
    apply_canon_proposal(state, proposal, source_sha)
    update = state.chapters[1].reader_value_updates[0]
    assert update["resource_dimension"] == "name"
    assert update["satisfaction_type"] == "boundary"
    assert update["hook_type"] == "consequence"

def test_scribe_cannot_self_certify_reader_value(tmp_path):
    proposal = CanonDeltaProposal(
        chapter=1,
        agent_name="scribe",
        source_artifact_sha="b" * 64,
        delta={"reader_value_updates": [verified_reader_value_update()]},
    )
    with pytest.raises(ValueError, match="continuity_guardian"):
        apply_canon_proposal(state, proposal, "b" * 64)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_state_tracking_updates.py tests/test_canon_proposals.py tests/test_canon_hash.py`

Expected: FAIL because the state field and source-restricted apply branch are absent.

- [ ] **Step 3: Implement strict parsing and idempotent apply**

Require the exact nine fields shown above, a valid content ID/SHA, one through three unique reader jobs, zero through two unique belonging anchors, controlled category values, nonblank `resource_change`, and a real boolean. `apply_to_state()` rejects this delta unless `source == "continuity_guardian"`. Deduplicate by `(report_id, candidate_sha256)` and reject the same report ID bound to different content. Preserve append-only proposal behavior and deterministic canonical hashing. Do not teach Scribe, Editor, or Style Curator to emit `Reader_Value_Updates`; Task 9 constructs the trusted proposal only after evidence verification.

- [ ] **Step 4: Run state and canon suites**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_state_tracking_updates.py tests/test_canon_proposals.py tests/test_canon_hash.py tests/test_canon_reconciliation.py tests/test_foundation_canon.py`

Expected: all tests pass and replay remains idempotent.

- [ ] **Step 5: Commit**

```bash
git add core/state_manager.py core/state_parser.py core/canon.py core/canon_ledger.py core/canon_reconciliation.py core/state_codec.py tests/commercial_fixtures.py tests/test_state_tracking_updates.py tests/test_canon_proposals.py tests/test_canon_hash.py
git commit -m "feat: prepare verified reader value canon"
```

---

## P2: Manuscript Evidence, Repair, And Whole-Book Review

### Task 8: Update Agent Prompts Without Creating One Shared Template Voice

**Files:**
- Modify: `core/orchestrator.py`
- Modify: `tests/test_pipeline_orchestrator.py`
- Modify: `skills/novel-brainstorm-workshop/references/prompt-contract.md`
- Modify: `skills/novel-brainstorm-workshop/references/quality-gates.md`

**Interfaces:**
- Scribe receives only the approved story contract, current chapter contract, recent *verified and promoted* reader-value summaries, and current context pack; it never receives the corpus reference and never self-certifies delivery.
- Editor checks repeated humiliation, passive turns, unsupported rescue, and repeated hook mechanics.
- Continuity Guardian checks evidence provenance, child knowledge/voice, institutional plausibility, and contract-to-prose delivery.
- Style Curator checks character-specific attention, work knowledge, speech strategy, shame trigger, body response, and template phrase repetition.

- [ ] **Step 1: Add failing prompt-routing tests**

```python
def test_scribe_prompt_contains_contract_but_no_corpus_material(tmp_path):
    project, orchestrator = _prepared_commercial_orchestrator(tmp_path)
    orchestrator.write_chapter(1, dry_run=True)
    prompt = (project / "outputs/chapter_001_scribe_prompt.md").read_text()
    assert "resource_dimension" in prompt
    assert "recent verified reader-value outcomes" in prompt.casefold()
    assert "Reader_Value_Updates" not in prompt
    assert "a5458ce1332e5b74c52889e4a5aed5b9809f69e6e1a8f09cde25c5ab974ee49f" not in prompt
    assert "/Users/max/workspace/批次-" not in prompt
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_pipeline_orchestrator.py -k reader_value`

Expected: FAIL because the production Scribe prompt does not receive the approved commercial design and promoted outcome context.

- [ ] **Step 3: Add role-specific guidance**

Translate structural labels into observable scene requirements. Prohibit agents from writing labels such as “this is the evidence payoff” in prose. Add the six overused patterns from the study to a repetition review rather than a blanket word ban: “I did not cry,” “I did not scream,” “my blood ran cold,” “my world shattered,” “they thought I was weak,” and “the game had just begun.” One contextually earned use is reviewable; repeated use across recent chapters is a repair finding.

- [ ] **Step 4: Run orchestrator and skill tests**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_pipeline_orchestrator.py tests/test_skills.py tests/test_prose_sanitize.py`

Expected: all tests pass and clean reader prose still excludes state blocks.

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py skills/novel-brainstorm-workshop/references/prompt-contract.md skills/novel-brainstorm-workshop/references/quality-gates.md tests/test_pipeline_orchestrator.py
git commit -m "feat: specialize agents for reader value delivery"
```

### Task 9: Add Artifact-Bound Commercial Chapter Review And Bounded Repair

**Files:**
- Modify: `core/commercial_quality.py`
- Modify: `core/orchestrator.py`
- Modify: `core/pipeline_runner.py`
- Modify: `tests/commercial_fixtures.py`
- Modify: `tests/test_commercial_quality.py`
- Modify: `tests/test_pipeline_runner.py`
- Modify: `tests/test_pipeline_integration.py`

**Interfaces:**
- Produces `CommercialChapterReport` bound to candidate-final SHA, story contract revision, and chapter contract revision.
- Produces `review_commercial_chapter(candidate_text, chapter_contract, guardian_payload, recent_updates) -> CommercialChapterReport`.
- Adds `chapter.commercial_check` after `chapter.style` and before `chapter.promote`.
- Produces `NovelOrchestrator.repair_commercial_chapter(chapter_number, report, attempt, dry_run=False)` for at most `RunSpec.max_quality_repairs` attempts.
- Writes the current report to `outputs/quality/commercial/chapter_NNN_report.json` and preserves every raw Guardian response and superseded report under `outputs/runs/{run_id}/feedback/commercial/chapter_NNN/`.
- A passed report produces one source-restricted `continuity_guardian` canon proposal containing the verified summary from Task 7. The proposal is bound to the report artifact SHA and joins the evidence proposal chain only during promotion.
- Adds `_is_commercial_repair_phase()`, `_commercial_repair_number()`, and `_latest_candidate_stage()` to `pipeline_runner.py`. `chapter.commercial.repair.N` is a Style Curator candidate producer with its own immutable `final` revision; promotion uses the latest successful commercial repair revision, or `chapter.style` when no commercial repair ran.
- Test fixtures add `span(text, quote)`, `guardian_reader_value_payload(**overrides)`, and `run_with_commercial_guardian_failures(failure_count: int)`. `span()` returns exact character offsets plus quote. The run helper builds a one-chapter `evidence_v1` run with an activated commercial contract and a fake Guardian whose first `failure_count` responses cite an exact blocking passage.

- [ ] **Step 1: Add failing evidence and repair tests**

```python
def test_commercial_report_requires_exact_candidate_quotes():
    text = "Megan signed the withdrawal. The lender froze the draw."
    payload = guardian_reader_value_payload(
        agency_span=span(text, "Megan signed the withdrawal."),
        payoff_span=span(text, "The lender froze the draw."),
        hook_span={
            "quote": "A quote not present in the candidate.",
            "start": 0,
            "end": 37,
        },
    )
    report = review_commercial_chapter(text, chapter_contract_v2(), payload, ())
    assert report.status == "blocked"
    assert "invalid_hook_evidence" in {item.code for item in report.blockers}

def test_pipeline_repairs_commercial_candidate_at_most_twice():
    manifest = run_with_commercial_guardian_failures(2)
    assert manifest.get("chapter.commercial.repair.1", 1).status == "done"
    assert manifest.get("chapter.commercial.repair.2", 1).status == "done"
    assert manifest.get("chapter.promote", 1).status == "done"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_commercial_quality.py tests/test_pipeline_runner.py -k commercial`

Expected: FAIL because manuscript review and repair stages are absent.

- [ ] **Step 3: Implement Guardian review with strict JSON**

Reuse the Continuity Guardian client through `orchestrator.llm_for("continuity_guardian")`; do not add an agent role. Require strict JSON with exact fields `agency`, `resource_change`, `local_payoff`, `ending_hook`, `reader_jobs`, `belonging_anchors`, `free_trial_beats`, `child_voice`, `institutional_plausibility`, and `findings`. Delivery entries contain `status`, `quote`, `start`, and `end`; `reader_jobs` and `belonging_anchors` contain one entry for every value declared by the chapter contract. `free_trial_beats` contains exactly the beats assigned to this chapter by Task 6, or an empty list after the free window. Applicability checks follow the shared tri-state rule. Verify character offsets and quotes against the exact candidate and convert valid critical/major findings to existing `EvidenceSpan`/`QualityFinding` values. A required delivery with absent or invalid evidence is itself a verified blocker; an evaluator-authored prose finding with invalid evidence is advisory and cannot block.

- [ ] **Step 4: Implement bounded repair lifecycle**

On blocking findings, pass only finding codes, exact evidence, unchanged story/chapter contracts, and current candidate to Style Curator. Sanitize the returned full candidate, persist a child `final` ArtifactStore revision, recompute SHA, rerun Guardian review, and stop after the configured commercial repair count. Extend stage-agent metadata, proposal-chain ordering, input-hash calculation, resume validation, and human-review rebinding for `chapter.commercial.repair.N`. The passed commercial report ID and SHA are included in `PromotionRequest.decision_metadata`, so the existing promotion receipt binds the commercial gate without changing promotion schema. A failed final attempt leaves the run blocked before promotion and preserves every attempt artifact.

- [ ] **Step 5: Run commercial, evidence, and pipeline tests**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_commercial_quality.py tests/test_quality_evidence.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py`

Expected: all tests pass; no candidate is promoted with a verified blocker.

- [ ] **Step 6: Commit**

```bash
git add core/commercial_quality.py core/orchestrator.py core/pipeline_runner.py tests/commercial_fixtures.py tests/test_commercial_quality.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py
git commit -m "feat: verify and repair commercial chapter delivery"
```

### Task 10: Add Free-Window And Whole-Book Commercial Review Gates

**Files:**
- Modify: `core/commercial_quality.py`
- Modify: `core/pipeline_runner.py`
- Modify: `tests/commercial_fixtures.py`
- Modify: `tests/test_commercial_quality.py`
- Modify: `tests/test_pipeline_runner.py`
- Modify: `tests/test_pipeline_integration.py`

**Interfaces:**
- Produces `CommercialFreeTrialReport`, `CommercialBookReport`, `evaluate_commercial_free_trial(project: Path) -> CommercialFreeTrialReport`, and `evaluate_commercial_book(project: Path, chapter_count: int) -> CommercialBookReport`.
- Adds `commercial.free_trial_review` immediately after promotion of the configured free-trial chapter 3 or 4 and before any later chapter planning. It writes `outputs/quality/commercial-free-trial-report.json`.
- Adds `commercial.book_review` after all chapter promotions and before `ending.preflight`/`book.check`.
- Writes `outputs/quality/commercial-book-report.json` bound to approved story contract, all final artifact revisions, all chapter contracts, and all chapter reports.
- Test fixtures add `commercial_project_with_reports(tmp_path, *, missing_chapter_3_payoff=False, delivered_belonging=("self", "work"))`; it writes a valid story contract with approved anchors `self/work/community`, twelve exact final artifacts, contracts, promotion bindings, and candidate-bound chapter reports without calling an LLM. `run_with_free_trial_failure(tmp_path)` builds a twelve-chapter fake pipeline whose three-chapter contract has one missing local payoff; it records calls so the test can prove chapter 4 planning never starts.

- [ ] **Step 1: Add failing free-window and whole-book tests**

```python
def test_free_trial_review_requires_complete_micro_arc(tmp_path):
    project = commercial_project_with_reports(tmp_path, missing_chapter_3_payoff=True)
    report = evaluate_commercial_free_trial(project)
    assert report.status == "blocked"
    assert "free_trial_local_payoff_missing" in {item.code for item in report.blockers}

def test_book_review_requires_two_belonging_anchor_payoffs(tmp_path):
    project = commercial_project_with_reports(tmp_path, delivered_belonging=("self",))
    report = evaluate_commercial_book(project, chapter_count=12)
    assert "belonging_payoff_incomplete" in {item.code for item in report.blockers}

def test_pipeline_stops_before_chapter_four_when_free_window_blocks(tmp_path):
    manifest, calls = run_with_free_trial_failure(tmp_path)
    assert manifest.get("commercial.free_trial_review").status == "blocked"
    assert ("chapter.plan", 4) not in calls
    assert manifest.get("commercial.book_review") is None
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_commercial_quality.py tests/test_pipeline_runner.py -k "book_review or free_trial or free_window"`

Expected: FAIL because the free-window and whole-book reports are absent.

- [ ] **Step 3: Implement cross-chapter gates**

The free-trial gate requires verified delivery of recognition, pattern proof, a boundary test, local payoff, irreversible choice, visible cost, and next concrete expectation across exactly the configured first three or four promoted chapters. It reads immutable chapter contracts and passed commercial reports, not manuscript heuristics or agent self-reports. The whole-book gate requires at least two approved belonging anchors to receive observable payoff by the end. Both reports expose satisfaction-type distribution, reader-job distribution, hook distribution, resource-dimension distribution, maximum humiliation streak, unseeded rescue count, and protagonist-caused-turn count as facts rather than a composite score.

- [ ] **Step 4: Integrate checkpoint reuse and downstream invalidation**

`commercial.free_trial_review` input hashes include the story contract plus every final revision, chapter contract, promotion receipt, and commercial report in the configured free window. Its blocker prevents generation spend on later chapters. `commercial.book_review` binds the same artifact classes for the full book plus the free-trial report. Any changed promotion invalidates the applicable review and all downstream stages. Legacy projects skip both stages.

- [ ] **Step 5: Run full commercial lifecycle tests**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_commercial_quality.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py tests/test_ending_quality.py`

Expected: all tests pass; chapter planning after the free window is never reached when its review blocks, and compile is never reached after a blocked whole-book report.

- [ ] **Step 6: Commit**

```bash
git add core/commercial_quality.py core/pipeline_runner.py tests/commercial_fixtures.py tests/test_commercial_quality.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py
git commit -m "feat: gate free trial and whole book reader value"
```

---

## Release Verification

### Task 11: Validate Skills, Regression Behavior, And A Fresh Fiction Fixture

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/WORKFLOWS.md`
- Modify: `tests/test_skills.py`
- Modify: `tests/test_pipeline_integration.py`

**Interfaces:**
- Documents activation, stage order, artifact ownership, resume behavior, and originality limits.
- Adds one synthetic 4-chapter domestic/professional fixture whose premise dimensions do not match `Built on My Name`, `The Empty Chair Beside Her`, or a catalog blocker.

- [ ] **Step 1: Add the end-to-end fixture before implementation wiring is declared complete**

The fixture uses a fifty-two-year-old community choir treasurer protecting a deceased member's scholarship records from a board chair who redirects credit and funds. Its proof is meeting minutes plus deposit timestamps; its first free arc ends when she freezes her own authorization and risks losing the community that defined her. This fixture exercises midlife belonging, name/money resources, evidence and boundary satisfaction, non-romantic belonging, and institutional plausibility without reusing the corpus's distinctive scene chains.

- [ ] **Step 2: Run the focused end-to-end test**

Run: `PYTHONPATH=core ./venv/bin/pytest -q tests/test_pipeline_integration.py -k commercial_story_lifecycle`

Expected: the run reaches compile, records every new checkpoint, and produces passing originality, chapter, and book reports.

- [ ] **Step 3: Run all Novel OS tests**

Run: `PYTHONPATH=core ./venv/bin/pytest -q`

Expected: zero failures.

- [ ] **Step 4: Validate skill structure and repository hygiene**

Run:

```bash
python /Users/max/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/novel-brainstorm-workshop
git diff --check
git status --short
```

Expected: skill validation and whitespace checks exit 0; status contains only planned source, test, reference, and documentation changes plus pre-existing unrelated files.

- [ ] **Step 5: Perform one dry-run prompt acceptance check**

Generate a fresh Prompt through the revised workshop, ingest it into a temporary project, parse `COMMERCIAL_STORY_JSON`, and verify that no source title, corpus path, source prose, URL ledger, online query list, advertising metric, or conversion claim appears in the Prompt or Architect prompt.

- [ ] **Step 6: Commit documentation and fixture**

```bash
git add docs/ARCHITECTURE.md docs/WORKFLOWS.md tests/test_skills.py tests/test_pipeline_integration.py
git commit -m "docs: document commercial story quality lifecycle"
```

## Research Coverage Map

| Study sections | Enforced by |
| --- | --- |
| 1-3: core mechanism, evidence limits, corpus profile | Tasks 1, 2, and 4 |
| 4: eight execution styles | Task 1 genre adapters |
| 5-6: reader jobs and premise formula | Tasks 1 and 2 |
| 7-10: conflict, anger, belonging, satisfaction | Tasks 2, 5, 6, 9, and 10 |
| 11: 120-180 word spoiler-light opening promise | Task 1 retention-opening and quality-gate references; existing publication-copy lifecycle remains unchanged |
| 12-13: free micro-arc and hook diversity | Tasks 2, 5, 6, and 10 |
| 14-15: voice quality and failure modes | Tasks 6, 8, and 9 |
| 16: proposed data contracts | Tasks 2, 5, and 7 |
| 17: agent ownership | Tasks 3, 8, and 9 |
| 18-19: priorities and design principles | P0/P1/P2 order plus Release Verification |

## Acceptance Checklist

- [ ] A new commercial workshop completes without any online search step.
- [ ] The approved target reader and emotional jobs are explicit.
- [ ] The Prompt contains exactly one valid `COMMERCIAL_STORY_JSON` block.
- [ ] Raw corpus material is absent from every runtime prompt and artifact.
- [ ] Architect cannot silently change the approved commercial design.
- [ ] Exact or high-confidence structural duplicates block before foundation commit.
- [ ] Genre-level similarity alone does not block an original mechanism.
- [ ] Chapter plans cannot exceed humiliation, hook-repeat, passive-turn, or unseeded-resource budgets.
- [ ] Actual chapter value is bound to exact candidate-final evidence.
- [ ] A failed commercial repair never promotes a chapter.
- [ ] The free reading window delivers a complete micro-arc before opening the larger problem.
- [ ] At least two belonging anchors receive observable payoff.
- [ ] Existing prompts without the new block retain the current lifecycle.
- [ ] Full tests and skill validation pass.

## Deferred Decisions

These are deliberately outside this implementation rather than unfinished work:

- Importing ad campaign, click, conversion, revenue, or retention telemetry.
- Fine-tuning a model on the source corpus.
- Embedding search or nearest-neighbor retrieval over raw sample chapters.
- A Web editor for commercial contracts or quality reports.
- Reprocessing completed novels or rewriting existing manuscript prose.
- Cross-organization originality comparison or legal originality certification.
