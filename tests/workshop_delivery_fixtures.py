"""Complete staged delivery artifacts for offline workshop protocol tests."""
import json


def story_design(count=21, mode="short_novel"):
    return {"narrative_mode": mode, "total_chapters": count, "story_design": {
        "premise": "母亲保护女儿并重新夺回自主生活。",
        "characters": "母亲以保留证据逐渐取代讨好；女儿拒绝以沉默换取关爱。",
        "causal_chain": "隐瞒被发现→母亲核实证据→丈夫改变解释→女儿拒绝配合→母亲停止资助。",
        "ending": "母女选择保留自己的生活，父亲承担失信的关系后果。",
        "decisions": "作者确认保持家庭关系；以证据和选择推动结局，无突发惩罚。",
    }}


def chapter_plan(count=21, mode="short_novel"):
    return {"narrative_mode": mode, "total_chapters": count, "chapters": [
        {"number": n, "title": f"Choice {n}", "plan": f"母亲视角；第 {n} 章新证据触发下一步选择，推进母女关系并回收早期线索。"}
        for n in range(1, count + 1)]}


def stage_response(stage, count=21, mode="short_novel"):
    return json.dumps(story_design(count, mode) if stage == "story_design" else chapter_plan(count, mode), ensure_ascii=False)
