# Novel OS CLI 能力研究与一键全流程方案

> 实施状态（2026-08-21）：本文下方的“现状”描述对应实施前基线。当前工作区已经按本方案新增 `run`、`resume`、`run-status`、`retry` 全书 CLI，持久 run manifest/阶段快照、Architect foundation、Style Curator、人工/自动 Final 晋级及统一编译；实现入口见 `core/pipeline_runner.py` 和 `core/pipeline_models.py`。

研究日期：2026-08-21
研究对象：<https://github.com/mrigankad/Novel-OS>
本地源码基线：`origin` 指向上述仓库，当前分支为 `dev`，`origin/dev` 与 `origin/HEAD` 均指向提交 `1bd4b5725a8225462873571eb9f9d26f5496ae83`。本地 `git log` 的提交标题为 `feat(web): edit Codex entries from the studio`；研究时无法重新解析 GitHub DNS，因此版本判断以该 checkout 为准。

## 结论先行

当前 Novel OS 已经具备“按章节运行多代理阶段”的核心能力，也有可直接 import 的 `NovelOrchestrator`。但是它**没有**一个接收单个 prompt、自动完成全书推演到最终稿的 CLI 入口。现有入口是多个相互独立的命令，标准流程仍要求用户逐章执行：

`plan chapter -> write -> edit -> check -> validate -> approve`。

因此，用户要的能力应作为一个新的、可恢复的 pipeline runner 增加，而不是继续用 shell 循环拼接现有命令。原因是现有阶段方法主要通过打印错误返回，部分失败不会转成非零进程码；此外，当前 CLI 没有最终稿晋级命令，核心导出只寻找 revised/draft 文件。

## 一手来源与现状证据

### CLI 命令面

根命令由 `core/orchestrator.py` 的 `main()` 构建，当前 subparser 只有 `init`、`character`、`plot`、`plan`、`write`、`edit`、`validate`、`approve`、`check`、`status`、`export`、`setup`（[`core/orchestrator.py:1007-1108`](../../core/orchestrator.py#L1007-L1108)）。实际 `--help` 输出中也没有 `run`、`pipeline`、`batch` 或 `all` 命令。

README 的 CLI 参考表与源码一致：outline、chapter plan、write、edit、validate、check、approve、status、export 是分开的命令（[`README.md:386-403`](../../README.md#L386-L403)）。官方工作流文档也把章节流程写成六个独立的 shell 调用（[`docs/WORKFLOWS.md:21-52`](../../docs/WORKFLOWS.md#L21-L52)）。

`docs/API.md` 虽然提供了 Python 高层调用示例，但仍是逐个方法调用，没有全书方法（[`docs/API.md:138-157`](../../docs/API.md#L138-L157)）。

### 每个阶段实际做什么

| 阶段 | 证据 | 当前行为与产物 |
|---|---|---|
| `plan outline` | [`core/orchestrator.py:308-336`](../../core/orchestrator.py#L308-L336) | 生成三幕结构和空章节模板，写入 `outputs/outline.json`。代码明确注明“for now, we create a template outline”，这里不是模型推演。 |
| `plan chapter` | [`core/orchestrator.py:400-427`](../../core/orchestrator.py#L400-L427) | 创建/更新 `ChapterState`，调用 `architect`，写 `outputs/chapter_NNN_prompt.md` 和 `outputs/chapter_NNN_outline.md`。 |
| `write` | [`core/orchestrator.py:524-567`](../../core/orchestrator.py#L524-L567) | 读取章节 outline/context pack，调用 `scribe`，写 `outputs/manuscript/chapter_NNN_draft.md`，并把结构化 state block 合并进 StoryState。 |
| `edit` | [`core/orchestrator.py:591-628`](../../core/orchestrator.py#L591-L628) | 读取 draft，调用 `editor`，写 `chapter_NNN_revised.md`；支持 line/developmental/pacing/dialogue/tension 五种模式。 |
| `check` | [`core/orchestrator.py:910-915`](../../core/orchestrator.py#L910-L915) | 运行本地确定性连续性检查，不调用模型；存在 critical finding 时返回数量。 |
| `validate` | [`core/orchestrator.py:734-777`](../../core/orchestrator.py#L734-L777) | 先运行确定性 pre-check，再调用 `continuity_guardian`，写 `outputs/feedback/chapter_NNN_continuity_report.md`，成功后将章节设为 `validated`。 |
| `approve` | [`core/orchestrator.py:872-906`](../../core/orchestrator.py#L872-L906) | 仅在 Guardian 状态不是 `FAIL` 时把章节设为 `complete`。它不创建 `final.md`。 |
| `export` | [`core/orchestrator.py:956-990`](../../core/orchestrator.py#L956-L990) | 仅导出 `complete` 章节，并按 `_revised`、`_draft` 顺序取文件；核心 CLI 不读取 `_final`。 |

### Agent、状态与文件契约

`StoryState` 以 `outputs/state/story_state.json` 为持久状态，章节包含 `planned`、`drafted`、`editing`、`edited`、`validated`、`complete` 等状态和 plot/character/foreshadowing/continuity 字段（[`core/state_manager.py:153-176`](../../core/state_manager.py#L153-L176)）。保存时会原子替换旧状态并保留 `.bak`（[`core/state_manager.py:375-404`](../../core/state_manager.py#L375-L404)）。

LLM 返回的 `[SCRIBE_STATE_UPDATE]`、`[EDITOR_STATE_UPDATE]`、`[CONTINUITY_REPORT]` 等块由 `state_parser` 解析并写回状态；这是跨章节记忆的实际桥梁（[`core/state_parser.py:1-20`](../../core/state_parser.py#L1-L20)、[`core/state_parser.py:201-258`](../../core/state_parser.py#L201-L258)）。

代理调用入口是 `LLMClient.run_agent()`，它从 `agents/<name>/prompt.md` 加载系统提示词（[`core/llm_client.py:258-274`](../../core/llm_client.py#L258-L274)）。当前 CLI 真正调用的 agent 是 Architect、Scribe、Editor、Continuity Guardian；虽然 `_run_agent_or_save_prompt` 对 `style_curator` 有清理分支（[`core/orchestrator.py:88-100`](../../core/orchestrator.py#L88-L100)），但 CLI 没有 `style`/`curate` 方法或命令。API 的 `PHASES` 也只注册 `plan_outline`、`plan_chapter`、`write`、`edit`、`validate`、`approve`，没有 Curator 或全流程阶段（[`api/services.py:46-62`](../../api/services.py#L46-L62)）。

### API/UI 也没有全流程编排

FastAPI 的运行接口每次只接受一个 `stage` 和参数，然后提交一个后台 job（[`api/routes.py:208-225`](../../api/routes.py#L208-L225)）。`JobRunner` 在单进程 daemon thread 中运行并只保存内存 job 状态（[`api/jobs.py:1-62`](../../api/jobs.py#L1-L62)）。前端 hook 也是“启动一个 stage、轮询一个 job、完成后 refetch”（[`web/src/hooks/useRunPhase.ts:14-59`](../../web/src/hooks/useRunPhase.ts#L14-L59)），所以页面上的 `Agent running...` 不代表全书流水线。

API 额外实现了 Final 的 review/promote 语义：`POST .../stages/{stage}/review` 接受或拒绝 AI 阶段，`POST .../final/promote` 晋级到 Final（[`api/routes.py:821-858`](../../api/routes.py#L821-L858)）。这套能力没有暴露为核心 CLI 的等价命令。API 导出会优先读取 human-reviewed Final，再退回 revised/draft（[`api/services.py:1649-1666`](../../api/services.py#L1649-L1666)），与核心 CLI 的导出顺序不同。

### Prompt 入口现状

API 创建项目支持 `premise` 字段（[`api/models.py:443-448`](../../api/models.py#L443-L448)），服务层将它写入 `StoryState.metadata`、story bible 和 `outputs/story_bible.md`（[`api/services.py:1483-1521`](../../api/services.py#L1483-L1521)）。核心 CLI 的 `init` 只接受 title/genre/author，没有 `--prompt` 或 `--premise` 参数（[`core/orchestrator.py:1028-1033`](../../core/orchestrator.py#L1028-L1033)）。因此，直接把完整 master prompt 塞到 premise 虽然能保存，但会把“创作约束”和“故事前提”混成一个字段；一键入口应保留原始 prompt，并生成结构化 brief。

## 与“prompt -> 最终落稿”目标的差距

1. **缺少总编排命令。** 没有 run manifest、章节循环、断点恢复、重试或全书完成判定。
2. **总纲不是真正的 Architect 推演。** `plan_outline` 目前是确定性模板，不能从 prompt 生成完整 story bible、角色弧线、情节线程和章节大纲。
3. **输入契约不完整。** CLI 不能读取 prompt 文件/stdin；项目初始化与 prompt 内容没有统一的 manifest。
4. **Agent pipeline 不完整。** Curator 在 agent 定义中存在，但没有 CLI/API 阶段；也没有核心 CLI 的 Final review/promote。
5. **自动化错误语义不足。** `_run_agent_or_save_prompt` 捕获 `LLMError` 后只打印并返回 `None`（[`core/orchestrator.py:62-79`](../../core/orchestrator.py#L62-L79)）；CLI 路由直接调用方法，不统一设置失败退出码（[`core/orchestrator.py:1166-1192`](../../core/orchestrator.py#L1166-L1192)）。因此，简单 shell `cmd1 && cmd2` 可能在模型调用失败后继续执行。
6. **Final 来源不统一。** API 把 Final 作为人审后的 DB-first 内容，核心导出只认 revised/draft；若一键 CLI 不先定义晋级策略，所谓“最终稿”会有歧义。
7. **API job 不适合长时间全书运行。** 当前 job 历史只在进程内存，服务重启无法恢复；全书运行需要持久 run 状态或直接走本地 CLI。

## 建议的技术方案（增量实现）

### 1. 增加显式的 `run`/`pipeline` CLI

建议新增独立的 runner 模块（例如 `core/pipeline_runner.py`），由现有 `NovelOrchestrator` 执行单阶段，避免把全书状态机继续塞进 `main()`。CLI 只负责参数、日志和退出码，runner 负责状态机。

建议命令：

```bash
python -m core.orchestrator run \
  --prompt prompt.md \
  --title "The Life She Kept" \
  --genre "Contemporary Adult" \
  --chapters 20 \
  --words 60000 \
  --auto-approve \
  --output markdown
```

最小参数契约：

- `--prompt FILE`，支持 `-` 表示 stdin；原文保存为 `outputs/input/prompt.md`。
- `--project DIR` 或 `--title`，两者只能选一个初始化方式；已有项目通过 `--resume` 继续。
- `--chapters`、`--words`、`--pov`、`--edit-mode`、`--model` 等可覆盖默认值。
- `--resume RUN_ID`、`--from PHASE`、`--until PHASE` 支持中断后续跑。
- `--auto-approve` 明确表示跳过人工 review；默认遇到 Guardian `FAIL` 或 critical pre-check 必须暂停。
- `--dry-run` 只落 prompt，不产生模型调用，沿用现有各阶段 dry-run 契约。

### 2. 将“完整推演”拆成可检查的阶段

建议的一次 run 顺序如下，所有阶段都在 checkpoint 后才进入下一阶段：

```text
intake
  -> brief/story bible (Architect, structured JSON + human-readable markdown)
  -> cast/codex/plot threads (Architect, schema validated)
  -> outline (Architect, real LLM; not current template-only plan_outline)
  -> for chapter 1..N, in order:
       plan chapter
       write draft
       deterministic check
       edit/revise
       deterministic check again
       Guardian validate
       Style Curator (optional but explicit)
       review/promote to final, or pause for human review
  -> compile/export only when final policy is satisfied
```

章节必须串行，原因是下一章的 context pack 依赖上一章写入的 StoryState；可在未来把不依赖前文的 outline 预计算并行化，但 draft/validate 不应并行覆盖同一 `story_state.json`。

### 3. 增加持久的 run manifest 与结构化结果

不要依赖 stdout 判断成功。每次阶段返回 `StageResult`，至少包含：`run_id`、`phase`、`chapter`、`status`、`attempt`、`started_at`、`finished_at`、`artifact_paths`、`findings`、`error`、`provider/model`。

manifest 可放在 `outputs/runs/<run_id>/run.json`，并在 `StoryState.session_log` 中写入摘要；每个阶段完成后采用 temp + `os.replace` 保存。恢复时读取 manifest，跳过已确认成功且产物存在的阶段；产物缺失或 hash 不符则重新执行。

阶段失败必须：

- 记录可读错误和原始 prompt 路径；
- 区分 retryable（网络/速率限制）和 blocked（内容/连续性 FAIL）；
- 达到重试上限后以非零退出码结束；
- 不把失败章节标记为 `drafted`/`validated`/`complete`；
- 保留上一版 artifact，不用失败输出覆盖有效版本。

这也修复了当前 CLI 被 shell `&&` 误判的问题。

### 4. 明确定义 Final 策略并复用 API 语义

推荐默认策略是 `review_required`：Guardian 通过后只生成候选 revised，用户确认后再写 `chapter_NNN_final.md`；无人值守场景必须显式传 `--auto-approve`，并在 manifest 记录 `approval_policy=auto`。

为了避免 CLI/API 分叉，应提取一个共享的 `promote_final`/`review_stage` service：

- `final` 是 canonical manuscript；
- `draft`/`revised` 是不可变 provenance；
- `export` 优先 Final，缺失时按策略拒绝或明确 `--allow-unreviewed`；
- 每次晋级保存来源阶段、agent、model、时间和 run_id。

### 5. Prompt 与结构化 brief 分离

建议把用户输入分成三层：

1. `prompt.md`：用户原文和创作约束，永不被模型自动改写。
2. `brief.json`：由 Architect 解析出的 title/premise/genre/audience/tone/length/content policy 等字段，经过 schema 校验。
3. `story_bible.md` + StoryState：角色、地点、世界规则、情节线程、时间线等可供后续 context pack 使用的事实。

这样既能把完整英文 master prompt 传给模型，又不会把 169 行操作指令误当作 2-4 句 premise；后续每章 prompt 只注入相关 brief/context pack，避免 token 无界增长。

### 6. API/UI 的后续接入（可选第二阶段）

第一版 CLI 可绕过 FastAPI，直接调用 file-based core，降低长任务被 API 重启打断的风险。若要在 Studio 中启动同一流程，新增：

- `POST /api/projects/{id}/runs`：创建持久 run，提交 prompt/策略/章节范围；
- `GET /api/runs/{run_id}`：返回当前 phase、chapter、attempt、日志和错误；
- `POST /api/runs/{run_id}/cancel`：协作式取消；
- DB `pipeline_run`、`pipeline_step` 表：替代当前仅内存的 `JobRunner` 历史。

UI 可以继续轮询，但按钮只启动一个 run，而不是让用户逐个点击 stage；同时保留手动单阶段入口用于重跑和人工修改。

## 验证清单

实现一键入口后，至少应有以下自动化测试：

- prompt 文件和 stdin 可被读取，原文、brief、run manifest 都被保存；
- fake LLM 下可完整跑 N=2 章节，所有 state update block 正确合并；
- 每个阶段只在前置产物存在时执行，失败返回非零并停止；
- Guardian `FAIL` 阻止自动晋级，`--auto-approve` 仍留下明确审计记录；
- 进程中断后 `--resume` 从最后一个成功 checkpoint 继续，不重复覆盖有效 artifact；
- revised/final/export 的来源和章节状态一致，未完成章节不会进入最终稿；
- dry-run 不调用 LLM；网络错误按重试策略处理；
- 现有单阶段 CLI、API JobRunner 和手工 Final 编辑回归测试保持通过。

## 研究边界

本笔记只使用仓库自身 README、docs、core、api、web 源码作为一手来源；没有把第三方博客或模型生成内容当作事实。GitHub 远端在研究时无法通过网络重新拉取，因此源码引用以本地 checkout 的提交 `1bd4b5725a8225462873571eb9f9d26f5496ae83` 为准。
