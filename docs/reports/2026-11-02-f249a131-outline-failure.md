# f249a13144d5 大纲失败：只读排查

> 后续：已实施通用修复并部署，原始排查证据保留；当前状态见[发布报告](2026-11-02-global-reference-repair-deployment.md)。

2026-11-02。用户询问新改动在哪里查看，并反馈当前创作任务失败。本轮核查运行版本、真实任务状态、保存的模型响应与恢复路径；没有部署、重启、修改真实任务或调用模型。

## 现场事实

- 5174 前后端容器 healthy；运行后端没有 `api/workshop_capacity.py`。上一轮容量与骨架修订界面仍是本地未部署代码，不能在当前页面看到。
- 头脑风暴 `b3f80487de8a43b896b0549aa2252afd` 已命名、准备并启动；会话 revision26，关联作品 `a-night-of-her-own-b3f80487`。历史“revision23/ready”不能再当成现场状态。
- 正文任务 `f249a13144d5` 于北京时间13:00:44开始，13:22:51失败于 outline / semantic_review。全书设计和3批共12章细纲均保存，正文0章。
- 最后一次模型请求已正常完成，有815字符返回，没有本次超时证据。

## 原因与证据

原骨架明确把同夜危险交班及撤回同意作为高潮：第8章交班，第9章立即承接停止信号、结束共鸣并继续救治；第11章是恢复期后的职业自主选择。保存的第9/11章细纲保持了这些事件。

全局转换结果却将 `volume_contracts[0].climax_chapter` 写成11；代码据此派生 `volume_01_climax.due_chapter=11`。语义核查明确要求两处对应第9章，并保留第11章原事件。不是要求重写故事或改12章总结构。

`volume_contract_template` 对12章的默认高潮位置恰好为11；模型转换未按原故事调整。模板造成锚定是合理推断，不能仅凭结果相同证明模型内部推理过程。离线验证把派生候选的高潮位置改为9，原结构校验可以通过，关联义务会由代码自动同步，说明不是结构范围禁止第9章。

当前 `StagedOutlinePlanner._run` 一旦遇到任一 `scope=global` 问题便停止；只给章节问题提供有次数限制的修订。进度仍提示可恢复，作品API也给出 `task_can_resume=true`，但普通恢复读取相同检查点和blocked检查，会重复停止。这是全局修订能力与恢复提示之间的缺口，不应通过删缓存、强行改passed或不断重试掩盖。

## 离线复现

命令：

```sh
venv/bin/python /private/tmp/novelos-f249a131-diagnosis-20261102/replay.py
```

使用真实保存的全局/章批/核查检查点的临时副本，原运行身份不变，模型客户端为禁止调用替身：

```text
replay 1: same saved global conflict; new model calls=0
replay 2: same saved global conflict; new model calls=0
structural check: Chapter 9 is allowed; dependent climax obligation follows automatically
template climax: 11
No original checkpoint or task state was changed.
```

原始证据位于作品 `outputs/runs/f249a13144d5/planning/checkpoints/e8cd887e356662be403ddbe60884de11129752cd4eafe350459819f9cc125dea/` 的 `global.json`、`batch-003.json`、`review-initial.json`，原骨架在 `outputs/input/prompt.md`。

## 后续处理边界

当前失败尚未修复或恢复。需要有来源/版本约束的全局定向更正：纠正候选合同中的高潮位置，代码重建关联义务，再核查修改后的完整候选；已有有效细纲应保留，不能直接伪造旧核查通过。通用实现应区分可局部更正的派生字段与需作者决定的故事冲突，并给出准确恢复入口。上线新界面本身不会消除该失败。

新容量/修订界面开发与验收见 [工程交付](2026-11-02-story-capacity-and-skeleton-review.md)；可查看 `.playwright-mcp/capacity-desktop.png` 与 `.playwright-mcp/revision-desktop.png`，它们是替身数据的工程预览，不是5174当前页面或真实任务数据。
