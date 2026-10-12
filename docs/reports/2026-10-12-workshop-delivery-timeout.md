# 头脑风暴完整输入与超时链路核对

2026-10-12，用户要求只读排查，不重启服务、不干预正在运行的任务。会话 `4cc8bbb89ae74a509b9d4341f50df7a9`、revision 10、job `45a255c7d54c4242a998f5224ad0c752`。未重启、暂停、重试、改配置或调用模型。

## 实测链路

- 14:10:22（上海）本轮开始，900秒为全轮共同预算。
- 第一次调用32秒左右返回422字符内部交接，最终账本输入32,043、输出622、共32,665 Token。模型申请从 exploration 转 delivery，不是格式失败重试。
- 14:10:59第二次调用开始，剩余863.222秒。system 126,400字节，user 13,640字节，模型gpt-6-astra、reasoning xhigh。
- **14:24:44实测运行进程3477的 `/proc/3477/fd/0`**：匿名输入文件140,357字节（包含317字节调用包装），通过独立文件描述符 `pread` 读取，不改变进程读取位置。UTF-8与JSON完整，system/user长度逐项匹配请求日志；包含初始设定、3条当前对话、待处理问题、当前骨架字段、delivery_instruction和short_novel模式，最后用户消息为“同意推荐，请按照推荐继续推理”。原进程fdinfo位置140,357，已到EOF。
- 输入SHA-256：`8b3c6b88a70c72d23d36537e365c2652ad2bfff5863d20ebb2185aa46bc3ed09`。没有将完整提示另存或输出到诊断报告。
- 第二次调用先出现输入11,709、输出/推理0的上游completed，再在14:11:06.969出现WebSocket发送成功。Codex 0.153源码存在generate=false预热机制，序列与之吻合；本次遥测未保存generate字段，预热归因是推断。
- 此后只有后台心跳和models端点200查询，没有正文、最终结果文件或第二次CLI turn.completed。存活进程、HTTPS连接和模型列表查询不能证明正在推理。
- **14:25:22.736自动转error**。第二次调用863.291秒，stdout 101字节（启动事件）、stderr 0、model_output_chars 0、final_output不存在、usage为空。没有自动启动第三次调用。

## 能证明和不能证明的边界

已排除框架未拼齐提示或stdin只交付一部分：真实输入完整，且CLI读至EOF。WebSocket成功日志记录的是发送操作成功，不是服务端收到完整提示或开始推理的确认。因此故障范围缩小到CLI读取输入之后的请求/响应链路，仍无法凭现有记录区分远端排队、计算、响应链路停滞或CLI内部状态。

Token统计不是逐token实时流：`core/llm_diagnostics.py`只汇总CLI `turn.completed.usage`；`core/usage_tracking.py`在请求退出时持久化最终值。OTel计数缺少稳定上游response ID，不能贸然叠加进账本。第二次usage为空意味着未知，不能写成零消耗；第一次已有明确用量。

进度投影未保存profile.switch/prompt_profile，也丢弃endpoint_category，导致界面看不到“正在完整骨架输出”，并将模型列表查询笼统显示成codex.api_request。后续可改进可观测性，但本轮未修改业务代码或部署。没有原始上游请求体/服务端确认，不应声称已核实LLM收到全部字节或持续推理。

原始证据：`docker-data/workshop_v2/4cc8bbb89ae74a509b9d4341f50df7a9/diagnostics/10-brainstorm.jsonl`及对应progress、session。预热/发送语义参考此前保存的 `/private/tmp/novel-codex153-source/client.rs` 与 `responses_websocket.rs`。

## 协议复核与隔离实验

同日续查使用运行镜像内的官方 Codex CLI 0.153.0，在 `docker run --network none` 的一次性容器中对接 loopback WebSocket mock。未挂载业务目录/真实凭证，未改当前服务。synthetic prompt 为140,357字节，含中文和首尾哨兵；不是原小说提示词。采用相同模型名与xhigh档位，但隔离容器没有真实模型元数据，因此这只验证本地序列化/传输和等待机制，不模拟远端接纳或推理。

- 收到两次 `response.create`：首个 `generate=false/input=[]` 预热，第二个省略generate并引用前一个response ID。
- 正式请求中synthetic prompt与stdin逐字节一致，SHA-256均为 `d60485425494147105b88c89520b8245c0a5fb2d530d45a8a4a57e4cd5d5a506`。正确模拟的最终响应可被CLI读取、写出last-message并产生turn.completed。
- 将此临时provider的idle上限缩为2秒：正式响应静默约2.32秒触发idle timeout；仅Ping约2.23秒仍触发；每0.5秒发无正文metadata则继续等待到本地7秒截止，无最终结果。前两例随后降级HTTP，但mock仅支持WebSocket，因而降级按预期失败；不应把该失败当作业务服务问题。
- 7项自动断言通过。脚本与无敏感内容报告：`/private/tmp/novelos-workshop-protocol-20261012/probe.py`、`results.json`。

这复现了一条“已发请求、连接有消息，但CLI没有正文/最终结果且持续等待”的可能路径。它不能反推原任务一定收到此类metadata，也不能确定其处于服务端排队还是计算。

官方非交互文档明确支持 `codex exec -` 从stdin接收提示词；WebSocket文档明确允许generate=false预热与previous_response_id续接。本项目让官方CLI构建协议，不是手工伪造Chat Completions报文。

严格边界：隔离捕获确认CLI真实帧包含stream字段；官方0.153源码client.rs:1023设为true，DTO直接拷贝并序列化，因此值true由源码佐证（结果文件只保存字段存在性）。公开WebSocket guide说transport字段stream/background不使用，但未说明额外字段必定遭拒。官方实现还有相应保留字段单测，因此不能把它归为Novel OS构造错误，也不能称已逐字段认证符合公开API合同。当前生产认证为ChatGPT，走内置Codex端点而非项目的openai_compatible HTTP入口；未捕获原始线上正式请求或服务端接纳回执。

官方参考：[非交互调用](https://learn.chatgpt.com/docs/non-interactive-mode)、[WebSocket模式](https://developers.openai.com/api/docs/guides/websocket-mode)。

## 确认的遥测盲区

官方Codex0.153的 `record_websocket_event` 仅写metrics，不发log event。Novel OS `core/codex_telemetry.py` 当前只配置OTel日志exporter。因此普通WebSocket接收帧即使真实发生，也不会进入现有日志；完成事件由另一函数单独记录。前文“此后只有心跳和models查询”仅指现有可见日志，不代表线上没有任何中间帧。

源码的300秒默认idle限制是相邻上层帧之间的超时；普通Text可重置，Ping/Pong在下层消耗，不能重置。结合隔离实验，不能把900秒无正文直接解释为模型持续推理，也不能据日志缺失直接认定请求未发。

后续可观测性应分开记录：stdin完整交付、正式请求发送（排除预热）、服务端接纳、正文/推理摘要进度和终态；models列表请求不得算作生成活跃。此轮未修改业务逻辑、生产遥测配置或启动额外模型调用。

## Cockpit Tools 统计口径核对

用户明确查看Cockpit Tools账号管理工具，不是OpenAI API Usage。本机安装1.3.63，数据目录 `~/.antigravity_cockpit`。只读内存比较确认：容器与宿主Codex账号一致，唯一匹配Cockpit受管账户，且就是当前选中账户。报告不保存邮箱、账号ID或凭证。不能以“看错账号”解释用户观察。

本机存在三个不同口径：

1. 账号配额：账号卡片从 `wham/usage` 读取used_percent整数并计算剩余百分比，配置为每10分钟刷新，不是单请求实时Token流。
2. 本机会话用量：`codex_session_usage.sqlite` 含session_log_sync/file_path/line_offset及输入输出Token事件表。本机库最后修改在9月3日，无本次第二轮thread或docker路径记录。Novel OS使用 `--ephemeral`，Compose只挂载Codex auth/config，不共享宿主sessions目录；这类临时容器调用无法由宿主rollout扫描统计。
3. 本地网关用量：本机网关enabled=false，其stats文件最后修改9月26日；它没有承接当前直连ChatGPT Codex的请求。

用户尚未指明Cockpit内具体面板，因此按这三种口径分别解释，不能擅自认定所看的是哪一项。账号相同不代表所有统计入口都能实时观察此请求。“900秒内数值不变”不足以证明未发出，也不能作为模型持续推理的证据。

对应Cockpit 1.3.63一手源码：[扫描本机会话](https://github.com/jlcodes99/cockpit-tools/blob/v1.3.63/src-tauri/src/modules/codex_session_usage.rs#L1)、[三种统计的界面说明](https://github.com/jlcodes99/cockpit-tools/blob/v1.3.63/src/components/codex/CodexSessionUsagePanel.tsx#L533)、[配额百分比](https://github.com/jlcodes99/cockpit-tools/blob/v1.3.63/src-tauri/src/modules/codex_quota.rs#L175)。Cockpit加密详情未解密；受管账户匹配基于唯一邮箱相等性，而非解密后的账号ID字段逐项比较。

## 续查：原生exec的输出过滤与可用进展证据

用户进一步明确保留原生Codex，优先查900秒是否真的在工作。继续只读检查运行容器：logs_2.sqlite（mode=ro，读取WAL）logs总0行，state_5.sqlite线程总0行，sessions/archived_sessions不存在，原临时目录已清理。官方0.153 exec入口传入log_db=None，默认stderr只保留error。因此不存在可恢复本轮历史帧的原生日志；不能为了回答确定性而推断未记录的事件。

新隔离测试使用同镜像官方CLI，对loopback模拟上游按时间发送公开摘要与正文增量，同时收集原生logs/metrics：

| 相对秒 | 模拟事件或观测 |
| --- | --- |
| 0.652 | 模拟上游发送summary delta |
| 1.155 | 模拟上游发送正文delta |
| 1.283 | 原生metrics导出已收到两类delta各1次 |
| 5.162 | 模拟上游发送item完成 |
| 5.170 | exec --json首次发布完整reasoning和agent_message |

CLI退出0、最终文本正确、无认证头/数据挂载/真实模型调用。测试采用隔离fallback metadata，不验证实际模型性能。源码与模拟均确认：exec内部使用InProcessAppServerClient，但JSONL processor丢弃实时delta，仅在项目完成时发布完整消息/摘要。

此测试能严格证明“stdout没有内容不等于CLI未收到模型进展”，也证明**保留原生exec、补metrics即可观察上游内容活动**。不能据此认定原请求确实收到delta，旧调用没采集的信息无法补回。普通WebSocket事件在logs中仍不可见；另有codex.turn_ttft日志可辅助，但不能当成正文首字时间。

数据：`/private/tmp/novelos-workshop-protocol-20261012/delta_probe.py`与`delta_results.json`。metrics为异步批量、缺response ID；temporality=1表示增量计数，接收端不得二次差分或用采集时刻冒充帧发生时刻，预热/正式回执不能仅凭一批聚合数武断绑定。

本轮仅诊断与方案：[原生Codex长任务设计](../design/2026-10-12-native-codex-long-running-workshop.md)。补遥测→粗阶段成果持久化→需要真实逐段正文时采用同原生CLI的app-server接口。后者改善事件获取，并不自动修复远端停滞，也不保证恢复已终止计算。
