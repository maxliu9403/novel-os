"""Compile saved cover plans with a Git baseline and working-tree compiler.

No model calls, checkout, service changes, or writes to project artifacts.
JSON output includes exact compiled prompts for inspection, not image-quality scores.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


WORKER = r'''
import sys, json, inspect
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root), str(root / "core")]
from core.cover_models_v2 import CoverBriefV2, CoverScenePlan
from core.cover_design import BookVisualIdentity, VisualEvidenceLedger
from core import cover_prompt_compiler as c
rows = []
for filename in sys.argv[2:]:
    data = json.loads(Path(filename).read_text())
    raw_brief = data["brief"]
    brief = CoverBriefV2.from_dict(raw_brief, source_prompt_sha256=raw_brief["source_prompt_sha256"])
    for index, concept in enumerate(data["concepts"], 1):
        payload = concept["scene_plan"]
        scene = CoverScenePlan.from_dict(payload)
        context = dict(
            visual_identity=BookVisualIdentity.from_dict(payload["_visual_identity"]),
            evidence_ledger=VisualEvidenceLedger.from_dict(payload["_evidence_ledger"]),
        )
        if "conflict_contract" in inspect.signature(c.compile_cover_prompt).parameters:
            from core.cover_models_v2 import CoreConflictVisualContract
            conflict = payload.get("_core_conflict_visual_contract")
            context["conflict_contract"] = CoreConflictVisualContract.from_dict(conflict) if conflict else None
        raw = c._modules(brief, scene, **context)
        row = dict(set_id=data["cover_set_id"], candidate=index, art_style=scene.art_style,
                   raw_chars=len(c._render(raw)), cast_count=len(scene.cast))
        try:
            compiled = c.compile_cover_prompt(brief, scene, **context)
            row.update(
                status="compiled", compiled_chars=len(compiled.text),
                lighting_preserved=scene.motivated_lighting in compiled.text,
                color_script_preserved=scene.color_script in compiled.text,
                matches_saved_candidate=compiled.text == data["candidates"][index - 1]["generation_prompt"],
                modules={name: {"raw": len(raw[name]), "compiled": len(body)}
                         for name, body in compiled.modules.items()},
                text=compiled.text,
            )
            codes = ["core_conflict_missing", "causal_relationship_missing", "protagonist_action_missing", "title_failure"]
            if set(codes).issubset(c._REPAIR_MODULES):
                repaired = c.compile_repair_prompt(brief, scene, compiled, codes, **context)
                row["repair"] = {
                    "codes": codes, "compiled_chars": len(repaired.text),
                    "lighting_preserved": scene.motivated_lighting in repaired.text,
                    "color_script_preserved": scene.color_script in repaired.text,
                    "focuses_preserved": all("Repair focus: " + code.replace("_", " ") + "." in repaired.text for code in codes),
                }
            if "HUMAN PERFORMANCE CONTRACT" in compiled.modules:
                natural = c.compile_repair_prompt(brief, scene, compiled,
                    ["generic_ai_face", "weak_story_action"], **context)
                row["naturalness_repair"] = {
                    "compiled_chars": len(natural.text),
                    "performance_preserved": compiled.modules["HUMAN PERFORMANCE CONTRACT"] in natural.modules["HUMAN PERFORMANCE CONTRACT"],
                    "lighting_preserved": scene.motivated_lighting in natural.text,
                    "color_script_preserved": scene.color_script in natural.text,
                }
        except ValueError as error:
            row.update(status="rejected", error=str(error))
        rows.append(row)
print(json.dumps({"compiler": c.COMPILER_VERSION, "cases": rows}, ensure_ascii=False))
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="c79f1df40338a9561cd6cca892e0f103663de8db")
    parser.add_argument("sets", nargs="+", type=Path)
    parser.add_argument("--assert-visual-preservation", action="store_true",
                        help="Fail if current photographic plans or repairs lose lights/colors or exceed 12000")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    # Freeze inputs in temporary storage so concurrent candidate status updates
    # cannot change one side of the comparison while the other is compiling.
    sources = [(path.resolve(), path.read_bytes()) for path in args.sets]
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{args.baseline}^{{commit}}"], cwd=repository, text=True,
    ).strip()
    report = {
        "baseline_commit": commit,
        "mode": "same_saved_plans_compiled_in_isolated_code_versions_no_model_calls",
        "sources": [{"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}
                    for path, data in sources],
    }
    with tempfile.TemporaryDirectory(prefix="cover-commit-audit-") as directory:
        root = Path(directory)
        snapshots = []
        for index, (_, data) in enumerate(sources):
            path = root / f"set-{index}.json"
            path.write_bytes(data)
            snapshots.append(str(path))
        baseline = root / "baseline"
        baseline.mkdir()
        archive = subprocess.check_output(["git", "archive", commit, "core"], cwd=repository)
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(baseline, filter="data")
        for label, code_root in (("baseline", baseline), ("current", repository)):
            result = subprocess.run(
                [sys.executable, "-c", WORKER, str(code_root), *snapshots],
                cwd=root, capture_output=True, text=True, check=True,
            )
            report[label] = json.loads(result.stdout)
    if args.assert_visual_preservation:
        cases = report["current"]["cases"]
        verified = bool(cases) and all(
            row["status"] == "compiled"
            and row["compiled_chars"] <= 12000
            and row["lighting_preserved"] and row["color_script_preserved"]
            and row.get("repair", {}).get("compiled_chars", 12001) <= 12000
            and row.get("repair", {}).get("lighting_preserved", False)
            and row.get("repair", {}).get("color_script_preserved", False)
            and row.get("repair", {}).get("focuses_preserved", False)
            for row in cases
        )
        report["visual_contract_verified"] = verified
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.assert_visual_preservation and not verified:
        raise SystemExit("Current photographic-plan visual preservation check failed")


if __name__ == "__main__":
    main()
