# 资料设计恢复修复部署验收

作者明确要求“部署”，本轮授权覆盖之前的禁止上线限制。仅切换 Novel OS 前后端；未恢复原本已停的任务、未重新执行未确认的付费请求。

## 发布结果

- 部署提交：`8e768ec330b3581017d88d183f7637ac4d14ebbc`，包含资料设计状态展示和共同请求执行/恢复修复。
- 镜像：`novel-os-backend:source-recovery-8e768ec33`、`novel-os-frontend:source-recovery-8e768ec33`，均为 healthy。接口 `/api/health` 返回 ok。
- 地址仍为 `http://localhost:5174`；数据挂载仍为 `output/origin-dev-trial-a2617c9/data`。端口、环境变量摘要、挂载路径及权限与切换前一致，模型设置文件原字节不变。
- 514 份运行源码与发布快照 SHA 一致；实际提供的前端入口及 43 个 JS/CSS 资源与镜像 SHA 一致，恢复操作文字已在部署资源中核验。
- 镜像从固定提交的独立 `git archive` 构建。切换前在无网络、无生产数据挂载的临时镜像容器中验证了超时分类、显式重执行幂等、完整回复复用和 Codex CLI 持久响应；仅使用本地替身。

## 数据与任务保护

部署前核对 20 个项目、两本资料、Workshop、封面状态和模型子进程，均没有活动工作，无需暂停正在生成的正文。所有 37,956 份非 SQLite 数据文件完整备份；SQLite 采用一致性 backup，完整性检查为 ok，16 张表的结构及全部逻辑记录均保留。切换后再次逐文件及数据库逻辑内容比对，结果完全一致。

原小说 `ac3f15f6efff` 已有 60 章完成正文及 60 章细纲，部署前就处于 `paused / ending.review`：结尾门禁要求部分人物弧光从 resolution 进入 completed。该既有错误及任务状态未因部署修改，也没有自动恢复或重新规划。本次只处理发布，不把部署当作修复该文学状态问题。

资料设计旧 run `53c567b47d3e4b4bb88e4d0179fb536d` 仍为 `pending_unknown`、`worker_active=false`、模型调用 1 次。部署后的只读 API 正确返回：

- `can_resume=false`：不能用普通继续重复进入无效查询。
- `can_retry_unknown=true`：可由作者通过“重新执行未完成步骤”授权一次新尝试。
- 原请求回执 SHA 仍为 `4c44000d97f90060a53381dd9725d7c6e0baf350ba71ee6085a29a10681aff45`。

旧分析、历史讨论、正文、大纲、检查点和请求记录都保留。本轮没有调用真实模型，没有重启其他应用或测试数据库。

## 证据与回滚

完整证据在 `output/source-request-release-20261112/`：固定 `source/` 快照、`revision`、`prepared.json`、`build.log`、`image-smoke.log`、`before/data/` 备份及文件/数据库清单、`cutover.log`、`deployed.json`。

`rollback.yaml` 记录切换前实际使用的 `source-routes-118153b50` 前后端镜像，未沿用更早的历史回滚版本。若后续需要回滚，应先安全处理活动任务，再仅切换镜像，保留当前数据；不能用本备份覆盖后续生成的新内容。此次无需回滚。开发验证见[修复报告](2026-11-12-source-request-recovery.md)。
