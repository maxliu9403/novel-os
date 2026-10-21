"""Render and check this workshop blueprint; does not validate unwritten prose."""
from pathlib import Path
import hashlib
import json

BASE = Path(__file__).resolve().parent
d = json.loads((BASE / 'long-form-design.proposal.json').read_text())
chapters = d['section_d']['chapter_map']
checks = []


def check(name, condition, detail):
    checks.append({'check': name, 'status': 'pass' if condition else 'fail', 'detail': detail})


check('chapter_sequence', [c['chapter'] for c in chapters] == list(range(1, 61)), '60章连续编号，包含原稿重新编排的1—4章。')
vols = d['section_d']['volumes']
check('three_volumes', [(v['chapter_start'], v['chapter_end']) for v in vols] == [(1, 20), (21, 40), (41, 60)], '3卷，每卷20章。')
check('word_target', sum(c['word_count_target'] for c in chapters) == d['format']['target_words'] == 72000, '章正文72000英文词；开篇导语另计120—180词。')
check('scene_coverage', sum(len(c['scenes']) for c in chapters) == 120 and all(s['planned_action'] and s['pov'] == 'Evelyn' for c in chapters for s in c['scenes']), '120个拟议场景均有行动和明确POV。')
check('planning_status', all(c['status'] == 'planned' and c['draft_review'] == 'not_run' for c in chapters), '不把规划证据冒充正文验收。')
check('chapter_contracts', all(c['primary_plot_advancement'] and c['character_arc_advancement']['toward'] and c['emotional_beat'] and c['ending_hook_type'] for c in chapters), '每章有情节、人物变化、情绪和结尾方式。')
check('adjacent_variety', all(a['emotional_beat'] != b['emotional_beat'] and a['ending_hook_type'] != b['ending_hook_type'] for a, b in zip(chapters, chapters[1:])), '相邻章情绪标签与结尾机制无完全相同；语义重复另经人工设计复核。')
for thread in ['daughter', 'father', 'work', 'support']:
    refs = [c['chapter'] for c in chapters if thread in c['subplot_touches']]
    check('thread_spacing_' + thread, all(b-a <= 3 for a, b in zip(refs, refs[1:])), {'chapter_refs': refs, 'note': '在本线首次与末次触及之间，相邻触及不超过3章；不要求每次展开完整场景。'})
check('payoff_refs', all(p['setup'] and p['payoff'] and all(1 <= n <= 60 for n in p['setup'] + p['payoff']) and max(p['payoff']) <= p['deadline'] for p in d['payoff_ledger']), '10项承诺均有有效种下、兑现与截止章节。')
chars = d['section_b']['principal_characters']
ending = d['ending_contract']['character_arcs']
outcome_labels = {
    'evelyn': '凭自己的选择与专业能力维持生活',
    'olivia': '能表达需要，拥有同伴、兴趣与相符年龄的选择',
    'christian': '在婚姻之外持续履行父亲责任',
    'helena': '承认援助的限度并安排自己的家庭支出',
    'jack': '保持有边界的专业关系',
    'claire': '成为有来有往的成人朋友',
}
hook_labels = {
    'relationship': '关系张力', 'action': '下一步行动', 'cost': '已经承担的代价',
    'arrival': '进入新处境', 'question': '需要回答的问题', 'deadline': '时间限制',
    'understanding': '理解发生变化', 'evidence': '事实带来的后果', 'resource': '资源改变',
    'belonging': '归属感', 'decision': '作出选择', 'consequence': '选择的后果',
    'terms': '具体条件', 'identity': '身份变化', 'competence': '能力接受检验',
    'agreement': '约定等待执行', 'failure': '无法撤销的损失', 'repair': '修补的下一步',
    'reward': '阶段回报', 'revelation': '新事实', 'disclosure': '事实公开后的压力',
    'boundary': '新的关系界限', 'tradeoff': '必须作的取舍', 'voice': '自己的表达',
    'price': '真实成本', 'cooperation': '合作变化', 'correction': '纠正事实',
    'reframing': '重新理解', 'accountability': '承担责任', 'choice': '主动选择',
    'result': '结果的后果', 'truth': '事实落定', 'milestone': '阶段成果',
    'freedom': '新的自由', 'desire': '自己的愿望', 'settlement': '具体结算',
    'behavior': '行为出现变化', 'constraint': '必须处理的限制', 'resolution': '主线落定',
    'memory': '记忆的新意义', 'privacy': '保住个人生活的代价', 'ownership': '成果由自己负责',
    'proposal': '等待检验的方案', 'commitment': '付出代价的承诺', 'readiness': '即将兑现',
    'payoff': '成果兑现', 'future': '未来的具体安排', 'agency': '自己的声音和选择',
    'afterglow': '情绪余韵', 'ending': '生活继续的完整收尾',
}
check('principal_outcomes', len(chars) == len(ending) == 6 and all(any(a['character_id'] == c['id'] and a['required_outcome'] == c['required_outcome'] and a['evidence_chapters'] == c['planned_evidence'] for a in ending) for c in chars), '6名主要人物的语义结局与拟议证据一致。')
check('ending_window', d['section_e']['ending_window'] == [56, 57, 58, 59, 60] and d['ending_contract']['intentional_open_core_threads'] == [], '最后5章用于兑现；没有故意悬置核心线。')
b = d['section_c']['proposed_budget']
pre = sum(x['amount'] for x in b['pre_first_pay_caps'])
monthly = sum(x['amount'] for x in b['normal_monthly_costs'])
check('budget_arithmetic', pre == 7400 and b['opening_accessible_household_cash'] - pre == b['pre_first_pay_remaining'] == 3000 and monthly == 5650 and b['normal_monthly_take_home'] - monthly == b['monthly_buffer_before_extra_child_contributions'] == 1150, {'pre_first_pay': pre, 'monthly_costs': monthly, 'monthly_buffer': 1150})
bridge = b['first_full_pay_month_bridge']
balance = bridge['starting_cash'] + sum(x['net'] for x in bridge['payments']) - bridge['housing_paid'] - bridge['nonhousing_noneducation_cost_cap'] - bridge['quarter_education_paid']
check('first_quarter_cash_bridge', balance == bridge['ending_cash_lower_bound'] == 2150 and min(x['amount'] for x in bridge['cash_nodes']) > 0, 'D31—D60支付首季3000后仍有现金2150；不计父亲补款，储备不重复作为消费扣减。')
check('rent_pay_order', b['rent_calendar']['first_period_end_day'] + 1 == b['rent_calendar']['next_rent_day'] and b['pay_calendar']['first_pay_day'] < b['rent_calendar']['next_rent_day'], 'D5—34首租期；D31首薪先于D35次租。')
check('ages', next(c for c in chars if c['id'] == 'olivia')['age_at_opening'] == 9 and next(c for c in chars if c['id'] == 'olivia')['age_at_ending'] == 10, '开篇9岁、约第6个月生日、事件一年后10岁。')
manifest = json.loads((BASE / 'canon-input-manifest.json').read_text())
for f in manifest['files']:
    source = hashlib.sha256(Path(f['source_path']).read_bytes()).hexdigest()
    snapshot = hashlib.sha256((BASE / f['snapshot_path']).read_bytes()).hexdigest()
    check('source_unchanged_' + str(f['reading_order']), source == snapshot == f['sha256'], f['source_file'])
report = {
    'artifact_type': 'design_validation',
    'scope': 'planning_only',
    'status': 'pass' if all(x['status'] == 'pass' for x in checks) else 'fail',
    'author_approval': d['approval_status'],
    'checks': checks,
    'human_design_review': {'status': d['section_e']['design_review'], 'repairs': d['section_e']['review_repairs'], 'basis': 'Three independent focused design reviews; root applied the listed final clarifications.'},
    'not_run': ['manuscript drafting', 'Editor prose approval', 'Continuity chapter approval', 'Style chapter approval', 'production Prompt parser validation'],
}
(BASE / 'design.validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')

lines = []


def emit(s=''):
    lines.append(s)


def table(headers, rows):
    def cell(x):
        return str(x).replace('|', '／').replace('\n', '<br>')
    emit('| ' + ' | '.join(headers) + ' |')
    emit('| ' + ' | '.join(['---'] * len(headers)) + ' |')
    for row in rows:
        emit('| ' + ' | '.join(cell(x) for x in row) + ' |')
    emit()


emit('# The Last Golden Spire｜三卷60章全书设计提案')
emit()
emit('暂定短书名：**The Last Golden Spire（最后一根金色塔尖）**。原素材文件名与四份原文保留。')
emit()
emit('已确认：保留原人物与已发生事件，修补逻辑；一部长篇分3卷，共60章，每卷20章。**60章包含承接原稿的第1—4章，不是另续60章。**')
emit()
emit('沿用先前偏好纳入本轮审阅：美国市场、50—65岁女性、英文正文；每章约1200词，全书章正文约72000词，开篇导语另计120—180词。设计说明为中文。')
emit()
emit('状态：完整A—E提案，待作者整体确认。所有章节与兑现证据均为planned，尚未新写正文、生成正式生产Prompt或启动模型。新人物年龄、城镇、项目、金额、时间跨度与结局属于本次建议，不能误写成原稿已有事实。')
emit()
emit('## A. 故事承诺与最终方向')
emit()
emit(d['section_a']['logline'])
emit()
for key, label in [('event_promise','事件'), ('relationship_promise','关系'), ('emotional_promise','情绪'), ('meaning_promise','主题')]:
    emit(f'- **{label}承诺**：{d["section_a"][key]}')
emit()
emit('**拟定结局**：' + d['section_a']['ending_direction'])
emit()
emit('本书由家庭危机后的实际后果推动。女主的反击包括纠正被擅自使用的未来服务承诺、拒绝带条件的补偿以及完成自己的工作；她也会判断失误并付出代价。事业戏约占30%—35%，母女、婚姻、照护和成人关系始终是核心。')
emit()
table(['卷', '章节／时间', '核心任务', '卷末兑现'], [[f'{v["volume_number"]}．{v["title"]}<br>{v["chinese_title"]}', f'{v["chapter_start"]}—{v["chapter_end"]}<br>{v["time_window"]}', v['central_conflict'], v['payoff']] for v in vols])
emit('## B. 人物、关系与声音')
emit()
table(['人物', '已有事实／新增建议', '想要什么与主要障碍', '结局证据'], [[f'{c["name"]}<br>{c.get("age_proposal",c.get("age_at_opening"))}岁' + ('（新增建议）' if 'age_proposal' in c else '→10岁'), c['canon'], c.get('desire','').rstrip('。')+'；'+c.get('flaw', c.get('limit','')), '第'+ '、'.join(map(str,c['planned_evidence']))+'章；'+outcome_labels[c['id']]] for c in chars])
emit('**新增配角**：Ruth Calder（53，主管）、Noah Chen（35，设计负责人）、Lena Ortiz（57，设施主管）、Mara Reid（44，学校学习支持联络人）、Maya（9，女儿同伴）。他们各有职责和需求，不能替女主完成全部选择。Carter保留为6岁儿童，一年后7岁，不承担成人行为的责任。')
emit()
emit('**母亲的三段变化**：维持完整家庭的外观才能保护孩子 → 必须永远正确才配离开 → 必须替孩子控制全部失望。她逐步学会承担决定、承认错误并允许女儿拥有自己的感受。')
emit()
emit('**女儿自己的故事**：'+d['section_c']['child_story_spine']['project']+'。'+d['section_c']['child_story_spine']['independent_stakes']+' '+d['section_c']['child_story_spine']['own_mistake']+' '+d['section_c']['child_story_spine']['final_result'])
emit()
emit('**叙述声音**：'+d['section_b']['voice']['pov']+'。'+d['section_b']['voice']['attention']+'。对话有生活中的绕开、玩笑和误解；重要变化由前态、触发、选择、后果与后来行为支撑。')
emit()
emit('## C. 世界、资源与连续性')
emit()
emit('现实家庭故事，模型没有超自然抹除能力。新城镇拟为Alderwick；新雇主Westmere Development Group与原稿Spruce Valley项目独立，Evelyn不参与丈夫投标评审。')
emit()
emit('**职业项目**：'+d['section_c']['setting_policy']['main_project']+' '+d['section_c']['project_spine']['role_scope'])
emit()
for step in d['section_c']['project_spine']['causal_chain']:
    emit('- '+step)
emit()
emit('时间：'+d['section_c']['project_spine']['delivery_calendar'])
emit()
emit('**原稿事实边界**：目前没有证实婚外性关系、Carter亲生父子反转、伪造监护危机或策划商场事件；后续也不以这些新增反转撑篇幅。Evelyn过去促成了入围，原稿没有证明她长期替丈夫做技术工作。第11章是新请求，第21章才出现未经授权的未来服务承诺。')
emit()
emit('### 资源和金钱')
emit()
table(['项目', '拟定金额／安排'], [
    ['开篇可用现金', '$10,400；用途逐笔留存，账户可用性不等于最终个人财产权'],
    ['首薪前支出上限', '$7,400，留$3,000；五晚短住$650，首租与押金$3,100，其他列项见结构化方案'],
    ['住房', b['housing']],
    ['首薪与次租', d['section_c']['timeline']['first_pay']+' D5—D34首租期，D35再付租金$1,550。'],
    ['稳定月度', '净工资$6,800，正常开支及预留$5,650，余量$1,150；不依赖父亲额外付款'],
    ['季度教育费', b['child_cost_agreement']],
    ['阅读服务与账期', b['education_service_calendar']],
    ['首个完整发薪月', 'D31—D60收入$6,800，房租$1,550，其他非教育支出上限$3,100，季度教育实付$3,000；加原余款后保守留$2,150。教育预留不重复扣为消费。'],
    ['$12,000旧款', b['horse_payment_resolution']],
    ['$2,400条件款', '第37章明确拒绝，没有到账；第38章父亲支付的是另有责任的$1,500教育欠份。'],
    ['一次性结算与月收入', '$6,000只计财产调整，不冒充持续工资。所有金额为本故事设计值，不是市场价格、通用法律结论或已获作者批准的事实。'],
])
emit('### 商场当天的统一时间')
emit()
table(['时刻', '发生什么／叙述者何时知道'], [[x['time'],x['event']] for x in d['section_c']['timeline']['mall_day']])
emit(d['section_c']['timeline']['mall_day_status'])
emit()
emit('### 原稿修补记录')
emit()
table(['位置', '保留', '修补'], [[x['location'],x['retained'],x['proposed']] for x in d['source_repairs']])
emit('原文未修改。学习支持与受惊恢复分别处理；实际程序、具体专业参数若进入正文，按写作所需再核验，不靠擅加诊断或自动司法结果补洞。')
emit()
emit('## D. 三卷与60章完整章节表')
emit()
emit('每章约1200英文词，以下均为Evelyn第一人称限知的两个拟议场景。场景由行动中的分歧、需要与选择形成，不以每章强行制造新事故作为推进。')
emit()
for v in vols:
    emit(f'### 卷{v["volume_number"]}：{v["title"]}｜{v["chinese_title"]}')
    emit()
    emit('**压力与变化**：'+v['primary_pressure']+' '+v['protagonist_shift'])
    emit()
    emit('**中点**：'+v.get('midpoint_revaluation','')+' **卷末兑现**：'+v['payoff'])
    emit()
    if v['carryover']:
        emit('**传入下一卷的后果**：'+v['carryover'])
        emit()
    for c in chapters[v['chapter_start']-1:v['chapter_end']]:
        emit(f'#### {c["chapter"]:02d}. {c["title"]}｜{c["time"]}')
        emit()
        for s in c['scenes']:
            emit(f'{s["scene"]}. {s["planned_action"]}')
        emit()
        emit('**变化与后果**：'+c['primary_plot_advancement'])
        emit()
        emit('**情绪**：'+c['emotional_beat']+'；**结尾方式**：'+hook_labels.get(c['ending_hook_type'],c['ending_hook_type'])+'。')
        emit()
emit('### 开篇与试读价值')
emit()
emit('前3章形成小弧：允许孩子停下勉强维持的项目 → 发现明确资源差异并重新争取工作 → 孩子获救、母亲付费建立退路。第4章保留最后塔尖与模型毁坏，并真正离家入住；不把孩子是否获救当作付费前故意扣留的信息。平台免费章数尚未锁定。')
emit()
emit('开篇导语在第1章前单独写一次，120—180英文词，承诺婚姻背叛后的有代价反击与持续重建，不泄露正式离婚、竞标或最后父女联系结果。')
emit()
emit('## E. 兑现、质量与审阅边界')
emit()
table(['承诺', '种下', '兑现', '截止'], [[p['promise'],'、'.join(map(str,p['setup'])),'、'.join(map(str,p['payoff'])),p['deadline']] for p in d['payoff_ledger']])
emit('最后5章各自完成不同任务：56职业成果实际使用，57承担所选未来，58女儿与同伴表达，59母亲处置自己的碎塔尖，60用持续生活收尾。第49章婚姻已正式结束，不把全部结果挤到最后一段。')
emit()
for requirement in d['section_e']['review_requirements']:
    emit('- '+requirement)
emit()
emit('**本轮结论**：三位独立复核者分别检查原稿连续性、资源与时间、长篇承载力；主要问题已修补。自动规划检查结果：'+report['status']+'。这些结论只覆盖蓝图，未对正文、人物声音或实际章节兑现作出通过认证。')
emit()
emit('**需要作者确认的整体方向**：暂定书名与沿用的读者语言；成人年龄与新增配角；三卷任务和60章具体安排；现实职业／家庭世界、时间与金额；不复婚、父亲承担有限责任、母女各有新生活的结局。可以一次确认A—E，也可以指定局部修改。')
emit()
emit('确认后再形成正式Novel OS生产Prompt及启动命令；本文件不是可直接启动生产的Prompt。')
emit()
emit('相关文件：[结构化设计](long-form-design.proposal.json) · [规划校验报告](design.validation.json) · [原稿清单与哈希](canon-input-manifest.json) · [连续性基础稿](continuation-foundation.md)')
(BASE / 'long-form-design.proposal.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'status': report['status'], 'checks': len(checks), 'failed': [x['check'] for x in checks if x['status'] != 'pass'], 'chapters': len(chapters), 'scenes': 120, 'markdown_bytes': (BASE / 'long-form-design.proposal.md').stat().st_size},ensure_ascii=False))
if report['status'] != 'pass':
    raise SystemExit(1)
