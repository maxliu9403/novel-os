# Reader-Facing Story Lead Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a verified book-level `publication.copy` stage that generates `hook_lead` and a spoiler-free `spoiler_free_blurb` from approved Final chapters, projects them into every Novel OS export and an immutable H5 `PublicationPackage V1`, and renders one accessible story introduction on the H5 detail page.

**Architecture:** `PublicationSourceBuilder` reads only ArtifactStore Final heads and their run-bound promotion records. `WholeBookConflictBuilder`, `PublicationCopyWriter`, and `PublicationCopyValidator` operate on immutable source snapshots and return strict JSON contracts; the pipeline persists one SHA-bound `publication-copy.json` artifact before compile. Compile consumes the structured artifact through `CompiledBook`; delivery then projects the same bytes and Final chapter bytes into H5's existing V1 package without changing H5's public schema or database.

**Tech Stack:** Python 3.11 standard library, existing `LLMClient`/`ModelRouter`, ArtifactStore and PromotionService, pytest, Go standard library tests in `ai-novel-h5`, Next.js/React/Lucide, Vitest, Playwright.

**Spec:** [2026-08-31-reader-facing-story-lead-design.md](/Users/max/OpenSource/Novel-OS/docs/superpowers/specs/2026-08-31-reader-facing-story-lead-design.md)

## Global Constraints

- `hook_lead` is 8-20 English words; `spoiler_free_blurb` is 120-180 English words. For CJK, use 16-36 and 240-360 content characters respectively.
- Both fields express one `whole_book_core_conflict` with Final evidence from opening, middle, and late sections; the blurb is spoiler-free and ends with a concrete open loop.
- Only ArtifactStore Final heads plus matching accepted promotion records are authoritative. Mutable `outputs/manuscript/chapter_*_final.md` files are projections only.
- The writer, validator, and projector never mutate StoryState, chapter artifacts, or promotion records.
- No historical backfill, full synopsis, advertising metrics, database migration, public catalog field, or chapter rewrite is part of this change.
- Existing `## STORY_LEAD:` input remains a compatibility parser, but structured publication copy plus a chapter marker is a compile blocker.
- `publication.copy` runs after `book.check` and before `compile`; `delivery.package` is a separate final stage. A failed publication-copy stage produces no new book exports or H5 package.
- Provider/model names and response hashes are recorded without credentials or full request headers. Failed responses stay under the run feedback directory and never enter delivery artifacts.
- H5 continues using `spoiler_free_blurb -> description` and `hook_lead -> tagline`; H5 package schema V1 and catalog fields do not change.
- Preserve unrelated working-tree changes in both repositories. Novel OS implementation and tests are committed in this repository; H5 changes require permission escalation because its checkout is outside the writable root.

## File Map

### Novel OS

- Create `core/publication_copy.py`: strict schema models, language-aware counters, canonical JSON, deterministic quality gates, and claim/quote validation.
- Create `core/publication_source.py`: Final-head/promotion binding, ordered source-set fingerprinting, paragraph-safe source grouping, and evidence-ledger construction.
- Create `core/publication_copy_service.py`: conflict extraction, Style Curator writer call, Continuity Guardian call, bounded repair loop, provenance, feedback artifacts, and atomic publication-copy persistence.
- Create `core/h5_publication.py`: H5 V1 package projection, finalization/delivery bindings, atomic immutable directory publication, and package self-validation.
- Modify `core/pipeline_runner.py`: stage order, stage input hashes, publication/compile/delivery checkpoint handling, completion and resume repair behavior, and Final-source compilation.
- Modify `core/orchestrator.py`: expose the existing role-keyed LLM client for the publication service without adding an agent role.
- Modify `core/compile_book.py`, `core/compile_epub.py`, `core/compile_docx.py`, `core/compile_pdf.py`, and `core/styles.py`: structured pre-chapter blocks, typography, EPUB preamble/navigation, metadata, and word-count treatment.
- Modify `core/delivery_package.py`: copy the authoritative publication artifact, classify H5 files, and include only the current H5 package root.
- Create `tests/test_publication_copy.py`, `tests/test_publication_source.py`, `tests/test_publication_copy_service.py`, and `tests/test_h5_publication.py`.
- Modify `tests/test_pipeline_runner.py`, `tests/test_pipeline_integration.py`, `tests/test_styles_compile.py`, `tests/test_compile_binary.py`, and `tests/test_delivery_package.py` for the new lifecycle and projections.

### H5

- Create `apps/api/testdata/publication-packages/novel-os-v1/` with `meta/publication_package.json`, `meta/finalization.json`, `meta/delivery.json`, four exact chapter files, and `正文.md`.
- Modify `apps/api/internal/studio/app/import_test.go` and `apps/api/internal/studio/adapters/source_contract_test.go` to import the Novel OS fixture through the existing filesystem adapter.
- Modify `apps/storefront/app/storefront-home.tsx` to render one `StoryLead` component and add the real-overflow disclosure hook.
- Modify `apps/storefront/app/storefront.css` for the unframed lead, six/five-line clamps, 44px icon control, and day/night tokens.
- Modify `apps/storefront/app/storefront-detail.test.tsx`, create `apps/storefront/app/story-lead.interaction.test.tsx`, and modify `apps/storefront/test/react-hook-harness.ts` to test hook state/ref behavior without adding a testing dependency.
- Modify `apps/storefront/app/commercial-responsive.test.ts` for the story-lead CSS contract.
- Synchronize `docs/mvp-cn/07-h5-storefront-reader.md` and its entry in `docs/mvp-cn/ai-index.json`.

### Task 1: Publication Contracts And Language Counters

**Files:**
- Create: `core/publication_copy.py`
- Test: `tests/test_publication_copy.py`

**Interfaces:**
- Produces `EvidenceQuote`, `WholeBookCoreConflict`, `PublicationSourceRef`, `PublicationGeneration`, `PublicationValidation`, and `PublicationCopy` frozen dataclasses.
- Produces `parse_publication_copy(raw: bytes | str) -> PublicationCopy`, `count_publication_units(text: str, language: str) -> tuple[str, int]`, `canonical_json_bytes(value: Mapping[str, Any]) -> bytes`, and `validate_publication_candidate(candidate: Mapping[str, Any], *, source_chapters: Mapping[int, str], chapter_one_prefix: str, ending_spoilers: Sequence[str]) -> PublicationValidation`.
- `PublicationCopy.to_dict()` emits exactly the schema-v1 top-level keys; `PublicationCopy.from_dict()` rejects unknown keys, duplicate JSON keys, non-pass validation status, malformed SHA-256 values, and untrimmed strings.

- [ ] **Step 1: Write the failing tests**

```python
def test_round_trip_rejects_unknown_and_duplicate_keys():
    raw = '{"schema_version":1,"schema_version":1}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_publication_copy(raw)

    payload = valid_copy_payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="unknown publication copy field"):
        parse_publication_copy(json.dumps(payload))

def test_english_and_cjk_counters_use_the_contract_units():
    assert count_publication_units("Pregnant, thirty-six weeks", "en-US") == ("words", 3)
    assert count_publication_units("她必须保护女儿，离开谎言。", "zh-CN") == ("content_characters", 11)
    assert count_publication_units("one two three four five six seven eight", "en-US") == ("words", 8)

def test_quality_gate_rejects_copy_of_first_chapter_and_wrong_claim_quote():
    candidate = valid_candidate(hook_lead="Pregnant and betrayed, Claire must choose to protect her daughter.")
    with pytest.raises(ValueError, match="not_chapter_one_copy"):
        validate_publication_candidate(
            candidate,
            source_chapters={1: "Pregnant and betrayed, Claire must choose to protect her daughter. Then the door opened.",},
            chapter_one_prefix="Pregnant and betrayed, Claire must choose to protect her daughter. Then the door opened.",
            ending_spoilers=[],
        )

    candidate["claim_evidence"] = [{"claim": "The bank closes her account.", "chapter": 2, "source_quote": "not in chapter"}]
    with pytest.raises(ValueError, match="source_quote"):
        validate_publication_candidate(candidate, source_chapters={1: "Opening.", 2: "Chapter two."}, chapter_one_prefix="Opening.", ending_spoilers=[])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=core pytest tests/test_publication_copy.py -q`

Expected: collection fails because `core.publication_copy` and its contract functions do not exist.

- [ ] **Step 3: Implement the strict contract**

Implement duplicate detection with `json.loads(raw, object_pairs_hook=_reject_duplicate_keys)`; require exactly these top-level keys: `schema_version`, `title`, `language`, `reader_heading`, `hook_lead`, `spoiler_free_blurb`, `whole_book_core_conflict`, `source`, `generation`, and `validation`. Parse every nested key from the design document and reject any key set other than the declared keys. Use `[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*` for English word tokens, Unicode letter/number tokens for other space-delimited languages, and `unicodedata.category` to count non-whitespace, non-punctuation CJK content characters. Enforce inclusive length ranges, one-sentence hook, no Markdown/HTML/URL/CTA, no generic-only hook, no ending spoiler phrase, no repeated ten-word hook/blurb window, no repeated eight-word window against the first 500 English words of chapter one, and exact claim quote membership in the named source chapter. Store `length.unit`, all six reader-pull booleans, and `claim_evidence` in `PublicationValidation`.

```python
def count_publication_units(text: str, language: str) -> tuple[str, int]:
    locale = (language or "").casefold()
    if locale.startswith(("zh", "ja", "ko")):
        return "content_characters", sum(
            1 for char in text
            if not char.isspace() and unicodedata.category(char)[0] != "P"
        )
    token_pattern = _ENGLISH_WORD if locale.startswith("en") else _UNICODE_TOKEN
    return "words", len(token_pattern.findall(text))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=core pytest tests/test_publication_copy.py -q`

Expected: all schema, counter, length, repetition, spoiler, and quote tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/publication_copy.py tests/test_publication_copy.py
git commit -m "feat: add publication copy contracts and quality counters"
```

### Task 2: Final Source Set And Whole-Book Conflict Evidence

**Files:**
- Create: `core/publication_source.py`
- Test: `tests/test_publication_source.py`

**Interfaces:**
- Produces `SourceChapter(number: int, title: str, revision_id: str, sha256: str, text: str, promotion_receipt_id: str, finalized_at: str)` and `PublicationSourceSet(run_id: str, chapters: tuple[SourceChapter, ...], source_set_sha256: str)`.
- Produces `build_publication_source_set(project: Path, run_id: str, chapter_count: int, quality_policy: str) -> PublicationSourceSet`, `publication_source_input_hash(source_set) -> str`, `group_source_chapters(source_set, max_codepoints: int = 120_000) -> tuple[tuple[SourceChapter, ...], ...]`, and `build_evidence_ledger(group: Sequence[SourceChapter]) -> str`.
- Evidence policy loads `PromotionService.load_receipt("pipeline-{run_id}-chapter-{number}", check_current_tail=False)` for `evidence_v1`; legacy runs require the completed `chapter.promote` StageResult binding and its deterministic legacy receipt id to match the Final head revision and SHA. Both paths reject missing heads, empty/invalid UTF-8 blobs, divergent revision hashes, and missing promotion bindings before any model call.

- [ ] **Step 1: Write the failing tests**

```python
def test_source_set_reads_final_head_bytes_and_receipt_not_mutable_projection(tmp_path):
    project, manifest = evidence_fixture(tmp_path, chapter_count=4)
    (project / "outputs/manuscript/chapter_001_final.md").write_text("forged", encoding="utf-8")
    source = build_publication_source_set(project, manifest.run_id, 4, "evidence_v1")
    assert source.chapters[0].text == "Approved chapter one."
    assert source.chapters[0].revision_id == ArtifactStore(project).get_head(1, "final").revision_id

def test_source_set_blocks_receipt_or_final_head_drift(tmp_path):
    project, manifest = evidence_fixture(tmp_path, chapter_count=4)
    receipt_path = next((project / "outputs/state/promotion_receipts").glob("*.json"))
    receipt_path.unlink()
    with pytest.raises(PublicationSourceError, match="promotion receipt"):
        build_publication_source_set(project, manifest.run_id, 4, "evidence_v1")

def test_grouping_preserves_every_chapter_and_only_splits_at_paragraph_boundaries(tmp_path):
    source = source_set_with_texts(["A" * 90_000 + "\n\nB" * 40_000, "Chapter two."])
    groups = group_source_chapters(source, max_codepoints=120_000)
    assert [chapter.number for group in groups for chapter in group] == [1, 1, 2]
    assert all("\n\n" in item for item in build_evidence_ledger(groups[0]).split("\n"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=core pytest tests/test_publication_source.py -q`

Expected: collection fails because the source builder and error type are absent.

- [ ] **Step 3: Implement Final binding and bounded grouping**

Use `ArtifactStore.get_head(number, "final")`, `get_revision`, and `read_text`; compare every result with the run's promotion binding. Canonicalize the source fingerprint as compact UTF-8 JSON of `[{"chapter": number, "revision_id": revision_id, "sha256": sha256}]` sorted by chapter and hash it with SHA-256. Split oversized text only after `\n\n`; each split carries the original chapter number and revision id. The ledger must list every segment and exact quoted source text, never a StoryState summary or mutable projection. Add a conflict-evidence helper that maps chapters to opening/middle/late buckets using the proportional rule from the design document and rejects a bucket without at least one exact quote.

```python
def publication_source_input_hash(source: PublicationSourceSet) -> str:
    return hashlib.sha256(
        canonical_json_bytes([
            {"chapter": item.number, "revision_id": item.revision_id, "sha256": item.sha256}
            for item in source.chapters
        ])
    ).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=core pytest tests/test_publication_source.py -q`

Expected: Final projection tampering is ignored, receipt/head drift blocks, fingerprints are stable, and long source grouping covers all chapters.

- [ ] **Step 5: Commit**

```bash
git add core/publication_source.py tests/test_publication_source.py
git commit -m "feat: bind publication copy to final source evidence"
```

### Task 3: Writer, Guardian, And Bounded Repair Service

**Files:**
- Create: `core/publication_copy_service.py`
- Test: `tests/test_publication_copy_service.py`

**Interfaces:**
- Produces `PublicationCopyService.generate(project: Path, run_id: str, title: str, language: str, genre: str, source: PublicationSourceSet, book_check_stage_sha256: str, ending_contract_sha256: str, finished_at: str) -> PublicationCopy`.
- Constructor accepts two protocol-compatible clients (`complete(system: str, user: str) -> str`, `provider`, `model`) for `style_curator` and `continuity_guardian`, plus `max_repairs: int = 2`.
- Generation uses `whole-book-conflict.v1`, `publication-copy-writer.v1`, and `publication-copy-validator.v1`; conflict and writer calls use the Style Curator client, semantic validation uses the Continuity Guardian client. `generation` stores provider/model, prompt versions, response hashes, and one RFC3339 `generated_at` preserved on retries.
- Persistence writes `outputs/publication/publication-copy.json` atomically with `PublicationCopy.to_dict()` and writes failed raw responses to `outputs/runs/{run_id}/feedback/publication-copy-attempt-{n:02d}.raw`.

- [ ] **Step 1: Write the failing tests**

```python
def test_generate_requires_three_buckets_and_records_all_model_provenance(tmp_path):
    source = source_set_with_opening_middle_late(tmp_path)
    writer = FakeClient([conflict_json(), writer_json()])
    guardian = FakeClient([guardian_pass_json()])
    copy = PublicationCopyService(writer, guardian).generate(
        tmp_path, "run-001", "The Empty Chair Beside Her", "en-US", "family drama",
        source, "b" * 64, "e" * 64, "2026-08-31T00:00:00Z",
    )
    assert copy.validation.status == "pass"
    assert copy.generation.conflict_prompt_version == "whole-book-conflict.v1"
    assert copy.generation.writer_model == "style-model"
    assert json.loads((tmp_path / "outputs/publication/publication-copy.json").read_text())['source']['source_set_sha256'] == source.source_set_sha256

def test_semantic_failure_repairs_at_most_twice_without_regenerating_conflict(tmp_path):
    source = source_set_with_opening_middle_late(tmp_path)
    writer = FakeClient([conflict_json(), bad_writer_json(), bad_writer_json(), good_writer_json()])
    guardian = FakeClient([guardian_fail_json("open_loop"), guardian_fail_json("open_loop"), guardian_pass_json()])
    copy = PublicationCopyService(writer, guardian, max_repairs=2).generate(
        tmp_path, "run-002", "Test Book", "en-US", "family drama", source, "b" * 64, "e" * 64, "2026-08-31T00:00:00Z",
    )
    assert copy.validation.status == "pass"
    assert writer.calls.count("whole-book-conflict.v1") == 1
    assert len(list((tmp_path / "outputs/runs/run-002/feedback").glob("*.raw"))) == 2

def test_invalid_json_blocks_and_does_not_write_publication_artifact(tmp_path):
    source = source_set_with_opening_middle_late(tmp_path)
    with pytest.raises(PublicationCopyBlocked, match="JSON"):
        PublicationCopyService(FakeClient(["not json"]), FakeClient([])).generate(
            tmp_path, "run-003", "Test Book", "en-US", "family drama", source, "b" * 64, "e" * 64, "2026-08-31T00:00:00Z",
        )
    assert not (tmp_path / "outputs/publication/publication-copy.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=core pytest tests/test_publication_copy_service.py -q`

Expected: collection fails because the service, protocols, and blocked error are absent.

- [ ] **Step 3: Implement the prompt and repair loop**

Build one untrusted-content boundary per source chapter (`<chapter number="1" revision="rev-001">chapter text</chapter>`), so chapter instructions cannot become system instructions. When the complete source exceeds 120,000 code points, call the conflict extractor per group, merge only exact quotes into the evidence ledger, and send the merged ledger to the writer; Guardian receives the same group boundaries. Parse every response with the strict parser from Task 1. The conflict contract must include protagonist, goal, opposition, stakes, escalation, unresolved choice, and exact opening/middle/late quotes. Run deterministic validation before Guardian, then merge Guardian checks and claims; on failure send only the reported finding codes and the unchanged source-bound contract to a writer repair call. Stop after two repairs and raise `PublicationCopyBlocked`. Build the complete artifact only after all checks pass, bind `book_check_stage_sha256`, metadata SHA, ending SHA, and source chapter refs, then atomically persist it.

```python
def _repair_prompt(candidate: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> str:
    return (
        "Return only the same JSON object with the reported quality findings repaired. "
        "Do not add fields, change source quotes, reveal the ending, or alter the core conflict.\n\n"
        + json.dumps({"candidate": candidate, "findings": list(findings)}, ensure_ascii=False, sort_keys=True)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=core pytest tests/test_publication_copy_service.py -q`

Expected: successful generation is persisted with stable provenance, semantic failures repair within the bound, and invalid responses leave no deliverable artifact.

- [ ] **Step 5: Commit**

```bash
git add core/publication_copy_service.py tests/test_publication_copy_service.py
git commit -m "feat: generate and validate reader-facing publication copy"
```

### Task 4: Pipeline Stage And Checkpoint Integration

**Files:**
- Modify: `core/orchestrator.py`
- Modify: `core/pipeline_runner.py`
- Test: `tests/test_pipeline_runner.py`
- Test: `tests/test_pipeline_integration.py`

**Interfaces:**
- Add `NovelOrchestrator.llm_for(agent_name: str) -> LLMClient`, delegating to the existing `_get_llm` role router; do not add a new model role.
- Add `publication.copy` to the stage order after `book.check`, and add `delivery.package` after `compile`.
- `_stage_input_hashes(project, phase, chapter, manifest=None)` includes for publication copy: `source_set_sha256`, `book_check_stage_sha256`, canonical title/language/genre metadata SHA, ending-contract SHA when present, and `sha256(b"publication-copy-policy.v1")`; compile includes Final heads plus publication-copy SHA; delivery includes all compiled output hashes plus publication-copy SHA and the current H5 package snapshot inputs.
- `_compile_book()` reads publication copy and Final heads only, writes requested `book.md`, `book.epub`, `book.pdf`, and `book.docx`, and never calls `build_delivery_package()`.
- `_deliver_package()` invokes the H5 projector and `build_delivery_package()`; `manifest.status = "completed"` only after `delivery.package` is done. For fewer than four chapters it records `h5_publication: inapplicable_less_than_four_chapters` in the delivery StageResult decisions and omits the H5 root. For four or more chapters it adds the returned `meta/publication_package.json` to the delivery checkpoint artifact list. Resume reuses a valid publication artifact and does not call a model during compile/delivery retries.

- [ ] **Step 1: Write the failing tests**

```python
def test_stage_order_has_publication_and_delivery_after_book_check(tmp_path):
    manifest = run_four_chapter_fixture(tmp_path)
    phases = [result.phase for result in manifest.stages.values() if result.chapter is None]
    assert phases.index("book.check") < phases.index("publication.copy") < phases.index("compile") < phases.index("delivery.package")
    assert manifest.current_phase == "delivery.package"

def test_publication_failure_stops_before_exports(tmp_path):
    manifest = run_fixture_with_publication_writer(tmp_path, writer_response="{}")
    assert manifest.status == "paused"
    assert manifest.current_phase == "publication.copy"
    assert not (tmp_path / "project/outputs/deliverables/book.md").exists()

def test_compile_retry_reuses_publication_checkpoint_and_final_bytes(tmp_path):
    result = run_four_chapter_fixture(tmp_path)
    copy_sha = PipelineRunner._sha256(tmp_path / "project/outputs/publication/publication-copy.json")
    final_sha = PipelineRunner._sha256(tmp_path / "project/outputs/manuscript/chapter_001_final.md")
    resumed = PipelineRunner(orchestrator_factory=factory).resume(result.run_id, tmp_path / "project")
    assert PipelineRunner._sha256(tmp_path / "project/outputs/publication/publication-copy.json") == copy_sha
    assert PipelineRunner._sha256(tmp_path / "project/outputs/manuscript/chapter_001_final.md") == final_sha
    assert resumed.get("publication.copy").attempt == 1

def test_final_head_change_invalidates_publication_and_every_downstream_stage(tmp_path):
    result = run_four_chapter_fixture(tmp_path)
    append_new_final_head(tmp_path / "project", 2, "Changed approved text.")
    resumed = PipelineRunner(orchestrator_factory=factory).resume(result.run_id, tmp_path / "project")
    assert resumed.get("publication.copy").attempt == 1
    assert resumed.get("compile").status == "done"
    assert resumed.get("delivery.package").status == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=core pytest tests/test_pipeline_runner.py tests/test_pipeline_integration.py -q -k 'publication or delivery or stage_order'`

Expected: the new phase keys are absent, compile still packages inline, and the order assertions fail.

- [ ] **Step 3: Implement stage wiring and recovery**

Expose `llm_for`, instantiate `PublicationCopyService(orchestrator.llm_for("style_curator"), orchestrator.llm_for("continuity_guardian"))`, and pass runtime provenance without secrets. Make the publication operation load the persisted `book.check` stage JSON to hash the exact stage result. Use a deterministic metadata payload from `StoryState.metadata`; include `manifest.spec.num_chapters` in the source builder call. On blocked publication validation, let `_stage` persist `blocked` and leave the run paused. Add `_deliver_package` as a separate `_stage` operation with explicit manifest/ZIP artifacts plus the returned H5 package metadata path when applicable; set `finished_at` for H5 delivery from the persisted compile stage `finished_at`, and set H5 finalization `finalized_at` from `book.check.finished_at`. Update completed-run repair to validate publication, compile, and delivery checkpoints in sequence, rebuild only missing projections, and preserve `current_phase = "delivery.package"`.

```python
self._stage(
    manifest, project, store, "publication.copy", None,
    lambda: self._generate_publication_copy(manifest, project),
    lambda value: isinstance(value, PublicationCopy),
    ["outputs/publication/publication-copy.json"],
)
self._stage(
    manifest, project, store, "compile", None,
    lambda: self._compile_book(manifest, project),
    lambda value: bool(value),
    [f"outputs/deliverables/book.{self._format_extension(fmt)}" for fmt in manifest.spec.output_formats],
)
self._stage(
    manifest, project, store, "delivery.package", None,
    lambda: self._deliver_package(manifest, project),
    lambda value: bool(value),
    ["outputs/deliverables/package-manifest.json", "outputs/deliverables/book-package.zip"],
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=core pytest tests/test_pipeline_runner.py tests/test_pipeline_integration.py -q -k 'publication or delivery or stage_order'`

Expected: order, blocked-run, source invalidation, resume reuse, and completion-phase tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py core/pipeline_runner.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py
git commit -m "feat: add publication copy and delivery pipeline stages"
```

### Task 5: Structured Compile Projection And Format Metadata

**Files:**
- Modify: `core/compile_book.py`
- Modify: `core/compile_epub.py`
- Modify: `core/compile_docx.py`
- Modify: `core/compile_pdf.py`
- Modify: `core/styles.py`
- Test: `tests/test_styles_compile.py`
- Test: `tests/test_compile_binary.py`

**Interfaces:**
- Add `CompiledBook.language: str` and `CompiledBook.publication_copy: PublicationCopy | None`, serialize both fields, and change `gather(*, title: str, author: str, genre: str, chapters: list[dict[str, Any]], publication_copy: PublicationCopy | None = None) -> CompiledBook`; blocks are added with `chapter=None` in the order `story_lead_title`, `story_hook`, `story_lead` before chapter one.
- Add `story_hook` to `STYLE_ROLES`, use it for the one-line hook, and exclude `story_lead_title`, `story_hook`, and `story_lead` from `CompiledBook.word_count`.
- `gather` raises `ValueError("structured publication copy and STORY_LEAD marker are both present")` when both sources exist.
- EPUB creates an `intro.xhtml` preamble in the spine before chapter files; `nav.xhtml` lists only actual chapters. `content.opf` emits exact publication blurb in `dc:description` and exact `CompiledBook.language` in `dc:language`.
- Markdown, HTML, DOCX, and PDF all render the same three blocks before the first chapter title; no renderer reads publication JSON independently.

- [ ] **Step 1: Write the failing tests**

```python
def test_structured_copy_is_projected_before_chapter_one_and_not_counted():
    book = gather(title="T", author="A", genre="Family drama", chapters=[{"number": 1, "title": "One", "text": "# One\n\nBody."}], publication_copy=publication_copy())
    assert [block.kind for block in book.blocks[:3]] == ["story_lead_title", "story_hook", "story_lead"]
    assert book.blocks[3].kind == "chapter_title"
    assert book.word_count == 1
    assert "Before the Story" in render_markdown(book, StyleSheet())

def test_structured_copy_and_legacy_marker_block_compile():
    with pytest.raises(ValueError, match="both present"):
        gather(title="T", author="", genre="", publication_copy=publication_copy(), chapters=[{"number": 1, "title": "One", "text": "## STORY_LEAD: Old\n\nOld copy\n\n# One\n\nBody."}])

def test_epub_uses_intro_preamble_description_and_real_navigation():
    z = zipfile.ZipFile(BytesIO(render_epub(book_with_publication_copy(), StyleSheet())))
    names = z.namelist()
    assert names.index("OEBPS/intro.xhtml") < names.index("OEBPS/chap001.xhtml")
    assert "Before the Story" in z.read("OEBPS/intro.xhtml").decode()
    assert "Pregnant and betrayed" in z.read("OEBPS/content.opf").decode()
    assert "intro.xhtml" not in z.read("OEBPS/nav.xhtml").decode()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=core pytest tests/test_styles_compile.py tests/test_compile_binary.py -q -k 'publication or structured or intro'`

Expected: `gather` has no publication argument, the `story_hook` role is unknown, and EPUB has no preamble or description.

- [ ] **Step 3: Implement shared blocks and renderer projection**

Add a private `_publication_blocks` helper in `compile_book.py`; preserve chapter text bytes and chapter navigation metadata. Pass `book.publication_copy.language` through `CompiledBook.language` with `en-US` as the existing default for books without structured copy. In EPUB, split blocks where `block.chapter is None` into `intro.xhtml`; group only integer chapter keys for `chap001.xhtml` onward, build nav from `book.chapters`, and escape `dc:description`. Replace the random EPUB UUID with a UUID5 derived from canonical `CompiledBook.to_dict()` so rerendering identical inputs is byte-stable. In DOCX/PDF, the existing block iterators consume the new block kinds through `StyleSheet.get`.

```python
def _publication_blocks(copy: PublicationCopy) -> list[Block]:
    return [
        Block("story_lead_title", copy.reader_heading),
        Block("story_hook", copy.hook_lead),
        Block("story_lead", copy.spoiler_free_blurb),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=core pytest tests/test_styles_compile.py tests/test_compile_binary.py -q`

Expected: all structured-copy ordering, marker collision, word-count, EPUB metadata/navigation, and existing renderer tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/compile_book.py core/compile_epub.py core/compile_docx.py core/compile_pdf.py core/styles.py tests/test_styles_compile.py tests/test_compile_binary.py
git commit -m "feat: project publication copy into compiled formats"
```

### Task 6: H5 V1 Projector And Deterministic Delivery Split

**Files:**
- Create: `core/h5_publication.py`
- Modify: `core/delivery_package.py`
- Modify: `core/pipeline_runner.py`
- Test: `tests/test_h5_publication.py`
- Test: `tests/test_delivery_package.py`

**Interfaces:**
- Produces `H5PublicationResult(package_id: str, root: Path, publication_package_path: Path, finalization_path: Path, delivery_path: Path, snapshot_sha256: str)` and `project_h5_publication(project: Path, run_id: str, source: PublicationSourceSet, publication_copy: PublicationCopy, book_check_finished_at: str, compile_finished_at: str, title: str, alternate_titles: Sequence[str], tags: Sequence[str]) -> H5PublicationResult | None`.
- `project_h5_publication` returns `None` for fewer than four chapters and records `h5_publication: inapplicable_less_than_four_chapters` in delivery metadata; four or more chapters create `outputs/deliverables/h5-publication/pkg-{run_id}-{snapshot_sha12}/`.
- `publication_package.json` uses exactly H5 V1 fields, exact Final chapter UTF-8 bytes, H5 `utf8-runes` counts, and `final_checks` all true. `正文.md` joins raw chapter bytes with `\n\n` and one final `\n`.
- `finalization.json` has one accepted receipt per chapter and `finalized_at = book.check.finished_at`; `delivery.json` uses `SHA256(publication_package_raw || finalization_raw)` and `delivered_at = compile.finished_at`.
- Atomic publication writes a sibling temporary directory, self-validates through the same byte/hash rules, renames only to a never-existing package id, and accepts an existing directory only when every file is byte-identical.
- Change `build_delivery_package(project_path: str | Path, *, cover_set: CoverSet | None = None, publication_copy_path: Path | None = None, h5_root: Path | None = None) -> PackageResult`; it copies `outputs/publication/publication-copy.json` to `outputs/deliverables/meta/publication-copy.json`, classifies it as `publication_copy`, classifies the current H5 subtree as `h5_publication_object`, and excludes stale H5 package roots unless explicitly passed as current.

- [ ] **Step 1: Write the failing tests**

```python
def test_h5_projector_matches_v1_bytes_counts_receipts_and_snapshot(tmp_path):
    source, copy = four_chapter_source_and_copy(tmp_path)
    result = project_h5_publication(tmp_path, "run-123", source, copy, "2026-08-31T00:00:00Z", "2026-08-31T00:01:00Z", "The Empty Chair Beside Her", [], [])
    package_raw = result.publication_package_path.read_bytes()
    finalization_raw = result.finalization_path.read_bytes()
    delivery = json.loads(result.delivery_path.read_text())
    assert delivery["snapshot_sha256"] == hashlib.sha256(package_raw + finalization_raw).hexdigest()
    assert json.loads(package_raw)["chapters"][0]["body_sha256"] == hashlib.sha256((result.root / "chapters/01.md").read_bytes()).hexdigest()
    assert (result.root / "正文.md").read_bytes() == b"# One\n\nBody.\n\n# Two\n\nBody.\n"

def test_h5_projector_keeps_old_immutable_root_on_collision_or_failure(tmp_path):
    source, copy = four_chapter_source_and_copy(tmp_path)
    first = project_h5_publication(tmp_path, "run-123", source, copy, "2026-08-31T00:00:00Z", "2026-08-31T00:01:00Z", "T", [], [])
    (first.root / "chapters/01.md").write_bytes(b"tampered")
    with pytest.raises(H5PublicationError, match="byte-identical"):
        project_h5_publication(tmp_path, "run-123", source, copy, "2026-08-31T00:00:00Z", "2026-08-31T00:01:00Z", "T", [], [])
    assert first.root.exists()

def test_delivery_manifest_roles_include_copy_and_current_h5_only(tmp_path):
    copy_path = tmp_path / "outputs/publication/publication-copy.json"
    copy_path.parent.mkdir(parents=True)
    copy_path.write_text('{"schema_version":1}\n', encoding="utf-8")
    h5_root = tmp_path / "outputs/deliverables/h5-publication/pkg-run-123-aaaaaaaaaaaa"
    (h5_root / "meta").mkdir(parents=True)
    (h5_root / "meta/publication_package.json").write_text("{}\n", encoding="utf-8")
    result = build_delivery_package(tmp_path, publication_copy_path=copy_path, h5_root=h5_root)
    roles = {entry["path"]: entry["role"] for entry in json.loads(result.manifest_path.read_text())["files"]}
    assert roles["meta/publication-copy.json"] == "publication_copy"
    assert all(role == "h5_publication_object" for path, role in roles.items() if path.startswith("h5-publication/"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=core pytest tests/test_h5_publication.py tests/test_delivery_package.py -q -k 'h5 or publication_copy or immutable'`

Expected: the projector module and new manifest role do not exist.

- [ ] **Step 3: Implement the V1 projector and delivery split**

Use `json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"` for both bound JSON files. Derive `snapshot_sha256` from the exact raw bytes before writing `delivery.json`; include the package id in the returned result and in the pipeline stage artifact paths. Validate chapter numbers, titles, hashes, counts, source refs, and all final checks before rename. For fewer than four chapters, do not create a fake H5 root. Extend `_payload_files` role classification without changing archive ordering or fixed ZIP timestamps, and pass the current H5 root explicitly from `_deliver_package`.

```python
def merged_manuscript(chapter_bodies: Sequence[bytes]) -> bytes:
    return b"\n\n".join(chapter_bodies) + b"\n"

def delivery_snapshot(package_raw: bytes, finalization_raw: bytes) -> str:
    return hashlib.sha256(package_raw + finalization_raw).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=core pytest tests/test_h5_publication.py tests/test_delivery_package.py -q`

Expected: V1 JSON, raw chapter bytes, merged manuscript, receipt, snapshot, immutability, short-book, role, and stable archive tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/h5_publication.py core/delivery_package.py core/pipeline_runner.py tests/test_h5_publication.py tests/test_delivery_package.py
git commit -m "feat: project immutable H5 publication packages"
```

### Task 7: H5 Import Golden Fixture

**Files:**
- Create: `apps/api/testdata/publication-packages/novel-os-v1/meta/publication_package.json`
- Create: `apps/api/testdata/publication-packages/novel-os-v1/meta/finalization.json`
- Create: `apps/api/testdata/publication-packages/novel-os-v1/meta/delivery.json`
- Create: `apps/api/testdata/publication-packages/novel-os-v1/chapters/01.md`
- Create: `apps/api/testdata/publication-packages/novel-os-v1/chapters/02.md`
- Create: `apps/api/testdata/publication-packages/novel-os-v1/chapters/03.md`
- Create: `apps/api/testdata/publication-packages/novel-os-v1/chapters/04.md`
- Create: `apps/api/testdata/publication-packages/novel-os-v1/正文.md`
- Modify: `apps/api/internal/studio/app/import_test.go`
- Modify: `apps/api/internal/studio/adapters/source_contract_test.go`

**Interfaces:**
- No production H5 importer interface changes. The fixture must use `platform: "novel-os"`, four contiguous chapters, non-empty hook/blurb, exact body hashes, `counting_policy: "utf8-runes"`, accepted finalization receipts, and a delivery snapshot bound to the raw JSON bytes.
- Tests continue using `NewFilesystemSource(config.EnvironmentDevelopment, filepath.Dir(fixture))` and the existing `NewImporter(environment, sources, recordingStore)`; they prove the Novel OS projection is accepted without schema or database changes.

- [ ] **Step 1: Write the failing tests**

```go
func TestImportAcceptsNovelOSPublicationPackageFixture(t *testing.T) {
	_, source, _, ok := runtime.Caller(0)
	if !ok { t.Fatal("resolve test source") }
	fixture := filepath.Join(filepath.Dir(source), "..", "..", "..", "testdata", "publication-packages", "novel-os-v1")
	filesystem, err := adapters.NewFilesystemSource(config.EnvironmentDevelopment, filepath.Dir(fixture))
	if err != nil { t.Fatal(err) }
	importer, err := NewImporter(config.EnvironmentDevelopment, map[string]ports.PackageSource{domain.SourceAdapterFilesystem: filesystem}, &recordingStore{})
	if err != nil { t.Fatal(err) }
	result, err := importer.Import(context.Background(), ImportParams{Adapter: domain.SourceAdapterFilesystem, SourceRef: "novel-os-v1", OperatorID: "operator-fixture", OperatorSessionID: "session-fixture"})
	if err != nil { t.Fatalf("Novel OS fixture import: %v", err) }
	if result.Created != true || result.BookID == "" { t.Fatalf("unexpected result: %+v", result) }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/max/workspace/goPrj/src/ai-novel-h5/apps/api && GOCACHE=/tmp/novel-go-cache GOTMPDIR=/tmp go test ./internal/studio/app ./internal/studio/adapters -run NovelOS -count=1`

Expected: the fixture path is missing.

- [ ] **Step 3: Add the exact fixture and test raw delivery binding**

Use four small Markdown bodies with headings and prose, write `正文.md` with the H5 separator algorithm, compute each `sha256` and rune count from the exact bytes, and compute `delivery.snapshot_sha256` by concatenating the final `publication_package.json` bytes and finalization bytes with no separator. Keep `finalization_source` as `novel-os run fixture-20260831 book.check`. Add a source-contract assertion that `hook_lead` and `spoiler_free_blurb` arrive unchanged in the imported catalog projection.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/max/workspace/goPrj/src/ai-novel-h5/apps/api && GOCACHE=/tmp/novel-go-cache GOTMPDIR=/tmp go test ./internal/studio/app ./internal/studio/adapters -run 'NovelOS|PublicationPackage' -count=1`

Expected: the existing importer accepts the fixture and the imported fields match the JSON exactly.

- [ ] **Step 5: Commit**

```bash
git -C /Users/max/workspace/goPrj/src/ai-novel-h5 add apps/api/testdata/publication-packages/novel-os-v1 apps/api/internal/studio/app/import_test.go apps/api/internal/studio/adapters/source_contract_test.go
git -C /Users/max/workspace/goPrj/src/ai-novel-h5 commit -m "test: accept Novel OS publication package fixture"
```

### Task 8: H5 Story Lead UI And Real Overflow Disclosure

**Files:**
- Modify: `apps/storefront/test/react-hook-harness.ts`
- Modify: `apps/storefront/app/storefront-home.tsx`
- Modify: `apps/storefront/app/storefront.css`
- Modify: `apps/storefront/app/storefront-detail.test.tsx`
- Create: `apps/storefront/app/story-lead.interaction.test.tsx`
- Modify: `apps/storefront/app/commercial-responsive.test.ts`

**Interfaces:**
- Add `useStoryLeadDisclosure(bookId: string, description: string) -> { descriptionRef: MutableRefObject<HTMLParagraphElement | null>, expanded: boolean, overflowing: boolean, toggle: () => void }`.
- Add `StoryLead({ book }: { book: FixtureBook })`, rendering nothing when both `book.tagline` and `book.description` are empty, rendering hook alone when description is empty, and rendering one `detail-story-lead` section after the hero and before `DetailActionGroup`.
- Use Lucide `Quote`, `ChevronDown`, and `ChevronUp`; only render the 44px icon button when measured overflow is true. Set `aria-expanded`, `aria-controls="story-lead-description-{book.id}"`, and matching labels `Expand story introduction` / `Collapse story introduction`; the icon is `aria-hidden` and has a matching `title`.
- Measure with `scrollHeight > clientHeight + 1` while collapsed; cache the collapsed height and compare against it while expanded. Re-measure on book id/description changes, `document.fonts.ready`, and `ResizeObserver`; reset expanded state when the book id changes. Do not animate height. Use `overflow-wrap: anywhere`.
- Desktop blurb clamp is six lines; at `max-width: 767px` it is five. Hook is never clamped. Existing day/night tokens remain the only colors.

- [ ] **Step 1: Write the failing tests**

```typescript
class FakeResizeObserver {
  static last: FakeResizeObserver | undefined;
  private readonly callback: () => void;
  constructor(callback: () => void) {
    this.callback = callback;
    FakeResizeObserver.last = this;
  }
  observe(): void {}
  disconnect(): void {}
  trigger(): void { this.callback(); }
}

it("reports only real measured overflow and preserves the collapsed height after expansion", async () => {
  const element = { scrollHeight: 240, clientHeight: 150 } as HTMLParagraphElement;
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("document", { fonts: { ready: Promise.resolve() } });
  const render = () => harness.render(() => useStoryLeadDisclosure("book-1", "long copy"));
  const initial = render();
  initial.descriptionRef.current = element;
  await harness.runEffects();
  expect(render().overflowing).toBe(true);
  render().toggle();
  element.clientHeight = 240;
  expect(render().expanded).toBe(true);
  FakeResizeObserver.last?.trigger();
  await harness.runEffects();
  expect(render().overflowing).toBe(true);
});

it("resets expanded state for a different book and uses the matching keyboard label", async () => {
  const render = () => harness.render(() => useStoryLeadDisclosure("book-1", "long copy"));
  const state = render();
  state.toggle();
  expect(render().expanded).toBe(true);
  harness.render(() => useStoryLeadDisclosure("book-2", "short copy"));
  await harness.runEffects();
  expect(render().expanded).toBe(false);
});

it("renders one lead in semantic order and no retired About section", () => {
  const markup = renderToStaticMarkup(createElement(DetailView, { book, onBack: vi.fn(), onRead: vi.fn(), onUnlock: vi.fn() }));
  expect(markup.match(/detail-story-lead/g)).toHaveLength(1);
  expect(markup).not.toContain("About this story");
  expect(markup.indexOf("detail-book-hero")).toBeLessThan(markup.indexOf("detail-story-lead"));
  expect(markup.indexOf("detail-story-lead")).toBeLessThan(markup.indexOf("detail-reading-band"));
  expect(markup.match(/A house remembers every promise\./g)).toHaveLength(1);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/max/workspace/goPrj/src/ai-novel-h5/apps/storefront && pnpm vitest run app/story-lead.interaction.test.tsx app/storefront-detail.test.tsx app/commercial-responsive.test.ts`

Expected: `useStoryLeadDisclosure` is missing, the detail page still contains `About this story`, and the old character-count toggle remains.

- [ ] **Step 3: Implement the hook, component, and CSS**

Extend the local hook harness with `useRef` storage so the interaction test remains dependency-free. In the hook, use one effect for reset and one for measurement; attach and clean up `ResizeObserver`, `document.fonts.ready`, and observer callbacks. In `StoryLead`, keep `book.tagline` as the H5 hook mapping, use a static `Before the story` heading, and put the hook in a `blockquote`-like paragraph with `Quote`. Replace the old description section and length heuristic in `DetailView`. Add the scoped CSS near the current detail styles, remove the old `.detail-about` rules, preserve the 44px touch target, and add mobile clamp and focus-visible states.

```tsx
const overflowing = element.scrollHeight > collapsedHeight + 1;
return {
  descriptionRef,
  expanded,
  overflowing,
  toggle: () => setExpanded((value) => !value),
};
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/max/workspace/goPrj/src/ai-novel-h5/apps/storefront && pnpm vitest run app/story-lead.interaction.test.tsx app/storefront-detail.test.tsx app/commercial-responsive.test.ts`

Expected: semantic order, one-copy rendering, empty-field compatibility, keyboard/state labels, measured-overflow, clamp, and theme CSS tests pass.

- [ ] **Step 5: Commit**

```bash
git -C /Users/max/workspace/goPrj/src/ai-novel-h5 add apps/storefront/test/react-hook-harness.ts apps/storefront/app/storefront-home.tsx apps/storefront/app/storefront.css apps/storefront/app/storefront-detail.test.tsx apps/storefront/app/story-lead.interaction.test.tsx apps/storefront/app/commercial-responsive.test.ts
git -C /Users/max/workspace/goPrj/src/ai-novel-h5 commit -m "feat: render accessible story lead on book details"
```

### Task 9: Documentation Synchronization And Cross-Repository Verification

**Files:**
- Modify: `/Users/max/workspace/goPrj/src/ai-novel-h5/docs/mvp-cn/07-h5-storefront-reader.md`
- Modify: `/Users/max/workspace/goPrj/src/ai-novel-h5/docs/mvp-cn/ai-index.json`
- Test: affected Novel OS pytest modules and H5 unit suites

**Interfaces:**
- Document the current H5 data flow as `hook_lead -> tagline` and `spoiler_free_blurb -> description`, the single story-lead placement, measured overflow behavior, and the fact that Novel OS produces immutable V1 roots while H5 importer/catalog schema remains unchanged.
- Keep the `docs/mvp-cn/ai-index.json` entry summary and tags synchronized with the updated reader contract; do not alter unrelated document entries.

- [ ] **Step 1: Write the failing documentation assertions**

```bash
if rg -q "Novel OS immutable V1|scrollHeight > clientHeight \+ 1|desktop six-line|mobile five-line" /Users/max/workspace/goPrj/src/ai-novel-h5/docs/mvp-cn/07-h5-storefront-reader.md; then
  exit 1
fi
```

Expected: the command exits 0 because the current document does not state the Novel OS source, single lead placement, or measured six/five-line behavior.

- [ ] **Step 2: Run the affected checks before documentation changes**

Run: `cd /Users/max/workspace/goPrj/src/ai-novel-h5 && pnpm --filter @novel/storefront test && cd apps/api && GOCACHE=/tmp/novel-go-cache GOTMPDIR=/tmp go test ./internal/studio/app ./internal/studio/adapters`

Expected: the code tests from Tasks 7-8 pass; the docs grep still lacks the new contract statements.

- [ ] **Step 3: Update the authoritative H5 document and index**

Add a concise subsection under the reader detail contract covering the exact field mapping, ordering, empty-field compatibility, `scrollHeight > clientHeight + 1` rule, cached collapsed height, `ResizeObserver`, 44px labels, and desktop/mobile clamp. Add the Novel OS V1 fixture and immutable package root to the publication-package subsection. Update only the matching `ai-index.json` document summary/tags and preserve valid JSON formatting.

- [ ] **Step 4: Run focused and full local verification**

Novel OS:

```bash
PYTHONPATH=core pytest tests/test_publication_copy.py tests/test_publication_source.py tests/test_publication_copy_service.py tests/test_h5_publication.py tests/test_pipeline_runner.py tests/test_pipeline_integration.py tests/test_styles_compile.py tests/test_compile_binary.py tests/test_delivery_package.py -q
PYTHONPATH=core pytest -q
```

H5:

```bash
cd /Users/max/workspace/goPrj/src/ai-novel-h5/apps/api
GOCACHE=/tmp/novel-go-cache GOTMPDIR=/tmp go test ./internal/studio/app ./internal/studio/adapters
cd /Users/max/workspace/goPrj/src/ai-novel-h5
pnpm mvp:unit
pnpm --filter @novel/storefront typecheck
pnpm --filter @novel/storefront lint
```

Expected: all tests and static checks pass, with no changed chapter Final SHA and no H5 schema migration.

- [ ] **Step 5: Run browser verification at all required viewports**

Start the H5 services with `pnpm mvp:dev`, then use the existing Playwright setup to inspect the detail page at 320px, 390px, 768px, and 1440px in both day and night themes. Capture screenshots and assert no horizontal overflow, no overlap between story lead/actions/chapters, one hook and one blurb, hidden button for short text, visible button for measured long text, correct expansion labels, and chapters moving naturally below the expanded lead.

- [ ] **Step 6: Commit documentation and verification updates**

```bash
git -C /Users/max/workspace/goPrj/src/ai-novel-h5 add docs/mvp-cn/07-h5-storefront-reader.md docs/mvp-cn/ai-index.json
git -C /Users/max/workspace/goPrj/src/ai-novel-h5 commit -m "docs: record Novel OS story lead reader contract"
```

## Self-Review Checklist

- [ ] Schema, duplicate-key handling, language counters, exact quote checks, first-chapter copy checks, generic-hook findings, reader-pull gates, and spoiler checks are covered by Tasks 1 and 3.
- [ ] Opening/middle/late evidence, source fingerprints, Final/promotion authority, oversized source grouping, and stale-input invalidation are covered by Tasks 2 and 4.
- [ ] Writer/Guardian role reuse, provider/model hashes, repair bound, failed-response retention, and no-empty-fallback behavior are covered by Task 3.
- [ ] Markdown/HTML/EPUB/PDF/DOCX placement, EPUB description/language/navigation, block styling, word-count exclusion, and marker collision are covered by Task 5.
- [ ] H5 V1 fields, raw bytes, rune counts, finalization receipts, delivery binding, atomic immutable roots, short-book behavior, manifest roles, and ZIP determinism are covered by Task 6.
- [ ] Existing H5 importer acceptance without schema changes is covered by Task 7.
- [ ] H5 semantic order, one-copy rendering, measured overflow, 44px accessibility, keyboard behavior, responsive clamps, day/night themes, and Playwright viewports are covered by Task 8 and Task 9.
- [ ] No task performs historical backfill, synopsis generation, ad-data processing, chapter rewriting, or database migration.
- [ ] Every function named by a later task is defined by an earlier task or by the current repository interfaces mapped in the File Map.
