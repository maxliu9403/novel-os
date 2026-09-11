"""Build real EPUB/ZIP H5 fixtures without invoking any language/image provider.

Run: venv/bin/python scripts/build_h5_handoff_samples.py
The short fixture paragraphs and labeled PNGs are integration data, not a novel.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from PIL import Image, ImageDraw
from compile_book import gather, render_markdown
from compile_epub import render_epub
from core.cover_models import CoverCandidate, CoverConcept, CoverSet
from core.cover_models_v2 import CoverBriefV2
from core.delivery_package import build_delivery_package
from core.h5_import import digest, epub_index, object_digest
from core.narrative_format import infer_narrative_format, serialization_payload, volume_contract_template
from core.novel_classification import catalog_payload, infer_classification
from styles import StyleSheet


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_cover_set(project, title):
    def character(cid, gender):
        return dict(character_id=cid, name=cid.title(), narrative_role="protagonist" if cid == "claire" else "antagonist",
                    must_appear=True, age=50, age_band="", gender_presentation=gender,
                    physical_identity="natural skin texture", occupation_and_status="community teacher",
                    daily_wardrobe="practical wool coat", lived_environment="family kitchen",
                    current_emotional_state="controlled hurt", agency_signal="holds a letter",
                    relationships=["ethan" if cid == "claire" else "claire"])
    brief = CoverBriefV2.from_dict({
        "schema_version": 2, "title": title, "language": "English", "author": "Novel OS Test Fixture",
        "genre": "Women's Fiction / Revenge", "target_audience": "women around 50", "market_scope": "English H5 test",
        "core_task": "Claire decides how to confront a family secret.",
        "core_conflict": "Her spouse conceals the truth while asking her to preserve appearances.",
        "emotional_promise": "earned independence", "principal_characters": [character("claire", "woman"), character("ethan", "man")],
        "relationship_map": [{"from_character_id": "claire", "to_character_id": "ethan", "relationship": "spouses",
                              "power_balance": "he controls appearances; she holds the letter", "visible_tension": "she steps away",
                              "shared_risk": "their family learns the truth"}],
        "lived_environment": {"era": "present day", "fictional_place": "Wren Harbor", "primary_spaces": ["family kitchen"],
                              "economic_signals": ["repaired chair"], "cultural_signals": ["Sunday dinner"],
                              "weather_and_season": "autumn", "environment_truths": ["an occupied home"]},
        "decisive_story_nodes": [{"node_id": "letter", "description": "She opens the letter at the family table.", "evidence_refs": ["character:claire"]}],
        "secondary_signals": [{"signal_id": "chair", "description": "a moved chair", "story_function": "show separation"}],
        "genre_emotion_profile": {"primary_genre": "womens_fiction", "submode": "revenge", "emotional_temperature": "restrained",
                                  "desired_viewer_feeling": "anticipation", "relationship_motion": "separation", "prohibited_shortcuts": []},
        "commercial_visual_goal": {"market": "English", "audience_segment": "women 50", "display_context": "mobile_thumbnail",
                                   "thumbnail_reference_width": 120, "thumbnail_reference_height": 180, "first_glance_priority": "family rupture",
                                   "reader_identification": "a wife who acts", "truthful_story_promise": "she confronts the truth"},
        "title_direction": {"hierarchy": "large", "preferred_zone": "top", "readability": "mobile_thumbnail"},
        "forbidden_elements": [], "visual_assumptions": [],
    }, source_prompt_sha256=digest(b"h5-handoff-fixture"))
    concepts = tuple(CoverConcept(
        concept_id=f"concept-{n}", visual_strategy=f"Test layout {n}", focal_scene="Letter at the kitchen table",
        composition="portrait", palette=f"fixture palette {n}", secondary_signal="letter", title_treatment="large",
        generation_prompt=f"Integration fixture for {title}; layout {n}.",
    ) for n in range(1, 5))
    base = CoverSet.new("h5-handoff-fixture", brief, concepts)
    candidates = []
    for n, color in enumerate(("#163047", "#7B2D39", "#3C5948", "#605077"), 1):
        png = Image.new("RGB", (400, 600), color)
        draw = ImageDraw.Draw(png)
        draw.text((28, 60), f"H5 TEST COVER {n}\n\n80 CHAPTERS / 4 VOLUMES\n\nINTEGRATION FIXTURE", fill="white")
        buffer = BytesIO()
        png.save(buffer, format="PNG")
        raw = buffer.getvalue()
        relative = f"outputs/deliverables/covers/pending/cover-{n:02d}.png"
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        candidates.append(CoverCandidate(
            candidate_id=f"candidate-fixture-{n}", concept_id=f"concept-{n}", status="ready",
            relative_path=relative, media_id=f"fixture-media-{n}", sha256=digest(raw), width=400, height=600,
            content_type="image/png", provider="fixture", model="fixture", request_id=f"fixture-{n}",
            generation_prompt=concepts[n-1].generation_prompt,
            safe_request_parameters={"fixture": True}, created_at="2026-09-06T00:00:00Z",
        ))
    return replace(base, cover_set_id="cover-h5-fixture", candidates=tuple(candidates), status="ready",
                   created_at="2026-09-06T00:00:00Z", updated_at="2026-09-06T00:00:00Z")


def validate_sample(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("package-manifest.json"))
        assert manifest["package_revision_sha256"] == object_digest(manifest["files"])
        for entry in manifest["files"]:
            raw = archive.read(entry["path"])
            assert digest(raw) == entry["sha256"] and len(raw) == entry["size"]
        data = json.loads(archive.read(manifest["h5_import"]["path"]))
        classification = json.loads(archive.read(data["classification"]["path"]))
        serialization = json.loads(archive.read(data["serialization"]["path"]))
        index = epub_index(archive.read(data["epub"]["path"]), serialization, classification)
        assert index["chapter_count"] == 80
        assert data["chapters"][0]["epub"]["spine_index"] == 1
        assert data["chapters"][20]["chapter_in_volume"] == 1
        assert len(index["non_chapter_items"]) == 1
        assert data["import_revision_sha256"] == object_digest({"book_id": data["book_id"], "versions": data["versions"]})
        return data


def main():
    destination = ROOT / "docs/examples/h5-import"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="novel-h5-handoff-") as directory:
        project = Path(directory)
        title = "The Letter at Wren Harbor — H5 Integration Fixture"
        classification = infer_classification(genre="Women's Fiction / Family / Revenge", audience="women around 50",
                                             premise="A wife uncovers a betrayal.", tone="angst", chapters=80)
        form = infer_narrative_format(classification, chapters=80, target_words=72000, explicit_length=True,
                                     mode="multi_volume", volume_count=4).with_confirmation("author_selected")
        volumes = volume_contract_template(form)
        for volume in volumes:
            n = volume["volume_number"]
            volume.update(title=f"Volume {n}: Test Movement", central_conflict=f"Distinct test pressure {n}",
                          volume_promise=f"Test reader promise {n}", protagonist_shift=f"Test choice {n}",
                          payoff=f"Test consequence {n}", carryover_hook=f"Next test movement {n}" if n < 4 else "")
        serialization = serialization_payload(form, volumes)
        chapters = [{"number": n, "title": f"Chapter {n}: Test Choice", "text": (
            "## STORY_LEAD: Introduction\n\nThis is a technical import fixture. Claire finds a letter, confronts a family secret, "
            "and makes a choice. This introduction must not count as chapter one or consume a free-trial slot.\n\n" if n == 1 else ""
        ) + f"# Chapter {n}: Test Choice\n\nThis is story chapter {n} of 80, used to verify EPUB import. "
            f"Claire reads the letter and chooses her next step. The scene belongs to volume {(n-1)//20+1}.\n\n"
            "Fixture text includes an em dash — and a Unicode symbol 🌿 for encoding checks."
        } for n in range(1, 81)]
        book = gather(title=title, author="Novel OS Test Fixture", genre="Women's Fiction", chapters=chapters,
                      classification=classification.to_dict(), serialization=serialization)
        deliverables = project / "outputs/deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "book.epub").write_bytes(render_epub(book, StyleSheet()))
        (deliverables / "book.md").write_text(render_markdown(book, StyleSheet()), encoding="utf-8")
        write_json(project / "outputs/publication/novel-classification.json", classification.to_dict())
        write_json(project / "outputs/publication/novel-serialization.json", serialization)
        base = create_cover_set(project, title)
        imports = []
        for selected_number, name in ((1, "01-introduction-80-chapters-4-volumes.zip"), (2, "02-same-book-cover-only-update.zip")):
            chosen = base.candidates[selected_number-1]
            selected = replace(base, status="selected", revision=selected_number,
                               selected_candidate_id=chosen.candidate_id,
                               candidates=tuple(replace(c, status="selected" if c == chosen else "ready") for c in base.candidates))
            shutil.copyfile(project / chosen.relative_path, deliverables / "covers/selected-cover.png")
            result = build_delivery_package(project, cover_set=selected)
            path = destination / name
            shutil.copyfile(result.archive_path, path)
            imports.append(validate_sample(path))
            assert build_delivery_package(project).archive_path.read_bytes() == path.read_bytes()
            if selected_number == 1:
                for relative in ("meta/h5-import.json", "meta/novel-classification.json", "meta/novel-serialization.json", "covers/cover-set.json", "package-manifest.json"):
                    shutil.copyfile(deliverables / relative, destination / Path(relative).name)
        a, b = imports
        changed = [key for key in a["versions"] if a["versions"][key] != b["versions"][key]]
        assert set(changed) == {"selected_cover_sha256", "cover_metadata_sha256"}
        assert a["book_id"] == b["book_id"] and a["chapters"] == b["chapters"]
        assert a["import_revision_sha256"] != b["import_revision_sha256"]
        # A new project with equal text/structure has its own book identity.
        # Copy only publication assets; do not copy project_identity.json.
        other = project / "another-project"
        shutil.copytree(deliverables, other / "outputs/deliverables")
        shutil.copytree(project / "outputs/publication", other / "outputs/publication")
        other_result = build_delivery_package(other, cover_set=replace(selected, project_id="h5-second-book-fixture"))
        other_path = destination / "03-different-book-same-structure.zip"
        shutil.copyfile(other_result.archive_path, other_path)
        other_import = validate_sample(other_path)
        assert other_import["book_id"] != a["book_id"]
        assert other_import["versions"]["serialization_sha256"] == a["versions"]["serialization_sha256"]
        write_json(destination / "verification.json", {
            "verified": True, "book_id": a["book_id"], "chapter_count": 80, "volume_count": 4,
            "introduction_spine_index": 0, "chapter_one_spine_index": 1,
            "identical_epub_sha256": a["versions"]["epub_sha256"],
            "identical_content_sha256": a["versions"]["content_sha256"],
            "changed_components": changed, "import_revisions": [x["import_revision_sha256"] for x in imports],
            "different_book_id": other_import["book_id"], "same_structure_distinct_book_verified": True,
            "archives": [{"name": p.name, "size": p.stat().st_size, "sha256": digest(p.read_bytes())}
                         for p in sorted(destination.glob("*.zip"))],
        })
    catalog = catalog_payload()
    write_json(destination / "novel-classification-catalog.json", catalog)
    rows = ["# 分类标准字典", "", "目录版本：`" + catalog["catalog_version"] + "`。以下为完整标准 ID，推广指数不接入。", "",
            "| ID | 维度 | 中文 | English |", "|---|---|---|---|"]
    for axis, entries in catalog["axes"].items():
        for item in entries:
            rows.append(f"| `{item['id']}` | `{axis}` | {item['labels']['zh-CN']} | {item['labels']['en-US']} |")
    labels = {
        "audience_channels": {"female": ("女频", "Female"), "male": ("男频", "Male"), "general": ("综合", "General")},
        "age_bands": {"unknown": ("未指定", "Unknown"), "teen": ("青少年", "Teen"), "adult": ("成年", "Adult"), "midlife": ("中年", "Midlife")},
        "length_forms": {"unknown": ("未指定", "Unknown"), "short": ("短篇", "Short"), "long": ("长篇", "Long")},
        "chapter_bands": {"unknown": ("未指定", "Unknown"), "chapters_0_50": ("0—50章", "0–50 chapters"),
                          "chapters_51_100": ("51—100章", "51–100 chapters"), "chapters_101_150": ("101—150章", "101–150 chapters"),
                          "chapters_151_200": ("151—200章", "151–200 chapters"), "chapters_201_plus": ("201章及以上", "201+ chapters")},
    }
    for axis, values in labels.items():
        assert set(values) == set(catalog[axis])
        for key in catalog[axis]:
            zh, en = values[key]
            rows.append(f"| `{key}` | `{axis}` | {zh} | {en} |")
    (destination / "classification-dictionary.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
