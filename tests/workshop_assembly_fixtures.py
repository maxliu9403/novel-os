"""Split an already-valid fixture into the bounded assembly transport."""
from copy import deepcopy
import re

PART_SECTIONS = {
    "publication": (1, 2, 4), "relationships_world": (6, 7),
    "secrets_payoffs": (8, 10), "reader_style": (11, 14),
    "cover_handoff": (15,), "delivery_review": (17,),
}


def parts_from_prompt(prompt, mode="short_novel"):
    headings = list(re.finditer(r"^##\s+(\d+)\.\s+[^\n]+", prompt, re.M))
    bodies = {int(item[1]): prompt[item.end():headings[i + 1].start() if i + 1 < len(headings) else len(prompt)].strip()
              for i, item in enumerate(headings)}
    header = prompt[:headings[0].start()]
    metadata = {key.lower(): re.search(rf"^{key}:([^\n]*)", header, re.M)[1].strip()
                for key in ("Title", "Author", "Genre", "Audience", "Language", "Tone", "POV", "Words")}
    metadata["words"] = int(metadata["words"])
    count = int(re.search(r"^Chapters:\s*(\d+)", header, re.M)[1])
    result = {}
    for part, sections in PART_SECTIONS.items():
        data = {"part_id": part, "narrative_mode": mode, "total_chapters": count,
                "sections": {str(n): bodies[n] for n in sections}}
        if part == "publication":
            data["metadata"] = deepcopy(metadata)
        result[part] = data
    return result
