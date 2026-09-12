# Novel OS 架构事实基线与外部写作方法接入边界

- 日期：2026-09-12
- 核对版本：`dea7d586026cc7d8e25b4b5a445a955602f23c84`
- 范围：源码、仓库 Skills、相关测试定义的静态阅读。没有运行模型、测试、安装、服务操作或小说设计；没有读取凭据与小说项目数据。
- 外部项目 `oh-story-claudecode` 的实现由另一项调查核验。本文不预设它只是提示词，也不宣称已完成双方功能比较。
- 下文引用为本仓库 `文件:行号`；“建议/推断”与已实现事实分开。

## 1. 决策摘要

1. **Novel OS 已是带持久化状态、版本证据、质量阶段和发布契约的生产引擎，而不只是五个角色提示词。**全书顺序是 intake → outline → originality（启用商业合同时）→ foundation.commit → 逐章计划/写作/编辑/校验/风格/商业检查/晋升 → 全书商业/终局检查 → publication.copy → compile → delivery.package。见 `core/pipeline_runner.py:643-879`、`:893-1204`。
2. **没有找到名为 `ContextAssembler` 的实现。**当前对应能力是 `build_context_pack` / `format_context_pack`，再由各阶段 prompt builder 拼接大纲、商业与终局上下文；并非统一、强类型、可插拔的完整上下文装配服务。见 `core/context_pack.py:12-36`、`:58-77`；`core/orchestrator.py:1154-1197`、`:1664-1684`、`:2721-2742`。
3. **最稳妥接入点是设计层与受控阶段输入，而非共享核心状态文件。**仓库 Workshop 明确产出已确认 Prompt、分类/篇幅/商业/封面合同和启动命令，运行时由引擎消费；新概念与已有 canon 分开。见 `skills/novel-brainstorm-workshop/SKILL.md:17-25`、`:262-274`、`:327-335`、`:417-431`。
4. **`evidence_v1` 的事务完整性相当深入，但其名称不等于独立文学质量认证。**晋升服务校验精确候选、合同绑定、旧 Final、canon hash、项目实例与幂等键；然而流水线/API 创建晋升报告时都传入空 `continuity_findings`。内容质量来自前置阶段及另行绑定的商业报告，单凭晋升凭据不足以倒推出“正文所有事实和文学质量均已核实”。见 `core/promotion.py:927-1066`；`core/pipeline_runner.py:3298-3349`；`api/services.py:1224-1258`。
5. **默认入口存在差异。**`RunSpec.quality_policy` 默认为 `legacy`，Docker 启动器默认 `evidence_v1`，Workshop 也显式给出后者。外部适配器必须把质量策略作为合同字段固定，而不是依赖调用方式。见 `core/pipeline_models.py:39-59`；`deploy.sh:297`；`skills/novel-brainstorm-workshop/SKILL.md:365-374`。
6. **能力边界值得保留：**按阶段 checkpoint 恢复、不可变 revision、canon journal/receipt、长篇卷合同、封面方向 hash 审批、EPUB-first H5 导入身份/章节定位都已实现；外部工作流若另建平行状态中心，会使这些能力互相冲突。下文逐项列证据。

## 2. 编排、恢复和并发：真实能力与范围

| 项目 | 已实现 | 边界/接入风险 | 代码证据 |
|---|---|---|---|
| 角色编排 | 阶段映射五个主要角色，逐章串行；商业复核也走 Guardian 角色 | 不是一个可任意并行调度的动态 agent DAG；新增外部阶段要显式纳入编排 | `core/pipeline_runner.py:103-120`、`:750-778`、`:893-1204` |
| checkpoint | 每阶段记录输入/输出 hash、revision、合同、proposal、evaluation、receipt、state snapshot、provider/model | 不是只靠“章节文件存在”跳过；外部覆盖输出会使复用失效或证据校验失败 | `core/pipeline_models.py:96-121`；`core/pipeline_runner.py:1274-1406`、`:2563-2586` |
| 恢复 | `resume` 先对账已提交 promotion；完成的 run 可走投影修复；已提交章节的 receipt 优先于重放大纲 | 外部恢复逻辑应调用既有 resume/retry，而不是重建 run 或把章节状态改成 done | `core/pipeline_runner.py:233-289`、`:901-935` |
| 质量暂停 | `PipelineError(blocked=True)` 持久化为 paused；review_required 在晋升前暂停，支持逐章人工批准 | 当前审阅暂停不等同于任意时间“暂停按钮”；核心 `_execute` 只捕获 Exception，未见 SIGINT/KeyboardInterrupt 的专门落盘协议。中断恢复依赖之前写入的 stage checkpoint，进行中的模型调用不续接 token 流 | `core/pipeline_runner.py:880-890`、`:1343-1379`、`:3265-3274`；`core/orchestrator.py:3129-3134` |
| 重试/自动修复 | 网络/调用重试有上限和指数退避；章节计划、连续性、商业正文有 bounded repair | 修复不是无限自迭代；新方法输出仍需适配 validator，并遵守引擎修复预算 | `core/pipeline_models.py:52-55`；`core/pipeline_runner.py:969-997`、`:1072-1111`、`:1163-1192`、`:1433-1447` |
| 同项目流水线 | `.pipeline-execution.lock` 使用非阻塞 POSIX flock，run/resume/retry 竞争同一执行权 | 是项目级互斥，非跨机分布式锁；也不自动约束绕过引擎的任意外部写入 | `core/pipeline_runner.py:169-199`、`:221-243`；`core/project_lock.py:1-8` |
| 晋升事务 | `.promotion.lock` + 项目实例身份 + CAS + journal/recovery | 与流水线执行锁是不同职责，外部直接写 JSON 不参加任一协议 | `core/project_lock.py:31-38`、`:111-150`；`core/promotion.py:1694-1735` |
| Web jobs | 内存 job 字典、线程锁、可选 unique_key、daemon thread；按项目路径隔离；同步 mutation gate 串行写并阻止删除竞态 | 不应等同持久化 worker/消息队列；进程重启不保留 `_jobs`；API mutation gate 是进程内条件变量，不是流水线的跨进程执行锁 | `api/jobs.py:35-92`、`:94-131`；`api/project_operations.py:52-99` |

**额外推断：多项目“同进程”隔离尚有风险。**`_execute` 在提供 `spec.model` 时写进程级 `os.environ["NOVEL_OS_MODEL"]`；ModelRouter 又从全局环境回退取值。同进程多个 Runner 并发时，这一全局设置可能串扰。独立项目路径和 run_id 不是模型配置的完整隔离。见 `core/pipeline_runner.py:649-650`；`core/model_router.py:27-55`。外部并行引擎宜传不可变的 per-run 配置，或采用独立进程，不宜新增全局环境切换。

## 3. ContextPack 与状态权威

### 3.1 当前上下文不是“全书全部记忆”

- 角色预算是**字符数**：Architect 5,000、Scribe 6,000、Guardian 7,000、continue 3,000、consequence 2,500。数据槽包括 cast、bonds、threads、foreshadowing、prior_chapters、codex、dropped。见 `core/context_pack.py:12-36`。
- 排序依据 POV、当前提及、在场、最近五章、关系一跳邻居；线程按优先级和人物关联；取前两章 synopsis，Codex 以名称匹配等规则排序。见 `core/context_pack.py:92-225`。
- 超预算优先丢 Codex、较低优先线程和角色；最终 formatter 仍有字符串硬裁剪兜底。称其“永不截断”或“所有 canon 都进入模型”都不符合实现。见 `core/context_pack.py:308-339`。
- Guardian 的正文超过 12,000 字符时，仅传前 6,000 + 后 4,000，并标注中间省略。全书大纲在章节规划器中也只取前 50,000 字符。见 `core/context_pack.py:39-55`；`core/orchestrator.py:1160-1165`、`:2723-2728`。

**推断：**外部“长篇记忆、线索追踪、角色声音”等方法有补强空间，但首先应补充召回/覆盖率与预算报告，而不是把全部外部文档无上限拼进 prompt。长章中部、久远伏笔、细粒度知识状态都可能不在当前 pack。新方法的关键保留事实宜落在 ChapterContract 的 `preserve_facts` / `allowed_knowledge`，其余内容作为可删减方法指导。相关字段见 `core/contracts.py:261-274`。

### 3.2 状态分层

| 层 | 当前权威与更新方式 |
|---|---|
| 作者输入 | `build_brief` 解析 Key: Value + classification + narrative_format + commercial contract；原 Prompt 保留。`ingest_prompt` 本身会更新/保存 StoryState，所以验证应指向临时项目，不是生产项目。见 `core/prompt_intake.py:137-218`、`:268-326`。 |
| 结构基础 | foundation 是规划基础；`FoundationCanonService.initialize` 加项目锁、规范化 hash、幂等 receipt。已有章节 canon 而缺合法基础衔接时要求 reconciliation。见 `core/foundation_canon.py:460-545`。 |
| 运行视图 | `proposal_only` 下由 foundation 构造非 canonical runtime state，`persist=False`；章节规划临时替换 state 后 finally 恢复。见 `core/orchestrator.py:844-868`、`:1116-1152`。 |
| 提案 | Agent 输出解析为 `CanonDeltaProposal`，按 chapter/agent/source SHA/delta 内容寻址。创建提案不触碰状态；实际 apply 要核对来源字节。见 `core/canon.py:100-159`、`:195-224`。 |
| 候选与合同 | `ArtifactStore` 保存不可变 UTF-8 blob / revision；单纯 put revision 不前移 head，也不写 legacy manuscript projection。见 `core/artifacts.py:1-6`、`:29-44`。 |
| 正式 canon | `PromotionService` 验证 exact candidate + report + proposal + contract heads + old Final + base canon + instance，journal 分 prepared/state_committed/head_committed/committed；追加 ledger 后持久化 receipt。见 `core/promotion.py:927-1066`、`:1591-1692`、`:1694-1735`。 |
| 核心设计改动 | 已有章节历史后的 foundation 改动有单独 `CanonReconciliationService.reconcile`，要求 reason/outcomes/幂等键及已提交章节历史。见 `core/canon_reconciliation.py:306-378`。 |

**重要残余边界：**这些是协作式权威协议，不是任意写文件者都绕不过的内核。`NovelOrchestrator` 单阶段模式默认仍是 `legacy_apply`，会直接 ingest Agent state block；`StoryState.save_state` 先把现有文件改名成 `.bak`，再普通 open/json.dump，没有包裹 PromotionService。canon hash 的语义域还包含 metadata、story_bible、人物、关系、线程、章节、timeline、style 等广泛字段。外部 Skill 即使只改“人物卡”或“风格元信息”也可能改变 canon。见 `core/orchestrator.py:77-101`、`:221-256`；`core/state_manager.py:409-438`；`core/canon_ledger.py:35-69`。

## 4. 商业免费章、终局与长篇质量

### 已实现

- **设计先检再写：**商业合同启用时，章节计划经过 design_check，失败可在 auto 预算内重新计划；不是写完再给一个总分。见 `core/pipeline_runner.py:936-1002`。
- **精确正文商业证据：**review 针对 style candidate；交付声称的 quote + offset 要与候选文本相符，错误 offset 仅在 quote 唯一出现时确定性修复。见 `core/pipeline_runner.py:1128-1155`；`core/commercial_quality.py:257-325`。
- **免费窗口：**启用条件是有商业合同、`evidence_v1` 且总章数覆盖 free_trial_arc；窗口后即检查，可在下一章前阻断。聚合 report 绑定 StoryContract、ChapterContract、Final revision、商业 report 和 promotion receipt。见 `core/pipeline_runner.py:739-778`；`core/commercial_quality.py:1048-1091`。
- **全书商业复核：**检查免费窗口报告、至少两个已批准归属锚点的可观察回报、初始/终端冲突资源维度与维度扩展，位于 compile 之前。见 `core/commercial_quality.py:1094-1162`；`core/pipeline_runner.py:780-792`。
- **终局：**enforce 合同下检查主冲突线程、弧线生命周期、单独的 semantic outcome 与证据、核心 payoff、最终章 Ending_Evidence、反派后果；明确 intentional_open 的部分转 warning。见 `core/ending_quality.py:310-414`。流水线在 compile 前执行，旧项目没有 enforce 时保留兼容路径。见 `core/pipeline_runner.py:794-819`。
- **长篇：**每五章、卷末和全书末审计；检查卷/章绑定、最近五章的重复 active_choice / irreversible_change / local_payoff、到期 world_event_ids、卷末 Final 文件。见 `core/long_form_quality.py:112-185`、`:188-225`、`:228-310`。

### 需谨慎的质量结论

1. **“可追溯”与“事实充分/读者满意”是两回事。**quote hash 能证明确实引用了这段文字，不自动证明引文足以支持语义判断；商业语义仍依赖模型 report。`QualityLab` 的确定性报告只建立 `no_blocking_findings`，`semantic_dimensions={}`；流水线/API 晋升时输入又是空 findings。见 `core/quality/lab.py:38-82`；`core/pipeline_runner.py:3305-3309`；`api/services.py:1229-1233`。
2. **终局检查主要读取已提交结构状态。**它不是独立通读全书正文的评审；required_outcome 可 exact/normalized/contains/aliases 匹配，状态里的证据真实性仍需要前置 prose-to-canon 校验。见 `core/ending_quality.py:293-305`、`:346-401`；Workshop 对稳定 outcome identifier 的说明见 `skills/novel-brainstorm-workshop/SKILL.md:309-321`。
3. **长篇防水检查目前偏结构/文本等同性。**相同动作换一种措辞可能通过，文学上有效的呼应也可能遇到字面重复门禁；它不等于完整语义节奏判断。见 `core/long_form_quality.py:204-225`、`:267-285`。
4. **商业词表带有明确当前类型倾向。**life stages、invisible labor、sacred assets 等枚举适合现有家庭/女性情感商业机制；不要认为分类支持 fantasy 就意味着该类型的升级、能力经济等已获得同等细粒度商业门禁。见 `core/commercial_story.py:14-88`；`core/novel_classification.py:74-104`。外部类型方法可作为新的 profile/schema adapter 候选，但需兼容既有受控枚举。

## 5. 分类、卷与系列

- 分类为版本化 canonical id 体系：主类型、最多两个次类型、故事类型、tone、setting、audience、长度/章节档位、来源与置信度；部分故事类型转成 `design_requirements`，不只是 UI 标签。见 `core/novel_classification.py:19-35`、`:284-330`、`:607-624`。
- 篇幅独立建模为 short_novel / standalone_long / multi_volume / series_installment，包含作者选择来源、确认状态、volumes、长篇适配评估。卷范围连续且覆盖每章一次，series_installment 有系列身份与本册编号。见 `core/narrative_format.py:23-46`、`:199-251`。
- intake 接受作者明确篇幅并检查与 CLI 章数/字数冲突；编译阶段将每章绑定到卷，输出分类和 serialization sidecar。见 `core/prompt_intake.py:179-213`；`core/pipeline_runner.py:3757-3774`、`:3781-3824`。
- **边界/推断：**当前“系列”是单册的系列归属合同，不等于跨项目多册 canon 联邦或跨册 scheduler；本次审阅的 NarrativeFormat/PipelineRunner 都以一个 project 和一份书的章数工作。外部系列圣经应先提供只读导入与明确的跨册事实版本引用，而不是共享一个可写 `story_state.json`。

## 6. 模型接入与 Skill 运行边界

- `ModelRouter` 已支持 writer/architect/editor/guardian/style/judge 角色，别名对应 Scribe/Continuity/Style；优先 v2 provider settings 路由，回退角色级/全局环境。见 `core/model_router.py:19-93`。
- 后端实现包括 Claude CLI、Codex CLI、Anthropic、OpenAI、Azure、Gemini 及 OpenAI-compatible（含别名）。这是源码支持面，不代表本机各后端已经配置或可用；此次未读取配置秘密、连接或调用。见 `core/llm_client.py:190-222`。
- `LLMClient.run_agent` 从 `agents/<role>/prompt.md` 读系统提示，然后走 complete。**仓库 `skills/` 不是全书引擎自动发现的插件目录。**把外部 Skill 放进仓库，不会自动成为所有模型后端的运行时能力。见 `core/llm_client.py:358-363`；仓库 Skill 还明确 Docker 不加载 host Skills，见 `skills/novel-cover-studio/SKILL.md:65`。
- Codex 后端显式 ephemeral/read-only/ignore-rules，并要求纯文本返回、不读改 workspace；Claude 后端则是 `claude -p --output-format json` 与文本 preamble，所示调用没有同样的 read-only/ignore-rules 或禁用 tools 参数。不要把两个后端的隔离强度视为相同；也不要预期外部 Claude hooks/skills 自动在 Codex/API 后端复现。见 `core/llm_client.py:367-408`、`:432-484`。
- 模型 provenance 有实际 role/provider/model 字段，但当前普通章节 `_stage_input_hashes` 的依赖主要是前序稿件/大纲，并未纳入任意外部方法包的内容 hash。**推断：**方法更新后 resume 可能合法复用旧输出；适配时应将 method_id/version/SHA、角色提示词版本和装配结果 hash 纳入 checkpoint 依赖，而不是只在日志里写名称。见 `core/pipeline_models.py:102-117`；`core/pipeline_runner.py:1582-1663`。

## 7. 封面：已有独立生产链，不应由写作 Skill 旁路替代

- 输入是单一严格 JSON handoff，记录完整 Prompt SHA，支持 v1→v2 正规化。见 `core/cover_handoff.py:19-55`。
- Art Director 产出 3–5 个结构化 scene plans，结合视觉证据 ledger、方向验证、novelty fingerprint，并有两次语义/schema repair 预算。见 `core/cover_director.py:23-112`。
- v2 生成要求最新已批准方向、当前 Prompt/brief hash 与 direction hash 一致，并重新校验方向。见 `core/cover_cli.py:82-127`。
- 图片生成逐候选执行；保存真实字节、media/SHA、尺寸/类型、请求信息、质量报告和不可变 attempt history；部分问题触发一次纠正生成。自动替换要求新分数齐全、各项至少 80、已知分数不回退且无 blockers/repair_codes；没有兼容评审时明确 human_review_required。见 `api/cover_service.py:80-136`、`:284-393`；`core/cover_quality.py:180-223`。
- 手动 selection 是独立操作，有 cover-set / active revision CAS 和 stale 确认，重建同一交付包。它目前检查候选 `status == ready`，不等同检查其所有质量分数已过自动修复门禁。见 `api/cover_service.py:208-266`。
- 当前新方向的 profile.v8 明确摄影电影宣传视觉，并非任意媒介通用生成器。外部封面方法若要求插画/其他媒介，应作为待评审产品策略变更，而非覆盖已批准方向。见 `skills/novel-cover-studio/SKILL.md:19-29`。

## 8. EPUB 与 H5 ZIP：已实现的是可验证交付契约

1. **EPUB 正文结构：**一章一 XHTML；前导 story lead 单独 introduction，不挤占第一章编号；OPF 嵌入 classification、serialization、`novel-epub-map.v1`，并区分 story chapters / non_chapter_items。见 `core/compile_epub.py:127-148`、`:151-220`。
2. **导入 sidecar：**`meta/h5-import.json` 的 `novel-h5-import.v1` 使用 `novel-os:<project_instance_id>` 作为书身份，不靠标题/结构猜同一本书。每章拥有稳定 chapter_id、EPUB href/zip_path/spine_index/XHTML hash，并带卷绑定。见 `core/h5_import.py:25-26`、`:104-149`、`:156-199`。
3. **独立修订：**分离 EPUB、本文章节内容、front matter、selected cover、classification、serialization、publication copy 的 hash；无 selected cover 用 keep_existing；旧 EPUB 无显式 map 则标 legacy_epub，不猜章节映射。见 `core/h5_import.py:163-209`。
4. **验证：**重复 ZIP 路径、重复 manifest/spine、非本地 href、XHTML type、章节连续性、spine 全覆盖、EPUB/sidecar 元数据冲突都会被校验。见 `core/h5_import.py:53-153`。
5. **ZIP 外壳：**`package-manifest.json` schema 4、文件清单、book_id、package_revision_sha256；原子替换 ZIP。封面更新会重新发现引擎 publication 源，保留当前 H5 metadata。见 `core/delivery_package.py:68-87`、`:157-227`。
6. **另有旧 MD publication evidence snapshot：**四章及以上输出不可变 H5 V3 root，包含逐章 MD、merged manuscript、finalization/delivery 元信息。少于四章该子投影为 inapplicable，不代表 EPUB sidecar 本身失效。见 `core/h5_publication.py:112-137`、`:199-269`；`core/pipeline_runner.py:3918-3923`。

**接入边界：**当前链是 H5 **导入材料和证据**的生成，不是外部 H5 阅读站的账号、计费、上传 API 或上线结果。外部方法的正文导出不应覆盖这些包；让其产出候选，经现有 Final 权威与 compile/package 链生成。源从不可变 Final head 读取，缺 Final、空章、源完整性失败都会阻断编译，见 `core/pipeline_runner.py:3781-3829`。

## 9. 建议的接入路线（尚未实施）

### P0：方法库接入 Workshop，不接管状态

将外部已核验方法按以下任务分类：选题/受众、角色弧、世界设定、结构、场景、节奏、语言风格、诊断。每个方法只输出 **建议与合同候选**，映射到已有 Section A–E、Prompt body、canonical 分类/篇幅 JSON、商业合同、ending_contract、cover handoff。用户确认后才由 Novel OS intake/foundation 接收。已有设计入口见 `skills/novel-brainstorm-workshop/SKILL.md:168-195`、`:262-274`；所需 Prompt 字段见 `skills/novel-brainstorm-workshop/references/prompt-contract.md:5-59`。

建议增加一个引擎之外的贡献记录格式，而不冒充已有 API：

```text
MethodContribution（拟议）
  method_id, method_version, source_sha256
  applicable_genre, target_role, scope: design | chapter_guidance | critique
  input_contract_ids, source_artifact_revision_ids
  proposed_fields, rationale, evidence_refs, unresolved_assumptions
  approval_status
```

默认输出目录应是专用 analysis/design proposal 区，避免 `outputs/state/`、revision heads、canon ledger、receipts、run manifest、publication metadata。这既适用于纯提示词方法，也适用于外部有 hooks、脚本或状态管理的完整工具：保留其研究/诊断价值，将写权限收口到 Novel OS 服务。

### P1：阶段方法增强，保持契约与恢复语义

- Architect：外部方法转 beat-sheet/ChapterContract 候选，保留 goal→obstacle→active_choice→cost→irreversible_change→local_payoff→ending_pressure；商业模式需 schema v2 字段。既有字段与验证见 `core/contracts.py:200-259`、`:261-320`。
- Scribe / Editor / Style：方法只作为版本化指导片段，保留原输出 block 协议；直接消费返回文本，不给其核心状态写路径。raw response、规范化、sanitization、proposal 持久化继续由 orchestrator 执行。见 `core/orchestrator.py:194-254`。
- ContextPack 入口：新增明确预算、优先级和可丢弃策略；关键事实优先于方法论。记录 omitted facts/method sections，解决“新增方法后上下文反而挤掉 canon”的风险。
- checkpoint：方法版本、源摘要、输出 schema、装配器版本进入 input hashes；变更后按阶段失效，不自动重写已 promotion 的历史。

### P2：外部评审输出转 Evidence，而非直接“批准”

外部批评器返回具备 artifact SHA / exact quote / start-end / finding code / severity / suggested repair 的报告。由本地 parser 先验证引文，再决定 advisory 或 blocking，纳入明示 rubric。保留“完整性 pass”与“语义评审 pass”两个维度，补足目前空 findings 的晋升报告边界。现有 EvidenceSpan 校验可复用 `core/commercial_quality.py:257-288`；晋升资源绑定见 `core/promotion.py:942-997`。

### 暂不采用的路线

- 外部工作流直接写 StoryState、清空/重建 run、改已提交 receipt、以自己的 memory 文件取代 canon。
- 把 Claude 专属 hooks 当成所有 provider 的通用能力；给 API 模型或 Codex 同样行为的假设。
- 因外部方法总分较高，绕过免费窗口、终局、长篇、方向审批或交付源绑定。
- 把外部 EPUB/ZIP 当作本仓库 package 契约的同义物；先做逐字段 adapter 和回归验证。

## 10. 接入前应验证的最小回归面（本次未执行）

1. 方法输出只在候选目录改变，核心 canon/head/receipt 在显式 promotion 前保持不变。
2. 同一项目并发 run/resume/retry 仅有一个执行者；不同项目模型/方法配置无串扰。
3. 中断或 hash 变化只重放需要的阶段，已提交章节保持原 revision/receipt。
4. 外部报告换候选、引文错位/重复、旧合同、未知字段都按预期失败；语义建议不自动获得晋升权。
5. 免费窗口失败阻止进入付费章节；终局失败阻止 compile；方法新增不改变 enforced 合同。
6. 长章中部和久远伏笔有覆盖测试；方法片段预算不吞掉 preserve_facts。
7. introduction 不平移章节/卷/免费窗；仅换封面不改 book_id/body/structure；分类变化只影响对应 metadata revision。
8. 封面方向 hash 变化使旧 approval 失效；未知视觉分数维持人工审阅；无选图时 H5 使用 keep_existing。

仓库已有可参照的测试定义（这里只阅读，未声称通过）：`tests/test_pipeline_runner.py:781-883`（执行锁）、`:1507-1554`（checkpoint/证据）、`:1707-1794`（审阅恢复/hash 变化）、`:710-724`（商业窗口/全书阻断）；`tests/test_h5_import.py:61-154`（intro/封面/身份/分类/坏映射）；`tests/test_skills.py:55-77`（设计与角色边界）。

## 11. 外部比较的核验分工

外部主调查负责核实：执行脚本/工具调用、持久化 memory/canon、hooks/agents 分工、自动重试恢复、结构化输出 schema、质量评估证据、类型资产、跨册管理、模型/CLI 依赖、导出产物，以及许可证/修改分发要求。最终应按“设计方法资产 / 执行能力 / 状态权威 / 证据质量 / 发布契约”五层比较，避免用提示词数量或 Skill 数量代替架构判断。

## 12. 五个拟移植方向：已有部分与真正增量

本节响应外部主调查提出的候选方向，仅核对本地重叠与缺口；外部实现和测试证据以主调查为准。即使外部也有 project lock/CAS/commit，集成仍应只保留一个生产状态权威，而非把两种提交协议叠加。

| 候选方向 | 本地已经存在 | 值得增加的部分与入口 |
|---|---|---|
| 按任务装配知识包 | Workshop 已按 workshop/expand/prompt-revise/canon-aware/chapter-shortcut 路由，并有 genre-adapters、质量/商业/篇幅参考；运行时 ContextPack 已按角色设置预算。见 `skills/novel-brainstorm-workshop/SKILL.md:27-38`、`:262-264`；`core/context_pack.py:12-36`。 | 补方法资产的任务→profile→版本依赖清单和可审计召回，而不是再做一个 ContextPack。将选中的方法片段与遗漏项记入装配记录；读取记录仅证明取到字节，不宣称模型完整理解或遵循。优先在设计层试用，再接角色 prompt builder。 |
| 英文语义去味/反空动作 | Scribe 已有 deep POV、角色词汇、show-don't-tell、节奏、反 clichés；Editor/Style 已检查六个英文模板句、人物注意方式、职业知识、对白策略、羞耻触发和身体反应。见 `agents/scribe/prompt.md:32-65`、`:82-102`；`core/orchestrator.py:1633-1647`、`:2114-2157`。 | 增量应是“动作是否改变注意/关系/信息/决策”“抽象判断是否由具体证据挣得”等语义诊断，而非加长禁词表。当前 Scribe 的固定“三种感官”和“情绪呈现为身体反应”还可能诱导装饰性动作；新规则要明确允许静止/省略，避免把每句内心都变成动作。保留原 Editor/Style，不新增平行终稿写手。 |
| 主角因果与兑现归属 | ChapterContract 已有 active_choice/cost/irreversible_change/local_payoff；商业 schema v2 有 protagonist_causes_turn、seeded/used resource ids；设计门禁阻断未铺垫资源和被动主角，商业 report 核对 agency/local_payoff 精确引文。见 `core/contracts.py:147-170`；`core/commercial_quality.py:1349-1369`、`:497-503`；`core/orchestrator.py:2115-2118`。 | 不重复造 agency gate。补“谁做出动作→谁承担成本→谁获得回报”的角色身份与因果链证据，区分主角主导和他人助力，而非只读布尔标记。先作为商业 review 的带引文建议项，验证稳定后再版本化 schema / rubric。 |
| author / reader / POV 知识隔离 | Character 有 secret 和 knowledge；ChapterContract 有 allowed_knowledge；Scribe 要求只显现 POV 所知，Guardian 明确检查知识越界。见 `core/state_manager.py:24-44`；`core/contracts.py:157-159`；`agents/scribe/prompt.md:32-37`；`agents/continuity_guardian/prompt.md:95-104`。 | 这是较大增量：现有 `New_Information_Revealed` 把“reader/characters learned”写在同一协议字段，parser 仅追加 chapter.new_information；没有独立的作者真相/读者已知/每个角色已知的证据化索引。见 `agents/scribe/prompt.md:219-228`；`core/state_parser.py:751-755`。可先给 Planner 完整真相、Scribe 受限视图、Guardian 完整真相+泄露约束；每条 knowledge candidate 带 fact_id、holder、learned_at、source_revision、reveal_channel，仍走 proposal→promotion，勿由外部直接更新 Character.knowledge。 |
| 作者偏好的作用域 | StoryContract 有 AuthorIntent/content_boundaries/themes/non_negotiables；StyleProfile 有 tone/POV/tense/节奏/词汇/方言、preferred/forbidden words；Workshop 有已确认受众和决策 trace。见 `core/contracts.py:58-109`；`core/state_manager.py:222-241`；`skills/novel-brainstorm-workshop/SKILL.md:95-120`。 | 增量是 global/series/book/role/chapter/scene 的显式 scope、优先级、失效条件与确认来源；当前主要是一本书的 profile，不宜把个人临时偏好全写入全局提示或 canon。将偏好解析层独立于 StoryState 语义事实，只把本次有效结果及版本传给 prompt builder；明确作者硬约束优先于类型建议，场景例外不倒灌全局。 |

两个相邻缺口值得一并处理：

- **跨章“去味”目前有口头要求但上下文未完整承载。**`_generate_style_prompt` 要检查 recent chapters 中模板重复，却只拼当前章、目标 profile 和 ending context；Editor 同样只拼当前 draft 和 ending context。见 `core/orchestrator.py:1615-1661`、`:2094-2125`。增量应包括只读的近期句式/动作/对白策略索引和 exact source refs，而非再次写同一句“避免重复”。
- **区分两个 Guardian 路径。**普通连续性 prompt 对长章裁剪（`:2723-2728`），商业 review prompt 则传完整 candidate_text（`:2362-2405`）。因此“所有审阅都看不到中部”也不准确；需要为每一类检查分别建立覆盖率，而不是用一个全局 read-complete 布尔值。

---

**复核说明：**已核对本报告 112 处显式 `文件:行号` 引用的文件存在性和行范围，并检查文本空白格式；没有据此声称运行测试通过。外部项目的已核验结论与跨项目比较以本轮主代理综合报告为准，本文仅为 Novel OS 静态实现基线与增量接入建议。
