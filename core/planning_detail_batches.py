"""Finish an existing chapter batch in smaller checked units when needed."""
from core.planning_capacity import input_limit
from core.planning_errors import PlanningInputTooLarge, PlanningOutputIncomplete
from core.outline_planning import _json, _PROJECTION_RULES, content_hash


def _oversized_new_request(planner, name, instruction, context):
    if "complete_adopted_source" not in context:
        return False
    prompt = instruction + "\n\n" + _json(context)
    if planner._prompt_fits(prompt, "chapter_plan", input_limit(planner.client)):
        return False
    from core.planning_batch_references import has_dispatched
    if has_dispatched(planner, name):
        return False
    return not planner._prompt_fits(prompt, "chapter_plan", input_limit(planner.client, negotiate=True))


def build_batch(planner, name, numbers, global_design, prior, dependencies):
    # Root aggregate names/dependencies predate bounded subranges and remain
    # readable by old checkpoints and by downstream stages.
    middle = len(numbers) // 2
    partition = {"left": numbers[:middle], "right": numbers[middle:]}
    split_deps = {**dependencies, "requested": numbers, "protocol": "detail-bisection.v1"}

    def check_split(value):
        if not middle or value != partition:
            raise ValueError("Invalid saved chapter batch partition")
        return value

    manifest_name = name + "-split"
    split = planner._load(manifest_name, split_deps, check_split, strict_dependencies=True)
    if split is None:
        from core import planning_batch_references as binding
        instruction = binding.instruction(planner, name, dependencies, numbers, planner.batch_prompt, global_design)
        context = {**planner._context(numbers), "frozen_global_design": global_design, "requested_chapters": numbers,
                   "previous_completed_chapters": prior[-2:]}
        # A previously dispatched legacy batch keeps its original request. Only
        # select a new source view when the complete original does not fit.
        scope = name + "-source-view"
        def check_view(value):
            if value != {"scope": "requested_and_adjacent"}:
                raise ValueError("Invalid saved detail batch source view")
            return value
        scoped = planner._load(scope, split_deps, check_view, strict_dependencies=True)
        if scoped or _oversized_new_request(planner, name, instruction, context):
            source_view = planner._context(numbers, project_source=True)
            if "adopted_source_projection" in source_view:
                context.pop("complete_adopted_source", None)
                context.update(source_view)
                if _PROJECTION_RULES not in instruction:
                    instruction = instruction.replace("full original skeleton", "supplied original skeleton") + _PROJECTION_RULES
                if not scoped:
                    planner._save(scope, split_deps, check_view({"scope": "requested_and_adjacent"}))
            elif scoped:
                raise ValueError("Saved detail batch source view no longer resolves to the author's source")
        try:
            return binding.call(planner, name, instruction, context, numbers, global_design, dependencies)
        except PlanningInputTooLarge as exc:
            if exc.stage_name != name or not middle:
                raise
        except PlanningOutputIncomplete:
            if not middle:
                raise
        planner._save(manifest_name, split_deps, partition)

    chapters = []
    root = name.split("-part-", 1)[0]
    for subset in partition.values():
        history = prior + chapters
        child = f"{root}-part-{subset[0]:03d}-{subset[-1]:03d}"
        deps = {"global": dependencies["global"], "previous": content_hash(history), "requested": subset}
        saved = planner._load(child, deps, lambda value: planner._batch(value, subset, global_design),
                              strict_dependencies=True)
        if saved is None:
            saved = build_batch(planner, child, subset, global_design, history, deps)
            planner._save(child, deps, saved)
        chapters.extend(saved["chapters"])
    # Validate combined order, execution contracts and frozen references again.
    return planner._batch({"chapters": chapters}, numbers, global_design)
