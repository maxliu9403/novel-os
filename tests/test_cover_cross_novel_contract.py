"""Cross-book operational contracts; mock model replies never claim creative QA."""
from copy import deepcopy
import json

import pytest

from core.cover_analysis import CoverStoryAnalyzer, derive_analysis_context
from core.cover_campaign_transport import decode_evidence_registry
from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source
from core.cover_store import CoverConflict, CoverStore
from core.cover_validator import validate_direction
from core.cover_workflow import CoverWorkflow, digest
from tests.cover_review_fixtures import supported_review
from tests.test_cover_analysis_snapshot import ModelFixture
from tests.test_cover_campaign import context as campaign_context
from tests.test_cover_models_v2 import two_character_fixture


BOOKS = [
    dict(key="hearth", title="Home", language="English", genre="contemporary romance",
         names=("Ada", "Ben"), places=("apartment entry", "courtyard"), title_mode="composed"),
    dict(key="mist", title="长夜尽头，守塔人归还了那枚能够开启失落王城的钥匙", language="Chinese", genre="fantasy",
         names=("林雾", "闻舟"), places=("古塔石门", "王城外庭")),
    dict(key="orbit", title="The Last Technician at the Airlock That Never Opened Until the Station Went Dark",
         language="English", genre="science fiction", names=("Iris", "Sol"), places=("station airlock", "observation deck")),
    dict(key="archive", title="雾门", language="Chinese", genre="mystery",
         names=("周宁", "沈岩"), places=("档案室门口", "钟楼台阶")),
]


def substitute(value, replacements):
    if isinstance(value, dict):
        return {key: substitute(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
    return value


def prepare_book(root, book):
    project = root / book["key"]
    (project / "outputs/input").mkdir(parents=True)
    ids = ["char_" + book["key"] + suffix for suffix in ("_first", "_second")]
    replacements = dict(zip(("char_mara", "char_oren", "Mara", "Oren"), (*ids, *book["names"])))
    brief = substitute(two_character_fixture(), replacements)
    brief.update({key: book[key] for key in ("title", "language", "genre")})
    brief["genre_emotion_profile"]["primary_genre"] = book["genre"]
    for character, name in zip(brief["principal_characters"], book["names"]):
        character["name"] = name
    # Each book contains source-owned publication metadata and distinct full story bytes.
    moments = [f"chapter-{i + 1}: {book['places'][i % 2]}. {book['names'][0]} removes the shared key; "
               f"{book['names'][1]}'s reaching hand stops. Access is refused."
               for i in range(4)]
    text = (f"# {book['title']}\nTitle: {book['title']}\nLanguage: {book['language']}\n"
            f"Genre: {book['genre']}\n\n## Story\n" + "\n\n".join(moments)
            + "\n\nCOVER_HANDOFF_BEGIN\n" + json.dumps(brief, ensure_ascii=False)
            + "\nCOVER_HANDOFF_END\n")
    (project / "outputs/input/prompt.md").write_text(text, encoding="utf-8")
    store = CoverStore(project)
    context = CoverSourceContext(store.root, f"workspace-{book['key']}:project:{book['key']}", lambda _: project)
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id=book["key"]))
    model = ModelFixture()
    def replies(request, result):
        result = substitute(result, replacements)
        if request["phase"] == "global":
            result["brief"] = deepcopy(brief)
            template = result["events"][0]
            result["events"] = [{**template, "event_id": f"event-{i}", "epoch": f"chapter-{i + 1}",
                "location": book["places"][i % 2]} for i in range(4)]
        return result
    model.mutate = replies
    analysis = CoverStoryAnalyzer(complete=model, model="offline-contract-fixture", max_repair_attempts=0).analyze(context, snapshot)
    actual_brief, ledger = derive_analysis_context(context, analysis)
    payload = substitute(campaign_context()[2], replacements)
    def source_refs(value):
        if isinstance(value, dict):
            return {key: source_refs(item) for key, item in value.items()}
        if isinstance(value, list):
            return [source_refs(item) for item in value]
        return ledger.allowed_refs[0] if isinstance(value, str) and value.startswith("evidence:") else value
    payload = source_refs(payload)
    for index, plan in enumerate(payload["plans"]):
        plan["story_evidence_refs"] = list(ledger.allowed_refs)
        campaign = plan["campaign"]
        campaign["layers"][0]["location"] = book["places"][index % 2]
        # Exercise English composition and exact native titles; native CJK does
        # not pretend to prove local font glyph coverage or generated-image QA.
        campaign["title_render_mode"] = book.get("title_mode", "native")
        campaign["title_layout"].update(title=book["title"], lines=[book["title"]], language=book["language"])
    # A real cross-time, cross-place composite: two source events, no fake co-presence.
    campaign = payload["plans"][1]["campaign"]
    earlier = deepcopy(payload["plans"][0]["campaign"]["layers"][0])
    earlier.update(layer_id="earlier", region=[0.05, 0.05, 0.45, 0.4], visual_weight=0.4)
    campaign["layers"][1] = earlier
    campaign["event_ids"].append("event-0")
    campaign["read_order"] = ["moment", "earlier"]
    campaign["layer_relationships"] = [{"from_layer": "earlier", "to_layer": "moment",
        "relationship": "visual_echo", "temporal_relationship": "cross_time",
        "truth_boundary": "Separate events at different times and places; neither is physically present in the other.",
        "evidence_refs": [ledger.allowed_refs[0]]}]
    return {"book": book, "project": project, "flow": CoverWorkflow(context, store), "snapshot": snapshot,
            "analysis": analysis, "brief": actual_brief, "ledger": ledger, "payload": payload, "ids": set(ids)}


@pytest.mark.parametrize("books", [BOOKS[:2], BOOKS[2:]], ids=["romance-en-short_fantasy-zh-long", "scifi-en-long_mystery-zh-short"])
def test_distinct_novels_share_director_but_never_source_cast_recipes_or_approval(tmp_path, books):
    prepared = [prepare_book(tmp_path, book) for book in books]
    calls = []
    def complete(system, user):
        request = json.loads(user)
        frozen = request["frozen_source_context"]
        # Select the fixture reply by the request's actual source identity, not global mutable state.
        current = next(item for item in prepared if item["snapshot"].snapshot_id == frozen["snapshot_id"])
        other = next(item for item in prepared if item is not current)
        assert frozen["owner_scope"] == current["snapshot"].owner_scope
        assert frozen["documents"][0]["text"].encode() == current["snapshot"].primary_bytes
        assert other["snapshot"].snapshot_id not in user
        assert all(character_id not in user for character_id in other["ids"])
        spans = frozen["evidence_spans"]
        if isinstance(spans, dict):
            assert spans["registry"] == "/cover_evidence_registry" and spans["rows"] == "all"
            registry = request["cover_evidence_registry"]
            # Check all original evidence belongs to this book before applying
            # the documented fields view; transport must not weaken isolation.
            assert decode_evidence_registry(registry, original=True) == current["analysis"].evidence
            spans = [{key: row[key] for key in spans["fields"] if key in row}
                     for row in decode_evidence_registry(registry)]
            selected = request.get("selected_verified_evidence")
            if isinstance(selected, dict):
                assert selected["registry"] == "/cover_evidence_registry"
                assert selected["original_records"] is True
                expected_refs = sorted("evidence:" + row["evidence_id"] for row in current["analysis"].evidence)
                assert selected["refs"] == expected_refs
        assert len(spans) == len(current["analysis"].evidence)
        for span in spans:
            evidence = next(row for row in current["analysis"].evidence if "evidence:" + row["evidence_id"] == span["evidence_ref"])
            assert current["snapshot"].document_bytes(span["document_id"])[span["byte_start"]:span["byte_end"]].decode() == evidence["exact_quote"]
        calls.append((current["book"]["key"], request["task"]))
        return json.dumps(supported_review(request)) if request["task"] == "review_campaign_semantics" else json.dumps(current["payload"], ensure_ascii=False)
    shared = CoverArtDirector(complete=complete, model="offline-director", image_size="1024x1536")
    approved = []
    for current in prepared:
        flow, analysis, book = current["flow"], current["analysis"], current["book"]
        analysis_file = flow.context.cover_root / "analyses" / analysis.analysis_id / "analysis.json"
        before = analysis_file.read_bytes()
        direction = flow.plan(analysis.analysis_id, shared)
        assert analysis_file.read_bytes() == before
        assert direction.source_binding["owner_scope"] == flow.context.owner_scope
        assert validate_direction(current["brief"], direction, story_analysis=analysis.to_dict()) == ()
        assert current["brief"].title == book["title"] and current["brief"].language == book["language"]
        assert current["brief"].genre == book["genre"]
        assert direction.plans[0].campaign["composition_mode"] == "scene"
        composite = direction.plans[1].campaign
        assert composite["layer_relationships"][0]["temporal_relationship"] == "cross_time"
        assert len({layer["epoch"] for layer in composite["layers"]}) == 2
        assert len({layer["location"] for layer in composite["layers"]}) == 2
        for recipe in direction.compiled_recipes.values():
            assert recipe["recipe_sha256"] == digest({key: value for key, value in recipe.items() if key != "recipe_sha256"})
            assert recipe["title_layout"]["title"] == book["title"]
        with pytest.raises(CoverConflict):
            flow.generation_arguments(direction.direction_id, direction.direction_sha256)
        flow.approve(direction.direction_id, expected_brief_sha256=direction.brief_sha256,
            approved_direction_sha256=direction.direction_sha256, confirm_source=True, extra_image_attempts=0)
        ready_brief, concepts, options = flow.generation_arguments(direction.direction_id, direction.direction_sha256)
        assert ready_brief.title == book["title"] and len(concepts) == 4
        assert options["extra_image_attempts"] == 0
        assert flow.approval(direction.direction_id)["initial_image_calls"] == 4
        assert [item.generation_prompt for item in concepts] == [direction.compiled_recipes[item.concept_id]["image_prompt"] for item in concepts]
        with pytest.raises(CoverConflict, match="额度"):
            flow.generation_arguments(direction.direction_id, direction.direction_sha256)
        with pytest.raises(CoverConflict, match="replenished"):
            flow.approve(direction.direction_id, expected_brief_sha256=direction.brief_sha256,
                approved_direction_sha256=direction.direction_sha256, confirm_source=True, extra_image_attempts=1)
        approved.append(direction)
    assert set(key for key, task in calls if task == "review_campaign_semantics") == {book["key"] for book in books}
    assert approved[0].source_binding["source_content_sha256"] != approved[1].source_binding["source_content_sha256"]
    assert approved[0].direction_sha256 != approved[1].direction_sha256
    # An approved recipe cannot cross owners or be rebound to a different live source.
    with pytest.raises((CoverConflict, ValueError, FileNotFoundError)):
        prepared[1]["flow"].validate(approved[0].direction_id, require_approval=True)
    first = prepared[0]
    (first["project"] / "outputs/input/prompt.md").write_text("changed source", encoding="utf-8")
    with pytest.raises(CoverConflict, match="骨架已变化"):
        first["flow"].validate(approved[0].direction_id, require_approval=True)
    prepared[1]["flow"].validate(approved[1].direction_id, require_approval=True)


@pytest.mark.parametrize("book", [BOOKS[1], BOOKS[2]], ids=["chinese-fantasy", "english-scifi"])
def test_cross_time_composite_cannot_pass_as_one_simultaneous_encounter(tmp_path, book):
    current = prepare_book(tmp_path, book)
    payload = deepcopy(current["payload"])
    payload["plans"][1]["campaign"]["layer_relationships"][0]["temporal_relationship"] = "same_moment"
    calls = []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        assert request["task"] != "review_campaign_semantics"  # Mechanical contradiction blocks before semantic approval.
        return json.dumps(payload, ensure_ascii=False)
    with pytest.raises(CoverDirectionError, match="campaign_false_simultaneity"):
        current["flow"].plan(current["analysis"].analysis_id, CoverArtDirector(complete=complete))
    assert len(calls) == 3  # Existing repair allowance; no added calls or image generation.
    assert not list((current["flow"].store.root / "approvals").glob("*.json"))
