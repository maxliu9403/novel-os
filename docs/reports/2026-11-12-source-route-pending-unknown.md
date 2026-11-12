# 整书设计停于“请求结果尚未确认”的只读排查

范围：仅排查。未改业务代码、生产数据或模型设置，未恢复/重发请求，未部署或重启。仅保存本报告和项目记忆。

## 现场证据

- 作品：A Contract with the Billionaire，6 章已提供正文；来源 `source-99b028ab711ab2e390414f888cc05fbd9ed6d7be9bdc0a6906c5ba76c93564ab`。
- 实际前后端镜像仍为 `source-routes-118153b50`；本地 `30c7b2e` 设计状态展示尚未部署。该未部署变更不能修复本次恢复链路。
- 运行 `53c567b47d3e4b4bb88e4d0179fb536d`，改编重写、首次三条路线。上海时间 2026-11-12 00:10:49 发起，00:25:49 停止；配置为 `codex:gpt-5.6-sol`、900 秒。
- 请求仅一次，状态 `pending_unknown`，已保存步骤 `0/3`，无完整 `raw`、无路线产物。输入回执包含全部六章和简介，共七个完整来源片段；system 13,636 字节、payload 79,315 字节。旧分析存在。
- 对应使用量请求 `977f07e171c84dba9a181febdd4aaff5` 记录为本地 `failed`，起止相差约 900.09 秒，usage 为空；不代表远端没有执行或没有计费。
- Job `3c51142811c44dd9bd9186ef4517d9cf` 为 `done`、error 为 null；设计 API 明确 `pending_unknown`、worker_active=false。任务已经停止，并非仍在后台分析。
- 对应容器日志没有保存该次原始异常；容器 `/root/.codex/logs_2.sqlite` 的 logs 表为零行，临时 `novel-os-codex-*` 目录为零个。不能以没有记录反推没有发出请求。

## 已确认的系统缺口

1. **具体原因被压成未知。** `core/source_request_recovery.py` 对明确 HTTP/未连接以外的异常只保留外层类型及 `no_verified_terminal_response`；`source_story_design.py` 再用通用文案覆盖。现有代码完全可以区分“本地等待超时”和“远端完成状态未知”，但没有共同保存这两个维度。
2. **该入口漏接已有诊断。** `api/source_routes.py` 构造 `LLMClient` 时没有传 `diagnostic_callback`。request.start/timeout/completed、CLI 活动、首个输出及线程身份未持久保存；usage 作用域不会自动补足这些诊断。旧阅读入口和 Workshop 已有各自诊断接线。
3. **工作线程结束被显示为 done。** `SourceStoryDesign._run` 捕获业务异常、写状态后正常返回；JobRunner 据此标记 done，且结果映射只保留来源/运行身份。工作线程退出和设计成功缺少明确的统一表达。
4. **当前继续按钮没有可用恢复能力。** `_request` 对未知结果要求 `client.recover_response` 返回原响应；生产 `LLMClient` 没有该方法。继续只会重读同一回执并再次停止，换模型也不能绕过该回执。界面没有依据真实恢复能力调整操作。
5. **CLI 产物缺少持久接回边界。** Codex 使用 `--ephemeral` 和临时 `last-message.txt`；期限到达会停止本地进程组，退出后清理目录。现有业务记录不足以判断截止前是否曾有部分输出或最终文件。

## 验证与结论边界

读取真实状态并要求 ready/存在路线的断言稳定失败：`pending_unknown, saved=0/3, route_set=None`。用真实回执只读执行恢复分支，确认没有 recover_response，立即返回 SourceRouteUnknownError，回执 SHA 不变；没有初始化模型连接或写入文件。

通过无网络、无子进程的内存替身，让实际 Codex 适配器收到 `TimeoutExpired(900)`，得到明确的 `LLMError: Codex CLI timed out after 900 seconds`，保留 TimeoutExpired cause；实际分类器随后输出与现场相同的泛化 unknown。该实验验证超时信息会丢失，不证明现场远端故障的具体成因。

现场耗时精确吻合本地超时设置，强烈支持本地等待到期；缺少原始诊断，仍不能判定上游为何没有正常结束。没有上下文超限、JSON 解析失败或文学质量核查阻断的证据。已确认这次尚未进入结果解析/质量核查阶段。

## 后续修复方向（尚未实施）

统一持久请求生命周期：保存脱敏的本地失败类型、超时范围、活动与回复进度；将本地终止与远端结果状态分开；完整回复/部分输出和可查询身份先落盘再退出；恢复入口按实际能力提供查询、接回或清楚标识的重新执行路径。调度状态、领域状态和界面操作应遵循同一结果合同，旧回执保留原证据，不能虚构缺失诊断。仅增加超时、上线状态徽标或把未知强行改成失败都不足以完成修复。
