# Novel OS 与 novel-studio 能力对比

研究日期：2026-08-22
研究对象：

- 当前仓库：Novel OS（本地工作区）
- 对比仓库：<https://github.com/Xiaoyangy/novel-studio>
- 对比源码快照：本地 checkout `/private/tmp/novel-studio-compare`，提交 `9da2ff1`

## 结论摘要

当前 Novel OS 已经可以支撑以下工作：

- 基础人物设定和人物关系管理
- 地点、世界观、物品和自定义 Codex
- 章节规划、正文生成、编辑和最终稿管理
- 时间线、剧情线程和连续性校验
- React 写作工作台、编译和导出

但当前项目还没有实现 `novel-studio` 的完整世界推演闭环。当前的 `consequence preview` 是“改写一段正文后预览状态影响”，不是“世界先推进、角色在屏外自主行动、事件经过信息传播后投影到主角视角”的模拟系统。

因此应区分三种结论：

| 能力 | 当前状态 |
|---|---|
| 基础人物和世界设定 | 已实现 |
| 连续性检查和局部后果预览 | 已实现，但范围有限 |
| 独立世界推演和屏外角色行动 | 未实现 |
| 面向现有写作流程的 Web UI | 基本具备 |
| 面向世界模拟的完整生产看板 | 未实现 |

这些能力可以在当前 Python/FastAPI/React 架构内增量实现，但需要补充后端领域模型、持久化、运行状态和前端展示，不能只增加提示词或页面。

## 对比范围和判定标准

本文把“世界推演”定义为一个可持久化、可校验、可恢复的状态演进流程，而不是一次 LLM 生成。至少应满足：

1. 世界状态可以在没有主角镜头的情况下继续推进。
2. 配角或势力有目标、压力、资源、知识边界和可选行动。
3. 每次行动有原因、结果、后续影响和可见性。
4. 事件通过明确的信息传播路径进入主角可感知范围。
5. 章节计划和正文只能使用当前投影允许主角知道的事实。
6. 模拟结果有落盘记录、校验结果和可追溯来源。

“详细人物设定”也分为两层：

- 资料层：姓名、背景、目标、弱点、关系等可编辑字段。
- 行为层：心理维度、知识账本、资源、压力、决策规则、情绪评价和成长状态，能直接驱动后续行动。

## 能力矩阵

| 能力 | 当前 Novel OS | novel-studio | 判断 |
|---|---|---|---|
| 基础人物字段 | `Character` 有姓名、年龄、外貌、欲望、目标、恐惧、弱点、优势、秘密、关系、知识、财产、位置和情绪状态 | 有基础人物资料，并在其上增加心理和动态状态 | 当前已实现基础版 |
| 复杂心理画像 | 没有固定的心理画像 schema；可通过自定义字段保存，但没有统一校验和行为计算 | `CharacterPsychProfile` 支持 Big Five、依恋、情绪向量、认知偏差、价值观、道德基础、能力矩阵和 Character DNA | 当前部分实现 |
| 人物动态 | 有弧线阶段、弧线进度、最后出场章节等字段 | 有目标、压力、资源、知识边界、决策框架、情绪评价、错误模式和成长状态 | 当前不足以驱动自主行动 |
| 世界资料 | `CodexEntry` 支持 character、location、worldbuilding、item、标签、备注和自定义字段 | 有独立世界法典、物理公理、势力、能力、规则覆盖和修订记录 | 当前已实现资料层 |
| 剧情和时间线 | 有 `PlotThread`、`ChapterState`、`TimelineEvent` 和伏笔字段 | 在此基础上增加世界事件、离屏日程和世界 Tick | 当前连续性基础较好 |
| 屏外角色行动 | 没有独立的角色日程账本 | `CharacterAgenda` 记录目标、步骤、状态、阻塞和最后推进章节 | 当前缺失 |
| 世界事件传播 | 当前主要由章节状态更新和连续性发现表达 | `WorldEvent` 记录发生章节、参与者、后果、最早可见章节和传播路径 | 当前缺失 |
| 世界 Tick | 没有独立的世界推进游标 | `WorldTick` 记录离屏世界推演覆盖范围 | 当前缺失 |
| 章节世界模拟 | 没有独立的章节模拟 bundle | `ChapterWorldSimulation` 保存角色决策、蝴蝶效应和主角投影 | 当前缺失 |
| 因果投影 | `consequence preview` 只处理选中文本的改写后果 | 世界先推进，再由 `ProtagonistDecisionProjection` 约束章节计划 | 当前缺失 |
| 连续性检查 | 已有确定性连续性引擎、Guardian 报告、例外和章节级检查 | 还校验模拟因果链、信息可见性和投影来源 | 当前已有，但校验对象较窄 |
| Web 写作工作台 | 项目、章节、Codex、关系、编辑器、快照、评论、连续性、编译和导出 | 有 Dashboard，但重点是运行证据和生产看板 | 当前编辑能力较完整 |
| 世界模拟看板 | 没有对应后端数据和专用页面 | 有 setting、cast、offscreen、growth、quality 等接口和页面 | 当前缺失 |

## 当前 Novel OS 的代码证据

### 人物、世界和剧情资料层

`Character` 是当前人物资料的核心结构，包含基础人物设定和当前状态字段：

- [core/state_manager.py:23](../../core/state_manager.py#L23)

世界资料由 `CodexEntry` 承载，支持地点、世界观和物品，并提供 `fields` 保存扩展信息：

- [core/state_manager.py:106](../../core/state_manager.py#L106)

剧情线程、章节状态和时间线事件已经存在：

- [core/state_manager.py:128](../../core/state_manager.py#L128)
- [core/state_manager.py:153](../../core/state_manager.py#L153)
- [core/state_manager.py:217](../../core/state_manager.py#L217)

这些结构足以支撑“资料管理”和“章节连续性”，但没有形成独立的世界运行时。

### Consequence Preview 的边界

当前后果预览的实现流程是：

```text
选择正文片段
  -> 调用 Scribe 重写
  -> 解析 SCRIBE_STATE_UPDATE
  -> 对内存状态做 dry-apply
  -> 比较连续性 findings
  -> 返回 deterministic 和 predicted consequences
```

相关代码：

- [core/consequence.py:1](../../core/consequence.py#L1)
- [api/services.py:1282](../../api/services.py#L1282)
- [api/routes.py:888](../../api/routes.py#L888)

这条链路回答的是“修改这段正文可能破坏什么”，而不是“正文之外的世界已经发生了什么”。它没有独立的角色行动账本、事件传播路径、屏外世界事件流或主角投影。

### 当前 Web UI 的边界

当前 Project Dashboard 已经加载 Codex、章节、关系和连续性，并提供 Chapter Board、Outliner、Compile Panel 和 Codex Proposals：

- [web/src/routes/ProjectDashboard.tsx:56](../../web/src/routes/ProjectDashboard.tsx#L56)
- [web/src/routes/ProjectDashboard.tsx:246](../../web/src/routes/ProjectDashboard.tsx#L246)
- [web/src/routes/ProjectDashboard.tsx:285](../../web/src/routes/ProjectDashboard.tsx#L285)

API 已经覆盖项目运行、编译、Codex、关系、章节阶段、快照、评论和后果预览：

- [api/routes.py:136](../../api/routes.py#L136)
- [api/routes.py:208](../../api/routes.py#L208)
- [api/routes.py:617](../../api/routes.py#L617)
- [api/routes.py:743](../../api/routes.py#L743)
- [api/routes.py:888](../../api/routes.py#L888)

所以当前 UI 可以展示已经存在的写作状态，但不能展示尚未存在的世界模拟状态。

## novel-studio 的关键实现差异

### 章节世界模拟

`ChapterWorldSimulation` 把世界模拟定义为章节正文之前的事实来源，并明确要求“世界先推进，主角视角计划从投影派生”。

它包含：

- `CharacterWorldDecision`：每个角色的目标、压力、资源、知识边界、可选行动、实际决策、原因、行动时长、结果和行动后状态。
- `DecisionButterflyEffect`：后续影响、目标、传播路径、抵达章节、可见性和对主角的影响。
- `ProtagonistDecisionProjection`：主角可观察效果、隐藏压力、可选决策、计划约束和因果链。

源码：

- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/internal/domain/chapter_simulation.go#L5-L20>
- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/internal/domain/chapter_simulation.go#L79-L125>

### 屏外日程和世界事件

`CharacterAgenda` 是角色在离屏期间的运行态，记录当前目标、步骤、状态、阻塞原因和最后推进章节：

- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/internal/domain/offscreen_agenda.go#L20-L75>

`WorldEvent` 记录事件发生时间、参与者、后果和信息传播。`VisibilityChapter` 约束事件最早何时可以进入主角感知：

- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/internal/domain/world_event.go#L5-L25>
- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/internal/domain/world_event.go#L46-L71>

### 人物心理与行为约束

外部项目把心理画像作为独立结构，并将其用于动态决策，而不是只展示在人物卡片上：

- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/internal/domain/psych_profile.go#L8-L25>

这也是两个项目在“人物细节设定”上的关键差异：当前 Novel OS 可以保存很多字段，外部项目则把这些字段组织成可被模拟消费的行为模型。

## Web UI 对比

当前 Novel OS 更接近：

```text
写作编辑器 + 章节流水线 + Codex + 连续性工具
```

`novel-studio` 更接近：

```text
世界模拟生产看板 + 人物成长看板 + 运行证据看板
```

外部项目 Dashboard 的 API 会读取：

- 世界 Tick、世界事件和章节世界增量
- 屏外角色日程和角色旅程
- 人物心理、知识账本和决策框架
- 人物成长轨迹和决策流
- 章节质量、审稿结果和门禁状态

相关实现：

- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/services/dashboard/server.py#L1261-L1418>
- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/services/dashboard/server.py#L1482-L1603>
- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/services/dashboard/static/index.html#L1244-L1275>
- <https://github.com/Xiaoyangy/novel-studio/blob/9da2ff1/services/dashboard/static/index.html#L1337-L1393>

当前 Novel OS 尚无以下专用视图：

- World Simulation
- Offscreen Agenda
- World Tick / World Event
- 角色独立行动和决策流
- 事件传播路径和信息差图
- 主角可见投影与隐藏压力
- 弧级模拟封存和模拟证据
- 全书级生产质量和 acceptance receipt

## 推荐技术路线

### 1. 保持现有双存储边界

当前架构已经区分文件型 Agent 工作区和 API/UI 使用的 SQLite 数据库。世界模拟不应直接把大量临时状态写进现有 `story_state.json`，建议增加独立的模拟状态目录或表：

```text
outputs/simulation/
  world_tick.json
  world_events.jsonl
  offscreen_agenda.json
  chapter_simulations/
  projections/
  simulation_manifest.json
```

这样可以保留现有运行的兼容性，也能在模拟失败时丢弃投影状态而不破坏正史。

### 2. 增加一组一等领域模型

第一版建议实现：

```text
CharacterDynamicsProfile
CharacterAgenda
WorldEvent
WorldTick
ChapterWorldSimulation
ProtagonistProjection
SimulationReceipt
```

其中 `Character` 和 `CodexEntry` 仍然保存作者资料；动态模型保存“此时此刻角色正在做什么”和“为什么这么做”。两者不能混成一个无限扩展的 JSON 字段。

### 3. 增加可恢复的模拟服务和 CLI

建议的阶段边界：

```text
world-init
  -> world-tick
  -> simulate-chapter-world
  -> project-chapter
  -> write/edit/validate
  -> seal-arc
  -> compile/export
```

其中：

- `world-tick` 推进离屏角色和势力。
- `simulate-chapter-world` 生成该章所有相关角色的决策和后果。
- `project-chapter` 只把主角可见事件和隐藏压力投影给 Architect/Scribe。
- `seal-arc` 固化本弧模拟来源，避免正文生成后世界状态漂移。

每个阶段都应记录 `run_id`、`simulation_id`、输入快照、产物路径、模型、时间、状态和错误原因。

### 4. 将正文生成绑定到投影

目标链路应变成：

```text
世界模拟结果
  -> 主角可见事件
  -> 主角可选决策
  -> 章节大纲
  -> 正文
  -> 连续性和信息边界校验
```

如果章节大纲没有对应的模拟投影，系统应将其标记为缺少因果来源，而不是只依赖模型自行补全。

### 5. 增加对应的 Web 页面和 API

建议新增以下页面：

| 页面 | 主要内容 |
|---|---|
| Simulation Overview | 当前 Tick、模拟覆盖率、运行状态和警告 |
| Offscreen Agenda | 配角和势力的目标、步骤、阻塞和停滞情况 |
| World Events | 事件流、参与者、后果、可见章节和传播路径 |
| Character Dynamics | 心理画像、知识边界、资源、压力和决策规则 |
| Growth | 人物弧线、状态变迁和决策时间线 |
| Causal Graph | 角色决定到世界影响再到主角投影的因果链 |
| Quality | 连续性、信息越界、投影覆盖和章节门禁 |

API 可以先增加只读接口，等模拟服务稳定后再增加启动和重跑接口：

```text
GET  /api/projects/{id}/simulation
GET  /api/projects/{id}/offscreen
GET  /api/projects/{id}/world-events
GET  /api/projects/{id}/characters/{name}/dynamics
GET  /api/projects/{id}/growth
POST /api/projects/{id}/simulation/tick
POST /api/projects/{id}/simulation/chapters/{number}
```

### 6. 先补核验，再补 UI

世界模拟最重要的不是“生成更多内容”，而是能证明生成内容没有越界。至少需要校验：

- 角色不能使用其知识边界之外的信息。
- 事件可见章节不能早于事件发生章节。
- 屏外角色的行动必须有目标、原因和状态变化。
- 主角正文只能使用已经传播到主角的事件。
- 蝴蝶效应的抵达章节必须和章节大纲一致。
- 已封存弧的模拟状态不能被普通正文生成静默修改。

## 分阶段验收标准

### MVP：可运行的世界推演内核

- 能初始化一个世界 Tick。
- 至少两个屏外角色有可推进的 Agenda。
- 能生成一条带传播路径和可见章节的 WorldEvent。
- 能为一个章节生成 `ChapterWorldSimulation`。
- 能从模拟结果生成主角投影。
- 模拟状态和正文状态分开落盘。

### 第二阶段：正文因果绑定

- 章节计划引用 simulation ID。
- 正文生成无法绕过缺失的主角投影。
- 连续性检查能发现知识越界和可见性越界。
- 失败任务可以从上一个 checkpoint 恢复。

### 第三阶段：完整 Web 看板

- 可以查看世界 Tick、离屏日程、世界事件和人物决策流。
- 可以从角色决定追踪到章节正文中的可见结果。
- 可以查看模拟覆盖率、质量门禁和运行日志。
- 页面数据来自持久化 API，而不是前端临时拼接。

## 风险和边界

1. 不应把 LLM 的自然语言输出直接当作世界正史，必须经过 schema 校验和状态提交。
2. 不应把心理画像做成没有行为含义的装饰字段；每个字段都应说明会影响什么决策。
3. 不应让普通章节生成直接覆盖 canonical world state，应区分 projected state 和 canonical state。
4. 不应为了复刻外部项目而整体迁移到 Go；当前 Python/FastAPI/React 已足够承载第一版模块化单体。
5. 当前已有运行任务和旧项目必须保持兼容，模拟状态应使用新增目录、版本字段和迁移策略。

## 最终判断

当前 Novel OS 已经具备成为“人物设定 + 世界设定 + 连续性管理 + 章节写作”系统的基础，也具备实现世界推演的技术承载能力。

但截至本文研究版本，它还不是 `novel-studio` 意义上的世界模拟系统：没有屏外角色自主行动、世界 Tick、世界事件传播、章节模拟投影和对应的生产看板。

Web UI 方面，当前项目的编辑和写作体验已经较完整；如果目标是展示完整的世界推演过程，则需要先补模拟后端和持久化，再增加 Dashboard，不能只在现有页面上添加几个展示卡片。
