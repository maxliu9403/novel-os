# 封面骨架来源定位审查

日期：2026-10-08

状态：仅记录事实与建议，未开发。前序方案见 [封面设计审查与优化方案](cover-design-audit-and-optimization-2026-10-08.md)。

整合后的来源契约、分析流程与实施顺序见 [基于完整小说骨架的封面工作台优化方案](../superpowers/specs/2026-10-08-skeleton-driven-cover-studio-design.md)。

后续复核更新：该会话已到 revision 17 并启动项目 `my-husband-s-secret-daughter-lived-across-the-street-d7c6589c`。当前讨论骨架、确认稿、v17 快照与项目输入副本 SHA 一致。下文 revision 14/16、尚未启动的描述保留为当时的审查快照；最新项目逐项路径与校验值见 [小说骨架定位清单](novel-skeleton-inventory-2026-10-08.md)。

## 1. 核心结论

程序应通过当前服务的 root/workspace、会话 ID 或项目 ID 解析来源，再用实际内容 SHA-256 绑定分析结果。当前草稿、历史 Markdown 快照、确认稿、项目运行副本是不同来源，不能仅用“任务 ID + revision”假定磁盘上必有同号 Markdown。

现有系统已保存和读取完整骨架，但封面导演主要收到结构化 brief 与筛选的证据段落，完整骨架理解需要新增阶段。

## 2. 指定会话的实际状态

会话 ID：`d7c6589ce29349ae99121b5f771fd6d1`。

宿主机目录：`docker-data/workshop_v2/d7c6589ce29349ae99121b5f771fd6d1/`。

| 内容 | 实际位置／状态 |
| --- | --- |
| 当前骨架 | `session.json` 的 `prompt_md` 字段 |
| 历史 Markdown | `versions/12.md`；本次检查时 versions 中只有这一份 `.md` |
| 会话修订号 | 本轮第一次读取为 14，后续复核为 16；两次当前骨架 SHA 相同 |
| 状态 | `status=ready`、`ready_for_confirmation=true` |
| 整体确认 | `prepared=null`，不能视为已批准骨架 |
| 项目关联 | `project_id=null`；本轮未发现对应项目副本 |
| 书名 | 命名确认字段为空，尚未整体确认最终书名 |

当前 `prompt_md` 的 UTF-8 内容 SHA-256：

```text
de3f11e5d1bc608253565df4411c5a19ccf54a409c96d41cdc11a3e9e269a8f4
```

`versions/12.md` 文件 SHA-256：

```text
35850a159e3821d7aee19561340c8854b7c590d66ec3a5b20fa94a32cb5f8103
```

两者差异位于 `NARRATIVE_FORMAT_JSON`：当前内容确认了叙事形式并补充格式／篇幅／审计配置，故事正文一致。叙事形式确认不等于整体骨架确认。

以上是读取时的状态快照。会话正在变化，不应把报告里的修订号硬编码为今后的当前版本。

## 3. 现有保存与定位链路

### 3.1 讨论阶段

`api/workshop.py:WorkshopService` 从项目根目录派生 Workshop 根：

```text
默认 workspace：
<projects.root.resolve().parent>/workshop_v2/<session_id>/session.json

其他 workspace：
<projects.root.resolve().parent>/workshop_v2/<workspace.id>/<session_id>/session.json
```

当前骨架内容以 `session.json.prompt_md` 为准。接口：

```text
GET /api/workshop/sessions/<session_id>/prompt
```

直接返回该字段的当前 Markdown，没有历史版本参数。下载文件名仅用于附件命名，不代表源文件路径。

### 3.2 历史快照

`versions/<revision>.md` 只在某些事件下写入，例如新骨架成功落库和 prepare。会话提交、状态变化、命名等操作也会增加 revision，却不一定创建 Markdown 文件。

因此 revision 是会话状态修订号，不是连续的骨架内容版本号。请求明确历史版时，需要检查对应文件存在并核对 hash；不能静默改用当前稿或最近一份文件。

当前 prepare 在某些情况下可能不增加 revision 就重写同名版本文件，因此这些文件也不是严格的不可变历史。后续分析应绑定内容 hash，保留自己的不可变输入快照。

`candidates/` 可能包含校验未通过的候选稿，不能作为已批准骨架。

### 3.3 已确认但尚未启动

`prepare()` 写入 `prepared.md` 和 `versions/<revision>.md`，并记录：

```text
session.prepared.revision
session.prepared.project_name
session.prepared.prompt_filename
session.prepared.prompt_sha256
```

当前有效确认状态需同时满足：prepared 存在、prepared.revision 与当前 session.revision 一致、当前 prompt_md 的 SHA-256 与 prepared.prompt_sha256 一致。

新讨论可能清空 prepared，但旧 `prepared.md` 仍留在磁盘。不能仅以文件存在或 `status=ready` 判断已批准。

### 3.4 已启动小说项目

启动读取已确认的 `session.prompt_md`，保存为：

```text
<ProjectService.base_dir>/<project_id>/outputs/input/prompt.md
```

随后会话记录 `project_id` 和 `job_id`。项目 ID 从已确认书名的 slug 和会话 ID 前缀生成；定位时使用已保存的 project_id，不能重新猜书名或重算 slug。

项目通过 `ProjectService.project_path(project_id)`／`tenancy.project_dir` 解析：默认 workspace 直接在 root 下；其他 workspace 位于 `root/ws-<workspace.slug>/` 下。会话目录使用 workspace.id，项目目录使用 workspace.slug，不能混用。

宿主机本地路径 `docker-data/projects/...` 是本环境实例，不应写死在应用逻辑中。

项目内 `outputs/input/prompt.md` 是该项目实际接收的骨架副本。讨论后来更新不能自动替换已启动项目的源版本；需要明确选择并记录更新。

通过 Prompt intake/Workshop 启动的项目会保存该副本；不能假定所有历史或其他入口创建的项目都必然有完整骨架。封面现有 legacy 恢复可在缺失骨架时依据 foundation/state 工作，这种来源应标明不完整。

## 4. 封面实际读取与模型输入

Web 创建封面方向的链路：

```text
project_id → ProjectService 解析项目目录
→ resolve_cover_brief_v2
→ collect_visual_evidence
→ director.plan
```

- 读取 `outputs/input/prompt.md` 的完整内容并计算 hash。
- 有 `COVER_HANDOFF_BEGIN/END` 时，解析其中 JSON；不是依赖固定的“第 15 节”编号。
- 无 handoff 时，从 foundation、brief、state、ending 等恢复字段，并进行有限人物资料提取。
- 从完整骨架中按视觉词、动作词、人名等规则筛选最多 8 段。
- 发给导演的 evidence ledger 最多 24 条，每条 summary 截到 420 字符；导演输入没有完整骨架正文。
- 现有 source bundle 的全文 hash 用于检测来源变化，不能证明模型看过全部内容。

只读重建一个真实项目输入：Pregnant and Done Waiting 的骨架为 72,774 字符，进入导演的骨架摘录仅 4 条、共 1,272 字符。导演还收到 brief 和其他来源证据，不能把 1,272 字符说成全部故事信息。

小说 run manifest 的 `spec.prompt_path` 可能保留原始外部路径或 stdin 标记 `-`；封面通常使用项目副本，不沿 manifest 回找外部文件，也不展开骨架中的 Markdown 链接。

## 5. 建议的来源约定（尚未实现）

定位与内容身份分开：

- 定位：服务端从已认证 workspace，结合 session_id/project_id 和来源选择器找到内容。
- 内容身份：对本次实际读取的内容计算 SHA-256，记录观察到的会话修订号和批准状态。
- 分析输入：将同一份已读取内容冻结为不可变快照，分析和后续导演引用该快照及 hash。

来源选择器建议明确区分：

| 来源 | 选择与读取 |
| --- | --- |
| workshop 当前稿 | session_id + current → session.prompt_md |
| workshop 已确认稿 | session_id + prepared → 核对 prepared 与当前内容 |
| workshop 指定存档 | session_id + archived + revision → versions/N.md + hash |
| 项目输入副本 | project_id → 项目 outputs/input/prompt.md + hash |

前端不传任意磁盘路径。对于当前稿可传 `expected_content_sha256` 防止分析时读到另一个内容版本；服务端返回 `observed_session_revision`，不把它当作独立骨架版本号。

若会话 revision 变化而源内容 hash 不变，通常可复用全文分析；批准资格需要单独重新核对。若内容 hash 变化，旧分析保留并标明旧来源，不能无提示切换成新版本。

缺失文件、版本不存在、hash 不符、跨 workspace、源内容不完整应明确返回对应状态，不能用同名书或“最新 MD”猜测补位。

## 6. 完整分析的后续边界

先确定上述来源约定，再设计完整骨架分析：

1. 对固定源快照进行全文覆盖；长骨架可按章节切块并汇总，记录覆盖范围，不能继续仅选关键词段落。
2. 提取角色关系的变化、施压与反制、核心冲突链、代价、关键事件、可视化场景、未解问题和剧透边界。
3. 每项结论关联源章节／段落与 snapshot hash；无法确定的身份或情节不补造。
4. 若骨架引用其他设计文件，显式记录依赖与缺失，避免把链接文本误当完整设计。
5. 导演消费完整分析结果并可回查原文；图片生成器继续消费已审核的精简视觉方案。

本阶段应集中在来源解析与封面设计衔接。当前未修改正文引擎、API、数据 schema、交付包或任何小说骨架。

## 7. 代码核查入口

- `api/workshop.py:659`：Workshop 根与会话目录。
- `api/workshop.py:790`、`:899`：会话修订与版本文件写入。
- `api/workshop.py:1029`：确认稿、prepared hash、版本快照。
- `api/workshop.py:1052`：启动校验与项目副本。
- `api/workshop.py:1179`：当前骨架下载。
- `api/tenancy.py:75`、`api/services.py:191`：workspace 与项目路径。
- `core/prompt_intake.py:277`、`:325`：外部输入保存项目副本。
- `api/routes.py:1260`：封面方向创建链路。
- `core/cover_handoff.py:27`、`:141`：handoff 与 legacy 来源。
- `core/cover_design.py:154`、`:590`：证据筛选与模型输入限额。
- `core/cover_director.py:359`：导演实际请求体。

本轮仅新增审查文档和只读核查，无模型调用，无业务代码开发。
