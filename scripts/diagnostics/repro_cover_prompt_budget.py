"""Read-only replay of all prompts in a saved cover direction (no model calls)."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.cover_models_v2 import ArtDirectionSet, CoverBriefV2
from core.cover_prompt_compiler import (
    COMPILER_VERSION, MAX_PROMPT_CODEPOINTS, _modules, _render, compile_cover_prompt,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", type=Path)
    args = parser.parse_args()
    direction = ArtDirectionSet.from_dict(json.loads(args.direction.read_text()))
    brief_path = args.direction.with_suffix(".brief.json")
    data = json.loads(brief_path.read_text())
    brief = CoverBriefV2.from_dict(data, source_prompt_sha256=data["source_prompt_sha256"])
    context = dict(
        visual_identity=direction.visual_identity,
        evidence_ledger=direction.evidence_ledger,
        conflict_contract=direction.core_conflict_visual_contract,
    )
    rows = []
    for scene in direction.plans:
        row = {"concept_id": scene.concept_id,
               "raw_codepoints": len(_render(_modules(brief, scene, **context)))}
        try:
            compiled = compile_cover_prompt(brief, scene, **context)
            assert len(compiled.text) <= MAX_PROMPT_CODEPOINTS
            assert compiled.text.count(brief.title) == 1
            for character in brief.principal_characters:
                if character.character_id in scene.cast:
                    assert f"{character.character_id} ({character.name})" in compiled.modules["CAST LOCK"]
            row.update(status="pass", compiled_codepoints=len(compiled.text))
        except (ValueError, AssertionError) as exc:
            row.update(status="fail", error=str(exc))
        rows.append(row)
    print(json.dumps({
        "compiler_version": COMPILER_VERSION, "results": rows,
        "direction_sha256": hashlib.sha256(args.direction.read_bytes()).hexdigest(),
        "brief_sha256": hashlib.sha256(brief_path.read_bytes()).hexdigest(),
    }, ensure_ascii=False, indent=2))
    return 0 if all(row["status"] == "pass" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
