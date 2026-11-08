# 设置页面与 Gemini 接入

日期：2026-11-06。分支：`codex/novel-quality-optimization`。

## 页面与路由

四个标签统一使用作品库的浅色大面板、深色圆角主按钮、紫色交互提示和系统字体。移除右侧说明预览及空白图片预览栏；真正生成的测试图在配置下方展示。保留当前标签的 URL，手机与桌面布局均可用。

文本路由首先展示默认写作模型与头脑风暴。其余放入高级任务覆盖，未配置时继承默认。角色是调用配置，不表示每章固定调用九次模型。

| 路由 | 当前调用用途 | 处理 |
| --- | --- | --- |
| default | 未单独配置任务的回退模型 | 主要设置 |
| workshop | 头脑风暴、参考阅读与骨架交付 | 主要设置 |
| architect | 全局设计转换、章节批次与规划核查 | 高级设置 |
| writer / scribe | 正文章节初稿 | 高级设置，可覆盖默认 |
| guardian / continuity_guardian | 新流程联合审稿、历史任务连续性检查 | 高级设置 |
| editor | 需要修订时的正文编辑、历史编辑流程 | 高级设置 |
| style / style_curator | 出版简介及历史任务文风处理 | 高级设置 |
| cover_director | 封面故事分析与美术方向 | 高级设置 |
| judge | 历史流程中按任务策略评估英文试读窗口；新 story-first 章节不调用 | 高级设置，说明适用范围 |

调用证据集中在 `core/model_router.py`、`core/orchestrator.py`、`core/pipeline_runner.py`、`core/chapter_review.py`、`core/publication_copy_service.py`、`core/narrative_methods/runtime.py`、`api/workshop.py` 与封面规划服务。本次未发现可安全删除的无调用角色；没有通过删除配置来破坏仍在使用的业务功能。judge 与已删除的 M3 全书阅读评议不是同一个模块。其历史策略默认是 advisory，不应误报为默认关闭；新 story-first 分支直接采用联合审稿，没有调用历史逐章方法评估。

## Gemini 接入边界

新增 Google Gemini 服务连接，使用 API Key 与 Google Gen AI SDK 的原生 Developer API。默认地址 `https://generativelanguage.googleapis.com`，支持原生协议网关；不使用 OpenAI 兼容转换。

- 原生模型列表分页读取，按账户实际权限选择，不把预设型号当作账户可用性证明。
- system instruction、用户文本与图片分别传入；文字内容不做截断或改写。
- 流式输出更新任务活动；隐藏模型思考内容，保留可观测的输出进度。
- 模型适配的推理档位，Gemini 2.5 使用 thinking budget，较新系列使用 thinking level。未知型号仅提供模型默认；服务端校验整个继承路由，防止从 Codex 继承不受支持的档位。
- Gemini 2.5 的思考预算占用输出额度，保存时检查并明确提示；不偷偷增加作者设置的输出上限。
- 支持 PNG/JPEG/WebP 图片理解，供封面与参考图分析使用。本次没有 Gemini 图片生成，也没有 Vertex AI/ADC 登录接入。
- 只有完整 STOP 才接受输出；空内容、截断、拦截和中断均不作为完成结果。模型请求不自动失败重试。
- SDK 原生长输出续传保留同一原始输入。续传属于同一次生成，累计用量不重复计入重试费用；仍受输出上限和任务原有时限约束。
- API Key 继续使用现有私密设置存储；API 错误仅报告状态与排查方向，不持久化可能含提示词或密钥的服务商原始错误。
- 费用页支持 Gemini 的可配置模型单价与人民币汇率，没有虚构默认价格或把估算称作实际扣费。

实现主入口 `core/gemini_provider.py`；现有 LLMClient、连接管理、视觉路由复用此入口，无第二套路由或任务系统。

官方依据：[Google Gen AI Python SDK](https://googleapis.github.io/python-genai/)、[模型列表协议](https://ai.google.dev/api/models)、[思考配置](https://ai.google.dev/gemini-api/docs/generate-content/thinking?hl=en)、[官方推理预算映射](https://ai.google.dev/gemini-api/docs/openai?linkId=13271420)、[Gemini 4 Argon 长输出续传说明](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-4-argon)。具体型号仍以所配置端点和账户支持为准，企业端点文档不等于 Developer API 账户已有该型号权限。

## 验收

- 最终以实际生产镜像为基础、仅添加测试工具的断网容器完成后端 218 项回归，包括原生 Gemini、进程取消、路由、用量和故事优先完整写作／导出。此前 202 项集合缺少 `ps` 的 3 项进程测试已在最终集合补齐并通过。
- 前端 Settings/Pricing 36 项通过，生产构建、改动文件 ESLint、Python 语法及 diff 检查通过。
- 原生 SDK + HTTP mock 验证真实序列化路径、system prompt、图片、模型分页、思考预算、取消、截断与空结果拒绝、429 无自动重试和续传累计用量。
- 四个标签在 1440px、390px 完成浏览器检查，无 JS 错误或横向溢出。截图位于 `docs/reports/evidence/2026-11-06-settings/`，使用隔离模拟配置，未更改真实服务连接。
- 没有发送真实 Gemini 请求、使用真实 Key 或启动付费出图。账户连通性及实际小说质量需要配置后验证，工程测试不能替代文学验收。

## 环境发布

已发布到 `http://localhost:5174/settings`，包括此前本地完成的 story-first 工作流。发布前两次检查正文、头脑风暴、参考阅读、封面任务及模型 CLI 子进程，没有活动任务。旧后端／前端镜像保留为 `novel-os-backend:before-settings-20261106`、`novel-os-frontend:before-settings-20261106`。

运行容器六个关键源码 SHA 与已验收宿主一致，google-genai 实际版本 2.34.0，后端和前端健康检查通过，原有九个模型路由保持 configured。实际 5174 四个标签无 JS 错误／告警，文本主区域仅两项。没有修改原有连接、模型档位或小说内容。

现场发现旧入口 HTML 缓存使浏览器仍显示旧设置；已为 Nginx index.html 添加 `Cache-Control: no-cache, must-revalidate`，实际响应已验证。已经缓存旧版的浏览器首次仍可能需要强制刷新，后续部署入口会重新校验。

最终日志 `/private/tmp/novelos-settings-production-regression.log`、`/private/tmp/novelos-settings-webtests.log`、`/private/tmp/novelos-settings-deployment.log`；实际页面截图 `docs/reports/evidence/2026-11-06-settings/live-text.png`。
