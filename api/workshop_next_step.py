"""Read-only workshop navigation and explicit, scoped progress operations.

An absent author question does not confirm a length allocation. Likewise an
unavailable companion cannot force the author to invent another story change.
"""
from . import workshop_capacity, workshop_story


def project(session: dict) -> dict:
    design = session.get('creative_design') or {}
    pending = (design.get('story_state') or {}).get('pending')
    status = session.get('status')
    capacity = session.get('capacity') or {}
    guidance = ((design.get('rounds') or [{}])[-1].get('guidance') or {})
    enabled = workshop_capacity.enabled(session)
    unavailable = status in {'running','launched','paused','error'} or session.get('deleted_at') or session.get('restart_required')
    can_continue = bool(not unavailable and not pending and workshop_story.enabled(design))
    can_plan = bool(not unavailable and not pending and enabled)
    capacity_ready = not enabled or (capacity.get('status') == 'confirmed' and bool(capacity.get('confirmed')))
    can_deliver = bool(not unavailable and not pending and capacity_ready and session.get('input',{}).get('premise','').strip())
    reason = None
    if status in {'paused', 'error'}:
        reason = '请先恢复未完成的推演，保留已有阶段和篇幅确认。'
    elif unavailable:
        reason = '请等待当前任务完成。' if status == 'running' else '请先处理当前会话状态。'
    elif pending:
        reason = '请先决定当前故事走向，再确定篇幅并生成骨架。'
    elif not capacity_ready:
        reason = '生成骨架前需要确认篇幅方案；无需额外输入故事想法。'
    elif not can_deliver:
        reason = '请先补充基本故事构想。'
    result = {'can_continue':can_continue,'can_deliver':can_deliver,'can_plan_capacity':can_plan,
              'delivery_blocked_reason':reason}
    if status == 'running':
        kind,label,detail = 'wait','正在推演','本轮完成后会显示可进行的下一步，已发送内容保持。'
    elif status == 'launched' or session.get('deleted_at'):
        kind,label,detail = 'complete','此讨论已结束','现有骨架和讨论记录保留。'
    elif session.get('restart_required'):
        kind,label,detail = 'edit_basics','确认基础设定','基础设定已修改，请先确认本次推导范围。'
    elif pending:
        kind,label,detail = 'resolve_question','决定当前走向',reason
    elif status in {'error','paused'}:
        kind,label,detail = 'recover','继续未完成的推演','已有内容和选择已保存，可恢复未完成的步骤。'
    elif not session.get('messages'):
        kind,label,detail = 'discuss','继续完善故事','先基于你的构想形成整书走向，再安排篇幅。'
    elif enabled and capacity.get('status') == 'proposed' and capacity.get('plan'):
        kind,label,detail = 'choose_capacity','查看篇幅方案','故事走向没有待选问题。请选择篇幅方案，采用后生成骨架。'
    elif enabled and not capacity_ready:
        kind,label,detail = 'plan_capacity','完善篇幅方案','故事走向没有待选问题。下一步由模型完善场景与分章安排，再由你确认篇幅。'
    elif can_deliver and (capacity.get('status') == 'confirmed' or
            (guidance.get('next_action') == 'generate' and guidance.get('reviewed_revision') == session.get('revision'))):
        kind,label,detail = 'deliver',('更新完整骨架' if session.get('prompt_md') else '生成完整骨架'),'可以生成章节、人物、伏笔与回报的完整骨架；这不会启动正文创作。'
    else:
        kind,label,detail = 'discuss','继续完善故事','可让模型基于已有讨论继续完善，不必额外输入想法。'
    return {**result,'kind':kind,'label':label,'reason':detail}


def action_message(session: dict, action: str | None, *, intent: str, mixed: bool) -> str:
    if action is None:
        return ''
    if intent != 'discuss' or mixed:
        raise ValueError('继续推演不能同时采用故事或篇幅方案，也不能直接生成骨架。')
    capability = project(session)
    allowed = capability['can_continue'] if action == 'develop_story' else capability['can_plan_capacity']
    if not allowed:
        raise ValueError(capability['delivery_blocked_reason'] or '当前不能执行此推进操作，请先完成待处理步骤。')
    instruction = ('请基于已保存的全部讨论、原作资料和作者明确决定，继续完善整书因果、人物选择、情绪回报与故事看点。'
                   '只提出真正需要作者决定的重大分歧；方向已充分时推进篇幅准备，不制造额外问题。'
                   if action == 'develop_story' else
                   '请保留全部已确认故事，完善覆盖整书的场景篇幅与分章方案。保持关键情绪场面、看点、高潮和结局；'
                   '逐项核对来源依据与分章总量，返回本轮可采用的 capacity_plan，不改成新的故事走向。')
    return instruction + '本次继续不代表采纳任何未确认的模型提案或假设，也不代表授权生成骨架或正文。'
