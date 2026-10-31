# 小说题材与叙事质量：GitHub 机制调研

调研日期：2026-10-31。范围：公开 README、实际提示词与实现文件的只读核查；没有安装、运行外部项目，也没有修改 Novel OS 的代码、配置或服务。以下记录五个项目的可借鉴机制、适用边界与当前本地链路对照。

## 结论边界

这些项目提供可借鉴的设计机制，不能据此证明生成小说必然更好。本次没有进行同模型、同输入的盲评，也没有找到已经验证的狼人题材专用方案。值得迁移的是“题材如何进入人物选择、世界规则、阶段推进和章节执行”的方法，不是直接增加检查器数量。

## 1. lingfengQAQ/webnovel-writer

- [官方仓库](https://github.com/lingfengQAQ/webnovel-writer)。本次源码固定于 `master` 的 `b38056ec3ae288af613c0af162d648e4a4e8825c`，该提交时间为 2026-10-22 UTC，内容涉及实体增量元数据与依赖修复。这是维护活动信号，不是质量证明。[提交](https://github.com/lingfengQAQ/webnovel-writer/commit/b38056ec3ae288af613c0af162d648e4a4e8825c)
- README 将 `master` 描述为 v6 插件维护分支，并另指 v8；本次 v8 页面未成功读取，因此以下结论只针对实际读取的 `master`，不宣称代表 v8。[README](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/README.md)
- 已读取许可证：GNU GPL v3。[LICENSE](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/LICENSE)

### 已核实机制

**题材模板不仅给文风，还定义产生剧情的规则。** 例如幻想言情模板把种族关系、禁忌原因与代价、羁绊效果、力量与情感的相互影响连在一起。它还把幻想道具对应到剧情作用，而非只提供外观词汇。这里可借鉴的是“规则 → 阻碍/选择 → 代价 → 关系变化”的组织方式，不能把其固定人物原型、长篇卷数直接当适合所有作品的模板。[幻想言情模板](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/webnovel-writer/templates/genres/%E5%B9%BB%E6%83%B3%E8%A8%80%E6%83%85.md)

**主辅题材有优先级。** 实际 Python 代码对复合题材去重、归一化，以首项为主引擎，发生冲突时优先兑现主类型的读者期待，辅类型用于增加变化。代码也要求章节体现辅类型，但照搬这种逐章要求可能造成机械插入；Novel OS 可以借鉴优先级，不必照搬频率。[genre_profile_builder.py](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/webnovel-writer/scripts/data_modules/genre_profile_builder.py)

**题材差异落到卷级推进与具体兑现。** 规划参考区别电竞的赛制/战术/赛果、直播文的反馈和证据反转、克苏鲁的规则揭露与损失。它要求先有本卷任务，再分配里程碑、章节钩子与回报；不是所有类型都套一种反转节奏。[genre-volume-pacing.md](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/webnovel-writer/skills/webnovel-plan/references/outlining/genre-volume-pacing.md)

**从全书设定到章节任务有可保存的结构。** 规划 Skill 先建立世界边界、力量代价、欲望和限制，再生成卷节拍与时间线、分批拆章；章节包含目标、阻力、代价、状态变化和未闭合问题，随后生成运行合同。对应 schema 区分全书设定、卷任务、章任务和审查合同；审查合同存在 `genre_specific_risks`。这证明已有跨阶段的数据结构，不能仅凭字段存在就声称它有效识别了所有题材偏离。[规划 Skill](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/webnovel-writer/skills/webnovel-plan/SKILL.md)、[合同 schema](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/webnovel-writer/scripts/data_modules/story_contract_schema.py)、[合同持久化](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/webnovel-writer/scripts/data_modules/story_contracts.py)

### 必须保留的限定

- `genre-profiles.md` 虽列有钩子、兑现和节奏配置，但文件开头明确标记 **Fallback Only**，不能把它写成当前唯一主判定系统。文件自身也把配置定位为权重与建议。[genre-profiles.md](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/webnovel-writer/references/genre-profiles.md)
- 实际统一 reviewer 只查设定、时间线、连贯、角色、逻辑；明确不评价文笔、不建议加反转。因此不能宣称该 reviewer 能验证题材吸引力或读者满意度。它要求问题有证据、事实级严重问题才阻断，这个边界值得保留。[reviewer.md](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/webnovel-writer/agents/reviewer.md)
- README/题材指南给出 37 种模板；本次读取的清单没有独立狼人模板。其目标读者、常用套路和篇幅主要面向中文网文，不能直接替代北美女性狼人小说的专门设计。[题材指南](https://github.com/lingfengQAQ/webnovel-writer/blob/b38056ec3ae288af613c0af162d648e4a4e8825c/docs/guides/genres.md)

**对 Novel OS 的价值判断：较高。** 最可借鉴的是把类型拆成冲突来源、规则代价与阶段回报，再传到章任务；不建议整体搬入其长流水线或全部硬性章数要求。这是基于以上源码的适配判断，尚未经本项目实验验证。

## 2. YILING0013/AI_NovelGenerator

- [官方仓库](https://github.com/YILING0013/AI_NovelGenerator)。本次固定 `main` 的 `a61674fad4dd6c4c5fa9a2ad2f12b667109e628a`，提交时间 2026-10-16 UTC，内容为创作流程与文档更新。[提交](https://github.com/YILING0013/AI_NovelGenerator/commit/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a)
- 当前 main 是重写版，README 将旧实现放在 `old` 分支；不能把旧版网络教程描述混入当前实现。README 明确其演示使用预设模型回复，演示展示流程，不能证明模型写作质量。[README](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/README.md)
- 已读取许可证：GNU AGPL v3。[LICENSE](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/LICENSE)

### 已核实机制

**将世界观摘要转成作者可审阅的具体规则。** 规则候选包含陈述、适用范围、执行方式、例外、代价与后果；生成时要引用实际已有角色/势力，区分全局、势力和角色作用域。规则候选不自动成为事实。这比一句“这是狼人故事”更适合表达变身、族群义务、身份暴露、羁绊等机制，但这些狼人具体设计仍需 Novel OS 自行实现。[规则生成提示词](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/backend/services/task_flows/defaults/create_core_world_rules_prompt.md)、[规则输出结构](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/backend/services/task_flows/defaults/create_core_world_rules_text_prompt.md)

**把计划目标和已经发生的事实分开。** 篇章规划要求前一项终点接到后一项起点；结果必须有行动和关键节拍支撑，不能提前宣布完成。正文提示词继续区分计划、当前事实、角色当时知道的信息和仍未发生的高潮，并禁止无铺垫的免费能力或替代代价。这直接针对“有设定、正文却随意忘记代价”的逻辑问题。[篇章规划提示词](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/backend/services/task_flows/defaults/plan_arcs_by_ai_story_prompt.md)、[章节写作提示词](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/backend/services/task_flows/defaults/write_chapter_by_ai_story_prompt.md)

**规则核查范围有限，结果可追溯。** 工作流将完整章正文和作者选择的规则交给模型；提示词必须同时考虑范围、例外和代价，输出疑点及原文引用。实际服务会校验规则 ID 属于所选集合、引用确实位于对应正文位置；它明确指出“引用匹配”不证明疑点结论正确。[核查流程](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/backend/services/task_flows/defaults/check_chapter_world_rules_by_ai.json)、[核查提示词](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/backend/services/task_flows/defaults/check_chapter_world_rules_prompt.md)、[实际服务](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/backend/services/novel/chapter_check_service.py#L431)

### 必须保留的限定

- 上述章节规则核查的实际服务限定作者选择的 **1–5 条活动全局规则**，不是自动扫描所有角色、势力与全书规则。虽然生成模型支持多种作用域，不能据此夸大核查覆盖范围。[实际服务](https://github.com/YILING0013/AI_NovelGenerator/blob/a61674fad4dd6c4c5fa9a2ad2f12b667109e628a/backend/services/novel/chapter_check_service.py#L431)
- “遵守狼人规则”与“狼人规则推动精彩剧情”是两件事：没有违反变身规则的家庭剧仍可能没有狼人故事感。该核查能帮助发现矛盾，本次未发现它已经实现题材核心兑现的效果验证。
- 本次不主张迁移其完整应用架构；优先参考规则结构、上下文事实分层与有出处的疑点审阅。

**对 Novel OS 的价值判断：中高。** 更适合借鉴逻辑与世界规则的落地方式，不应把它当成现成的狼人创意生成器。这是适配建议，不是实验结论。


## 3. DOC / doc-storygen-v2：让正文兑现具体大纲事件

- 原论文：[DOC: Improving Long Story Coherence With Detailed Outline Control](https://aclanthology.org/2023.acl-long.190/)。论文通过详细的分层大纲和生成控制，改善数千词故事的连贯性、大纲相关性与趣味性；有相对 Re3 的人工评测，但这不等于证明它能提高当前模型、百万字小说或狼人市场作品的质量。
- 新版实现：[facebookresearch/doc-storygen-v2](https://github.com/facebookresearch/doc-storygen-v2)，核实提交 `d092b42686bfc29ef2d610d5563e822ebf9d1451`。
- [规划提示词](https://github.com/facebookresearch/doc-storygen-v2/blob/d092b42686bfc29ef2d610d5563e822ebf9d1451/scripts/plan/prompts.json)先建立背景与人物，再将高层事件细化；子事件同时看到前后事件，给事件补上场景和出场人物。
- [正文提示词](https://github.com/facebookresearch/doc-storygen-v2/blob/d092b42686bfc29ef2d610d5563e822ebf9d1451/scripts/story/prompts.json)使用故事前提、有关人物、之前事件摘要、当前事件和邻近事件。评估明确区分“续写是否与上下文相容”和“计划事件是否在正文发生”。
- [实际组装与评分代码](https://github.com/facebookresearch/doc-storygen-v2/blob/d092b42686bfc29ef2d610d5563e822ebf9d1451/storygen/story/story_writer.py)按大纲节点选取人物，支持折叠已完成的旧事件节省上下文，并按配置进行相关性/连贯性评分。
- 对 Novel OS 的价值：类型不能只停在总骨架，应变成当前章节会发生的行动、阻力和后果；审核需要正文证据。建议复用现有审核，不照搬多候选评分循环。
- 限制：计划事件发生不代表人物有趣；僵硬执行细纲可能损害创作弹性。旧论文的评测不直接验证新版代码。新版 [LICENSE](https://github.com/facebookresearch/doc-storygen-v2/blob/d092b42686bfc29ef2d610d5563e822ebf9d1451/LICENSE)为 Apache-2.0。

## 4. Long-Novel-GPT：剧情与正文对应、支持局部修改

- 仓库：[MaoXiaoYuZ/Long-Novel-GPT](https://github.com/MaoXiaoYuZ/Long-Novel-GPT)，核实提交 `107c31e54686947a6d00404e332475a60b66e630`。
- [DraftWriter](https://github.com/MaoXiaoYuZ/Long-Novel-GPT/blob/107c31e54686947a6d00404e332475a60b66e630/core/draft_writer.py)基于剧情/正文成对片段进行创作和总结，可以指定片段范围。
- [正文上下文提示词](https://github.com/MaoXiaoYuZ/Long-Novel-GPT/blob/107c31e54686947a6d00404e332475a60b66e630/prompts/创作正文/context_prompt.txt)分别传入剧情上下文、正文上下文、当前剧情与待改正文；[正文系统提示词](https://github.com/MaoXiaoYuZ/Long-Novel-GPT/blob/107c31e54686947a6d00404e332475a60b66e630/prompts/创作正文/system_prompt.txt)要求按剧情写作并响应用户修改意见。
- 对 Novel OS 的价值：定位“哪些类型设计没有写出来”，只改相关剧情/章节，再同步后续状态，避免整本重写。
- 限制：读取的正文提示词是通用写作规范，不是完整狼人类型包；示例中的第三人称等固定要求也不应照搬。未确认独立代码许可证，本次仅借鉴机制，不提出直接复制源码。README 的长篇能力声明不能代替质量评测。

## 5. Dramatron：逐层创作、保留作者的方向选择

- 仓库：[google-deepmind/dramatron](https://github.com/google-deepmind/dramatron)，核实提交 `2e7c36afadacf8321b77a468940024371b7a8c7a`。
- [实现 notebook](https://github.com/google-deepmind/dramatron/blob/2e7c36afadacf8321b77a468940024371b7a8c7a/colab/dramatron.ipynb)中，`generate_characters` 根据故事前提生成人物，`generate_scenes`结合前提和人物描述形成情节，`generate_place_descriptions`按场景生成地点，后续再写对白；`StoryGenerator`分层保存结果和人工干预历史，界面提供改写与历史切换。
- [官方研究介绍](https://deepmind.google/research/publications/13609/)说明它做过 15 位戏剧/影视专业人士参与的共创研究。
- 对 Novel OS 的价值：头脑风暴主动提出若干有实质差异的类型走线，让用户修正主要矛盾、人物关系和规则代价后再深化；把用户判断留在高价值创作选择处。
- 限制：它是剧本共创研究，不是全自动网文质量保证。[项目说明](https://google-deepmind.github.io/dramatron/details.html)也记录了公式化等问题。无需把其每个生成层都照搬成用户步骤。

## 6. 与当前 Novel OS 的对应关系

本次只读检查发现，当前实现已经有类型传递和创作约束，不能断言“作品类型完全没用”。

- `core/novel_classification.py:150` 的 `werewolf` 要求把族群规则、身份、伴侣关系预期变成因果约束。
- `core/novel_classification.py:700` 的 `design_requirements` 汇集所选分类的设计要求。
- `core/prompt_intake.py:210` 将这些要求写入 structured brief。
- `core/orchestrator.py:618` 起要求所选类型影响前提、不断升级的选择、高潮和结尾。
- `skills/novel-brainstorm-workshop/references/genre-adapters.md`有通用奇幻/推想世界规则建议和混合类型的主次要求；所读文件没有狼人专用包。

因此值得验证的假设是：类型要求存在，但具体化与正文兑现不足。一句总体要求可能不足以稳定产生某个细分类型的叙事。还没有读取用户所说狼人作品的骨架、请求与正文，不能把这个假设当作该作品的已确认根因。

## 7. 建议的改造方向（讨论方案，尚未开发）

### 1. 从所选标签生成明确的阅读体验

界面中的标签包含世界类型、情节套路、人物身份、成长主题、基调和读者年龄。保持现有分类 ID/导出协议；创作层另行解释这些维度，并确定本书主要阅读承诺。

例如“狼人 + 复仇 + 女性成长”可以走族群政治、超自然情感或身份恐怖。先让模型提出有差异的方案，并说明每种方案的核心矛盾、人物选择与类型规则；不默认所有狼人作品都必须出现 Alpha、命定伴侣、拒绝配偶等同一组套路。

### 2. 为本书选少量真正产生后果的类型规则

类型模板提供可选素材、常见读者期待和失败模式；本书方案决定实际采用什么。每条采用的规则应说明：何时生效、能做什么、限制/代价、谁能利用、将迫使谁做出什么选择。正文语言风格另外配置视角距离、情绪强度、对白潜台词与感官重心，避免用修饰词替代剧情设计。

狼人题材的原创示意（不是用户现有小说事实）：

> 满月变身时，失控者依赖族群的共同庇护。女主发现首领在牺牲弱小成员换取族群安全；揭露他会使自己被逐。她既不愿继续沉默，也不能把同伴一起带入危险，于是必须寻找新的庇护方式，并承担试验失败的代价。

这里的族群规则会改变人物选择、对手反应与高潮的解法。去掉狼人机制后，关键障碍需要重新设计。该设定只是可选示例，不应变成全部狼人小说的固定公式。

### 3. 骨架把规则映射到关键转折，正文提供兑现证据

在现有骨架流程里表达：欲望 → 障碍 → 选择 → 代价 → 对手应对 → 新局面。类型规则至少在重要转折发挥不可替代作用；不要按“每章出现几次狼人词汇”验收，也不强迫每章展示全部类型要素。

章节生成带入本章相关规则、当前关系/资源变化、预期行动和后果。复用现有阶段审核检查实际段落：计划的代价有没有发生？转折由谁的选择造成？结局是否凭突然新增的规则解决？只把事实矛盾、未铺垫的关键规则等确定性问题当阻断项，审美和套路强弱作为建议。

### 4. 用小规模对照验证，而非先堆更多审核

同一模型和同一原始构想，对比现方案与上述方案。先评前几章正文，加中段和高潮规划，再选小篇幅完整作品验证全书兑现。匿名阅读评价类型辨识、类型不可替代性、人物主动性、因果连贯、期待与回报、语言表现，同时记录耗时和费用。模型自评可以帮助定位，不能充当唯一质量证明。

优先级：类型设计模板与本书具体规则 → 大纲到正文兑现 → 局部修改 → 更细的语言风格控制。复用现有阶段，不新增长链路、多轮强制审批或无上限自动重写。

## 8. 本次边界

仅进行公开 GitHub、官方论文及本地代码只读调研，并新增本研究文档。未改动应用代码、配置、运行服务、小说数据或分类字典，未调用模型生成小说；未验证第三方项目的端到端质量。


## 未纳入推荐的项目

`helloo1568/fanqie-novel-workbench` 的公开 GitHub 页面本次连续读取失败，不能以搜索摘要证明其当前实现、许可证或可用性，故未采信其实现描述。
