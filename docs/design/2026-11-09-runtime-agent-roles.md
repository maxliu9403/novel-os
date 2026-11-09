# 五个创作角色在生产流程中的位置

基线为 `dev@a2617c9`。五种编辑职责都能在代码中找到。Web 启动时，后台作业调用 `PipelineRunner`，由 `NovelOrchestrator` 选择角色、组装提示并调用模型。一次作业可以多次使用同一角色，也可以在本地完成一个阶段。README 的五角色图适合解释分工。判断一本书实际有哪些模型调用，应看其运行清单、代码分支和请求记录。[README 的五个角色](../../README.md#-the-five-agents) · [后台启动](../../api/workshop.py#L2076) · [逐章分派](../../core/pipeline_runner.py#L1036)

| 角色与模型路由 | 带 `story-first.v1` 交接的全书任务 | 其他完整创作任务 |
| --- | --- | --- |
| Architect，`architect` | 分阶段大纲使用 Architect 连接生成设计、章纲和语义核查。进入正文后，程序从已通过的 foundation 提取本章执行合同。 | 全书规划依任务保存的模式运行。逐章扩写时由 Architect 生成本章细纲。[大纲连接](../../core/orchestrator.py#L858) · [两种章节规划](../../core/orchestrator.py#L1277) |
| Scribe，`writer` | 根据本章计划、已提交事实和实际正文上下文生成初稿。 | 也由 Scribe 撰写初稿。[写作请求](../../core/orchestrator.py#L2398) · [有创作交接时的上下文](../../core/creative_execution.py#L287) |
| Editor，`editor` | 首稿先进入联合审稿。出现具体缺陷且策略允许自动修订时，Editor 根据反馈修订，之后再次审稿。 | 初稿经过本地预检后，Editor 按所选编辑模式修订，再交连续性核验。[常规编辑](../../core/pipeline_runner.py#L1124) · [按需修订](../../core/pipeline_runner.py#L1373) · [修订角色](../../core/orchestrator.py#L2581) |
| Continuity Guardian，`guardian` | 程序先查连续性。Guardian 联合检查故事、连续性、文风和读者体验，每项引用当前正文，评议绑定候选文本哈希。 | Guardian 结合程序检查结果和本章文本生成连续性报告。适用的商业合同还可触发独立商业核验。[预检与模型调用](../../core/orchestrator.py#L2793) · [联合审稿协议](../../core/chapter_review.py#L34) · [商业核验条件](../../core/pipeline_runner.py#L1239) |
| Style Curator，`style` | 文风已经在联合审稿中核验。`chapter.style` 本地核对版本、保存通过的候选正文并写回执。 | Curator 根据修订稿生成文风报告及最终候选。[本地保存](../../core/orchestrator.py#L1777) · [模型润色](../../core/orchestrator.py#L1846) |

因此，带 `story-first.v1` 标记的任务，在大纲已经通过、单章初稿审稿一次通过时，正文核心流程需要两次模型调用，分别为写作和联合审稿。自动修订成功一轮增加一次编辑和一次复核。这个口径排除全书规划、出版文案、可选审读及请求重试。使用模型替身的生产入口测试覆盖两章正常通过、修订和中断续跑。[阶段编排](../../core/pipeline_runner.py#L1333) · [调用次数与最终文本断言](../../tests/test_story_first_engine.py#L294)

### 提示词、模型与写入权限

- Workshop 讨论使用独立的 `workshop` 路由，产出已采用骨架和创作交接。新建带出版分类的讨论在创作策略中记录故事引擎版本。全书任务依据保存的交接选择正文流程。README 链接的中文《创作路径》区分了 Workshop 设计交付和引擎执行，角色规范与 Skill 提供创作指导。[中文创作路径](../../skills/novel-brainstorm-workshop/references/creation-path.md) · [Workshop 路由](../../api/workshop.py#L367) · [引擎版本来源](../../api/workshop_design.py#L189)
- 角色配置可以指向相同模型，也可以选用不同连接。路由器解析角色配置，编排器按角色缓存客户端。普通单步调用读取对应的 `agents/<角色>/prompt.md`。有创作交接时使用冻结的提示词和作者上下文。分阶段规划采用专用 `outline_planner` 提示和 Architect 连接，全书一致性核查也使用这条连接。章节 Guardian 专注实际正文。[路由与缓存](../../core/orchestrator.py#L140) · [连接解析](../../core/model_router.py#L58) · [普通角色提示](../../core/llm_client.py#L629) · [规划提示与回执](../../core/outline_planning.py#L319)
- 完整运行设置 `proposal_only`，大纲通过后单独提交 foundation。章节状态由晋级流程提交，`story-first.v1` 只采纳最终通过审稿版本所绑定的状态提案。手工编排器还支持旧的直接写状态模式。整书 dry run 在材料登记后结束。README 与 SYSTEM_OVERVIEW 所述的“每个角色更新状态”须按入口理解。[角色交接规范](../../AGENTS.md#handoff-protocol) · [系统概览](../../SYSTEM_OVERVIEW.md#the-five-agents) · [完整运行初始化](../../core/pipeline_runner.py#L751) · [基础设定提交](../../core/pipeline_runner.py#L825) · [最终章节提案](../../core/pipeline_runner.py#L3790)

### 对百章规划的价值

保留作者已采用的骨架、规划与已发生事件的区分、来源版本、已验证回执和候选稿晋级。这些机制使后续章节可以引用同一份事实，也能在换连接或恢复执行时验证成果来源。模型仍须判断情节因果、人物选择、文风及伏笔是否成立，程序可承担引用解析、版本比对、确定性检查和组装交付。[创作回执](../../core/creative_execution.py#L358) · [现有晋级服务](../../core/promotion.py#L528)

当前大纲器为章节窗口携带全局路线图和相邻完整章节。此覆盖可以检查边界衔接，跨卷伏笔还需要把远处的铺垫、转折和兑现原文一起交给核查。角色分工能够明确谁负责哪种判断，百章工程还需在任务执行层解决独立检查点、依赖追踪、容量内取证和局部失效。此处的代码核对与测试不能代替真实长篇文学质量评估。[窗口实现](../../core/planning_review.py#L19) · [既有长篇质量检查](../../core/long_form_quality.py#L113)
