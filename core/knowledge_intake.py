"""One frozen-selection intake for web handoffs and command-line book runs."""

import json
from pathlib import Path

from core.creative_execution import make_handoff
from core.creative_types import compile_snapshot
from core.novel_classification import parse_classification_block
from core.reader_investment import snapshot as reader_snapshot


def validate_source_selection(source: str, handoff: dict | None) -> None:
    selected = (handoff or {}).get("type_snapshot")
    classification = parse_classification_block(source)
    if selected is None:
        if classification is not None and classification.taxonomy is not None:
            raise ValueError(
                "动态分类骨架缺少冻结写作资料；请提供 knowledge.json，不会按名称猜测标签。"
            )
        return
    compile_snapshot(selected, "story_design")
    if (
        classification is None
        or classification.taxonomy != selected["publication_taxonomy"]
    ):
        raise ValueError("骨架发行分类与冻结 Genre / Tags 不一致，请重新确认骨架。")


def load_handoff(prompt_path: str, knowledge_path: str) -> dict:
    if prompt_path == "-":
        raise ValueError("--knowledge 需要已保存的完整骨架文件，不能使用 stdin。")
    path = Path(knowledge_path)
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("knowledge.json 必须是 2 MiB 以内的冻结选择快照。")
    knowledge = json.loads(path.read_text(encoding="utf-8"))
    source = Path(prompt_path).read_text(encoding="utf-8")
    handoff = make_handoff(
        source=source,
        origin={"kind": "cli_frozen_selection"},
        type_snapshot=knowledge,
        reader_investment=reader_snapshot(),
    )
    validate_source_selection(source, handoff)
    return handoff
