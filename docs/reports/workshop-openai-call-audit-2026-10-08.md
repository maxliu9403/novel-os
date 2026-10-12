# OpenAI 官方文档与头脑风暴调用链核对

日期：2026-10-08。对象：讨论 d7c6589ce293 第 8 次提交的 900 秒无正文超时。

## 实际调用路径

本次配置 provider=codex，Novel OS 运行 Codex CLI 0.153.0；不是本项目的
OpenAI SDK Chat Completions 分支。CLI 使用 Chatgpt 认证和挂载的 Codex 配置。
模型为 gpt-6-astra，推理档位 ultra。每轮使用 --ephemeral，并重发完整资料和对话；
最终从 --output-last-message 文件取得回复。

官方说明 exec --json 返回 thread/turn/item 等事件流，不能把这个事件流等同于
Responses API 的逐文本增量。当前包装提示还明确要求只输出最终 JSON，不输出进度说明。
参见 [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)。

## 参数检查

### ultra 是当前 CLI 的合法档位

实际模型目录 gpt-6-astra 支持 low/medium/high/xhigh/max/ultra。
公共 API 文档的 reasoning.effort 枚举与 Codex 用户档位不是同一层接口，不能因
公共 API 枚举不含 ultra 就把当前 CLI 参数判错。官方说明 Ultra 包含最大推理和
适合时的代理委派；当前目录不足以证明本轮真的委派了代理。
参见 [Codex 模型与推理档位](https://learn.chatgpt.com/docs/models)。

### max_tokens=8192 没有约束本次 CLI

core/llm_client.py 的 Codex 分支只显式传递 model 与 model_reasoning_effort，
没有传递配置中的 max_tokens。诊断里的 8192 只是配置值，不是已生效的生成预算。

官方 Responses API 的 max_output_tokens 同时包括推理和可见输出；预算过小可能
在没有正文时就以 incomplete 结束。因此不能机械地把 8192 当作“正文可输出 8192”，
也不能未经能力验证给 CLI 塞入一个同名 API 参数。
参见 [推理模型与 token 预算](https://developers.openai.com/api/docs/guides/reasoning)。

### 900 秒来自本地进程截止

当前 helper 在墙钟期限到达时终止整个进程组。日志中的 cli.timeout 是 Novel OS
自己的判断，不是已经收到 OpenAI 的 HTTP 408/504 或模型失败响应。

官方 Responses 后台模式支持保存 response ID、查询 queued/in_progress/终态，
并可结合流式游标恢复。但这些能力不能直接当作现有 Codex exec 已经提供；接入
HTTP API 与保持 Codex 登录方式需要分别设计。
参见 [后台执行](https://developers.openai.com/api/docs/guides/background) 和
[Responses 流式事件](https://developers.openai.com/api/reference/resources/responses/streaming-events)。

## 官方遥测的隔离验证

使用官方 OTel 导出方式，收集器只绑定容器回环地址。禁用用户提示词日志，并对收集
字段二次白名单过滤，只保留事件名、状态、耗时、档位和 token 计数。
未保存提示词、模型正文、认证头、邮箱、账号 ID、原始错误或私有推理。
没有修改原讨论、全局配置或服务端代码。
参见 [Codex 可观测性配置](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry)。

短请求保持同一模型与 ultra，要求只返回 OK，17.74 秒完成 CLI 调用
（探针含准备及关闭收集器共 18.61 秒）。这再次证明当前调用链能够正常完成简单任务。

完整重放捕获应用为原版本 8 组装的原始输入：system 137,370 字节、user 19,622 字节，
以 SHA-256 核对对照测试输入一致；未裁剪对话或改变创作合同。
ultra 重放在隔离的 180 秒上限内未完成，主动终止，不等同于又等待了 900 秒。
high 单变量对照也在 180 秒内未完成，实际遥测确认 effort=high。两次输入 SHA-256
一致；两者都未完成，不能据此认定 ultra 是唯一原因，也不能证明高低档位性能相同。
high 探针中保留了 endpoint 分类，观测到的 HTTP 200 属于 models 请求，确实不是
小说生成接口的成功状态。

最后的 full_ack 对照保留完整系统规则与此前对话，只替换最新用户消息为连接诊断，
要求返回极短的合法 JSON。CLI 在 18.99 秒完成，探针含准备和关闭收集器共 20.68 秒；
模型返回有效 JSON，input_tokens=53,007、output_tokens=23，没有生成骨架。
回复字段并未逐字返回要求的 OK（12 个字符），因此这只是完整上下文可送达、可返回
可解析短回复的证据，不是严格指令遵循测试通过，也不是实际小说任务成功。

| 隔离测试 | 推理档位 | CLI 结果 | 含探针开销的耗时 |
| --- | --- | --- | ---: |
| 极短连接确认 | ultra | 返回 OK | 18.61 秒 |
| 原始完整小说请求 | ultra | 达到 180 秒测试截止，无回复 | 183.18 秒 |
| 同一完整小说请求 | high | 达到 180 秒测试截止，无回复 | 181.52 秒 |
| 完整规则、历史，仅本轮改为简短确认 | high | 返回有效 JSON | 20.68 秒 |

这些结果支持继续检查实际推演任务的工作量和生成等待，而非将大上下文本身判为无法
传输。各次请求不同时发生，仍不能排除上游波动；无遥测事件的时间段不能确认为正常思考。
脱敏的原始计数与事件证据见
[调用审计探针数据](../diagnostics/2026-10-08-workshop-openai-call-audit.json)。

观测中先出现一条 output_token_count=0 的 response.completed，再出现下一次
WebSocket 请求。它与初始化/预热行为相符，不能视为小说回复已经成功，也不能仅凭
其字段确认确切内部用途。WebSocket request success 不等于模型已经开始推理；
generic HTTP 200 也可能属于初始化请求，不能冒充生成请求已获准。

OTel 按批次导出，observed_seconds 是收集时间。只有实际保留的 source_timestamp
才是源事件时间。CLI 不一定为所有网络帧发出日志，进程被终止时还可能丢失缓冲事件；
没有 delta 日志不能证明远端没有计算或传输。

## 当前结论与修复边界

### 提示词工作量审查

`api/workshop.py:130` 的 design 资料包每轮注入 13 个文档，其中完整 prompt 合同约
30.8 KB、结构模式规范约 12.9 KB、骨架模板约 12.6 KB、启动命令说明约 7 KB；
`:170` 起另附四种结构模式的完整序列化示例。当前并未按讨论阶段和已选模式裁剪。
Web 的启动命令实际由服务端构建，相关 CLI 操作说明也仍进入模型输入。

`core/llm_client.py:563` 起的包装禁止中途进度说明；`api/workshop.py:285` 要求单个
完整 JSON，`:344` 等待调用返回才解析。它们造成确定的可见性限制。
但 `api/workshop.py:268` 明确允许探索时 prompt_md=null，Skill 同样允许继续讨论；
没有证据支持“每轮强制重写完整骨架”或“存在必然死锁”这样的结论。

可以将探索与交付资料按阶段加载、只携带已选结构，并通过同一故事请求做单变量对照。
这是基于证据的优化方向，尚未实施，也尚未证明它能解决原始 900 秒超时。

### 诊断结论

- 等待位置在模型调用阶段，已越过输入传递；不是前端未渲染，也未进入骨架校验。
- 没有证据表明此次是非法模型名、非法 CLI effort、认证失败或 API 字段拒绝。
- 已确认调用层缺口：输出预算没有生效、长任务只有本地硬截止、缺少可恢复的远端任务状态。
- 高档位、重复大上下文与长输出是需要测量的因素，不能单独宣布为原始 900 秒超时根因。
  官方也区分输入处理与生成耗时，不能仅凭 137 KB 输入认定网络或计算堵塞。
  参见 [延迟优化](https://developers.openai.com/api/docs/guides/latency-optimization)。

独立的 HTTP SDK 分支仍使用 Chat Completions、max_tokens，且没有转发 reasoning_effort。
这些是另一路径的适配缺口，不能拿来解释此次 Codex 请求。官方推荐的新接口及参数见
[Chat Completions 参数](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。

优先修正实际路径的有效参数报告与调用级诊断，再基于同输入实验选择交互式推理档位和
讨论/交付工作量。若迁移直连 API，单独实现 Responses、推理与正文合计预算、完整终态
处理及可恢复任务；不把只加前端流式展示当作模型调用超时的修复。

完成诊断后核对：原讨论仍为 revision=8、status=error、8 条消息、骨架为空，更新时间
仍为 2026-10-08T07:00:43.332816+00:00。未重试、暂停或覆盖原讨论；隔离 CLI 和收集器
已退出。此次仅保存审计文档和脱敏元数据，没有修改生产代码、模型配置或重启服务。
