"""Build a long book's global design in independently recoverable units.

The published global contract is the same one consumed by chapter planning,
review and the foundation gate. Partial roadmaps stay private candidates.
"""
from __future__ import annotations

from core.outline_planning import _json, content_hash
from core.planning_capacity import input_limit
from core.planning_errors import PlanningOutputIncomplete
from core.skeleton_projection import chapter_locations, global_source_index

VERSION = "chapter-roadmap.v1"
# Keep existing short-book requests stable. The narrative-format default also
# classifies books over 50 chapters as long. Range size is a wire bound only.
SHORT_BOOK_CHAPTERS = 50
MAX_RANGE = 8

ROADMAP_PROMPT = """STAGE: chapter_roadmap
Return one JSON object {chapter_map:[{number,purpose,incoming_consequence,
outgoing_commitment,source_anchor_ids:[],reserved_reveals:[],ending_obligations:[]}],
source_anchors:[{id,quote}]}. Cover exactly requested_chapters in published
chapter-number order, preserving causal chronology and deliberate flashbacks. Map each chapter
to the adopted source and the frozen global design. Preserve every author choice,
emotional qualification, character's knowledge, intentional open thread and volume
boundary. The previous_roadmap_entry is the actual saved boundary. Carry its
unresolved consequences into this range. Check earlier_obligations against their
intended deadlines and honor the finale and volume contracts. List delayed reveals
and ending obligations explicitly, using [] when none apply. Additional details
belong in the relevant chapter row. Do not repeat or edit the global story.

For scoped source views the supplied text contains all global sections and the
original requested and adjacent chapters. Read the accepted plot-wide decisions
from frozen_global_design and immutable_contracts. Cite exact source text with
stable IDs, reusing existing IDs or defining new IDs in source_anchors. Put a
chapter number in each new ID to avoid collisions with other ranges. Never
redefine existing quotations. Do not invent a citation or drop a qualifier."""


PROVISIONAL_RULES = """
The source window contains every global section, global extension, and exact chapter
title, with all detailed chapter bodies deferred. This seed is provisional. Preserve
explicit facts, qualify assumptions, and flag uncertainty in structural_risks. Do
not treat an event's title as evidence of its outcome. Every original chapter body
must subsequently be read against this seed before it can become a global design."""

CHECK_SEED = """
The saved seed used all global sections and chapter titles, but had no chapter
bodies. Compare every requested original chapter to that seed, including narrative
order, exceptions, identity, knowledge, and timing of disclosures. Include an
explicit seed_conflicts:[] only if consistent. For a material conflict, return
{seed_conflicts:[{chapter,message,source_quote}]} with the chapter number, a
specific contradiction and an exact original quotation. Never rewrite an author's
choice to fit a provisional seed."""


def _has_reply(planner, name: str) -> bool:
    return ((planner.cache / f"{name}-response.json").is_file()
            or any((planner.cache / "raw").glob(f"{name}.*.response.txt")))


def build_global_design(planner) -> dict:
    context = {**planner._context(), "volume_contract_template": planner.volume_template}
    seed_prompt = planner.global_seed_prompt
    seed_name = "global-seed-v1"
    # Bind the source selection before sending it. A subsequent larger budget
    # cannot replace a previously dispatched provisional seed or its roadmap.
    mode_name = "global-seed-source-v1"
    mode_deps = {"protocol": VERSION, "source": planner.identity["source_sha256"]}

    def validate_mode(value):
        if value not in ({"scope": "complete"}, {"scope": "global_sections_and_chapter_titles"}):
            raise ValueError("Invalid saved seed source scope")
        return value

    mode = planner._load(mode_name, mode_deps, validate_mode, strict_dependencies=True)

    def fits(instruction):
        prompt = instruction + "\n\n" + _json(context)
        return (planner._prompt_fits(prompt, "story_design", input_limit(planner.client))
                or planner._prompt_fits(prompt, "story_design", input_limit(planner.client, negotiate=True)))

    # Finish legacy replies under the original prompt. A short book keeps its
    # existing single request if it fits and no bounded workflow has started.
    if mode is None and (_has_reply(planner, "global")
                         or (planner.chapter_count <= SHORT_BOOK_CHAPTERS and fits(planner.global_prompt))):
        try:
            return planner._call("global", planner.global_prompt, context, planner._global,
                                 repair_fields=planner._repair_global_fields)
        except PlanningOutputIncomplete:
            # Both monolithic replies remain in the original namespace. No
            # complete candidate exists to reuse. Transition once to the seed
            # and smaller roadmap outputs under the persisted source scope.
            pass

    scoped = None
    if mode is None:
        has_seed = (planner.cache / f"{seed_name}.json").is_file() or _has_reply(planner, seed_name)
        if not has_seed and not fits(seed_prompt):
            scoped = global_source_index(planner.source, planner.chapter_count)
        mode = {"scope": scoped["mode"] if scoped else "complete"}
        planner._save(mode_name, mode_deps, mode)
    provisional = mode["scope"] != "complete"
    if provisional:
        scoped = scoped or global_source_index(planner.source, planner.chapter_count)
        if not scoped:
            raise ValueError("Saved global source scope no longer resolves to the frozen source")
        context.pop("complete_adopted_source")
        context.get("context_representation", {}).update(source_contracts_in_immutable_contracts=[])
        context["adopted_source_projection"] = scoped
        seed_prompt = seed_prompt.replace("the COMPLETE adopted skeleton", "the supplied original global sections and chapter titles")
        seed_prompt = seed_prompt.replace("across the complete source", "in the visible source")
        seed_prompt += PROVISIONAL_RULES
    dependencies = {"protocol": VERSION, "prompt": content_hash(seed_prompt), "context": content_hash(context)}

    def validate_seed(value):
        if "roadmap_source_coverage" in value:
            raise ValueError("Roadmap source coverage is assigned after all chapter spans have been checked")
        story = planner._global_seed(value)
        if scoped and any(anchor["quote"] not in scoped["text"] for anchor in story["source_anchors"]):
            raise ValueError("Provisional seed must quote the visible global source and title index")
        return story

    seed = planner._load(seed_name, dependencies, validate_seed, strict_dependencies=True)
    if seed is None:
        seed = planner._call(seed_name, seed_prompt, context, validate_seed,
                             strict_dependencies=True, recover_archived=True)
        planner._save(seed_name, dependencies, seed)
    planner._progress("global_design", "running", "已保存全书人物、主线与结局，正在按段规划章节走向",
                      milestone="global_seed_saved")
    roadmap = _Roadmap(planner, seed, provisional=provisional)
    for volume in seed["narrative_format"]["volumes"]:
        for start in range(volume["chapter_start"], volume["chapter_end"] + 1, MAX_RANGE):
            stop = min(start + MAX_RANGE, volume["chapter_end"] + 1)
            roadmap.add(list(range(start, stop)))
    assembled = {**seed, "chapter_map": roadmap.rows, "source_anchors": roadmap.anchors}
    if provisional:
        assembled["roadmap_source_coverage"] = {
            "schema": "roadmap-source-coverage.v1", "source_sha256": scoped["source_sha256"],
            "seed_view": scoped["mode"], "covered_chapters": len(roadmap.rows),
            "coverage": "Every requested original chapter body was supplied for roadmap comparison against the seed."
        }
    return planner._global(assembled)


class _Roadmap:
    def __init__(self, planner, seed: dict, *, provisional: bool):
        self.planner = planner
        self.provisional = provisional
        self.locations = chapter_locations(planner.source, planner.chapter_count) if provisional else {}
        self.instruction = ROADMAP_PROMPT + (CHECK_SEED if provisional else "")
        self.seed_hash = content_hash(seed)
        # Every excluded contract is supplied verbatim by planner._context().
        self.story = {key: value for key, value in seed.items() if key not in planner.locked}
        self.rows: list[dict] = []
        self.anchors = list(seed["source_anchors"])
        self.limit = input_limit(planner.client)
        self.negotiated = False

    def _context(self, numbers: list[int]) -> dict:
        # An obligation can depend on an exception in the same row. Carry its
        # entire source-bound entry, including additional author/model fields.
        prior_obligations = [row for row in self.rows if row["reserved_reveals"] or row["ending_obligations"]]
        return {**self.planner._context(numbers, project_source=True),
                "requested_chapters": numbers,
                "frozen_global_design": {**self.story, "source_anchors": self.anchors},
                "previous_roadmap_entry": self.rows[-1] if self.rows else None,
                "earlier_obligations": prior_obligations}

    def _fits(self, context: dict) -> bool:
        prompt = self.instruction + "\n\n" + _json(context)
        if self.planner._prompt_fits(prompt, "story_design", self.limit):
            return True
        if not self.negotiated:
            self.limit = input_limit(self.planner.client, negotiate=True)
            self.negotiated = True
        return self.planner._prompt_fits(prompt, "story_design", self.limit)

    def _validate(self, value: dict, numbers: list[int]) -> dict:
        if self.provisional:
            conflicts = value.get("seed_conflicts")
            if not isinstance(conflicts, list):
                raise ValueError("Provisional roadmap requires explicit seed_conflicts")
            for issue in conflicts:
                if (not isinstance(issue, dict) or type(issue.get("chapter")) is not int
                        or issue["chapter"] not in numbers or not isinstance(issue.get("message"), str)
                        or not issue["message"].strip() or not isinstance(issue.get("source_quote"), str)
                        or not issue["source_quote"].strip()):
                    raise ValueError("Provisional seed conflict needs a requested chapter, message, and exact source quotation")
                start, end = self.locations[issue["chapter"]]["source_chars"]
                if issue["source_quote"] not in self.planner.source[start:end]:
                    raise ValueError(f"Provisional conflict quotation must occur in cited chapter {issue['chapter']}")
            if conflicts:
                return {"seed_conflicts": conflicts}
            value = {key: item for key, item in value.items() if key != "seed_conflicts"}
        if set(value) != {"source_anchors", "chapter_map"} or not isinstance(value["source_anchors"], list):
            raise ValueError("Roadmap response requires only chapter_map and source_anchors arrays")
        supplied = value["source_anchors"]
        self.planner._validate_source_anchors(supplied)
        known = {anchor["id"]: anchor for anchor in self.anchors}
        for anchor in supplied:
            identifier = anchor["id"]
            if identifier in known and known[identifier] != anchor:
                raise ValueError(f"Roadmap cannot redefine frozen source anchor {identifier}")
            known[identifier] = anchor
        merged = list(known.values())
        rows = value["chapter_map"]
        self.planner._validate_roadmap(rows, merged, numbers)
        for row in rows:
            for key in ("reserved_reveals", "ending_obligations"):
                if not isinstance(row.get(key), list):
                    raise ValueError(f"Roadmap chapter {row['number']} requires an explicit {key} list")
        # bind_global may have added deterministic chapter-source locators.
        return {**({"seed_conflicts": []} if self.provisional else {}),
                "chapter_map": sorted(rows, key=lambda row: row["number"]),
                "source_anchors": merged[len(self.anchors):]}

    def add(self, numbers: list[int]) -> None:
        planner = self.planner
        name = f"roadmap-v1-{numbers[0]:03d}-{numbers[-1]:03d}"
        dependencies = {"seed": self.seed_hash, "prior_rows": content_hash(self.rows),
                        "prior_anchors": content_hash(self.anchors), "range": numbers,
                        "protocol": VERSION, "instruction": content_hash(self.instruction)}

        def validate(value):
            return self._validate(value, numbers)

        chunk = planner._load(name, dependencies, validate, strict_dependencies=True)
        if chunk is None:
            middle = len(numbers) // 2
            split = {"left": numbers[:middle], "right": numbers[middle:]}

            def validate_split(value):
                if not middle or value != split:
                    raise ValueError("Roadmap division does not preserve its frozen chapter range")
                return value

            saved_split = planner._load(name + "-split", dependencies, validate_split, strict_dependencies=True)
            if saved_split is None:
                context = self._context(numbers)
                if middle and not _has_reply(planner, name) and not self._fits(context):
                    saved_split = split
                else:
                    try:
                        chunk = planner._call(name, self.instruction, context, validate,
                                              strict_dependencies=True, recover_archived=True)
                    except PlanningOutputIncomplete:
                        # Neither archived reply is a complete JSON candidate.
                        # Reduce output size while retaining the same original
                        # source, accepted seed, boundary and obligations.
                        if not middle:
                            raise
                        saved_split = split
                    else:
                        planner._save(name, dependencies, chunk)
                if saved_split is not None:
                    planner._save(name + "-split", dependencies, saved_split)
            if saved_split is not None:
                # Once a subrange exists, expanding the adapter budget must
                # never rerun it inside a larger model request after restart.
                self.add(saved_split["left"])
                self.add(saved_split["right"])
                return
        if chunk.get("seed_conflicts"):
            issue = chunk["seed_conflicts"][0]
            raise ValueError(f"Provisional story conflicts with the adopted source at chapter {issue['chapter']}: "
                             f"{issue['message']} (source: {issue['source_quote']}). The candidate is preserved.")
        self.rows.extend(chunk["chapter_map"])
        self.anchors.extend(chunk["source_anchors"])
        planner._progress("global_design", "running",
                          f"已保存第 {numbers[0]}–{numbers[-1]} 章走向，累计 {len(self.rows)}/{planner.chapter_count} 章")
