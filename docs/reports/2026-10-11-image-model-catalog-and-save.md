# 图片模型目录与保存修复

日期：2026-10-11。范围：设置页「图像生成」的模型目录、Codex 连接保存、请求型号与实际型号展示。

## 问题与原因

用户报告拉取图片模型后仍显示 `gpt-image-2`，保存所选型号时报错。原图像输入框复用了连接的 `discovered_models`，没有独立的图片目录合同。Codex 的 `model/list` 是文本宿主模型目录；旧配置中的 `gpt-image-2` 也只是历史选择值，不能据此声称拉取到了该图片型号。

此外，精确图片型号与 Codex 内置工具的选择能力混在同一个输入框中。当前部署的工具参数没有图片 `model` 选择字段，填写 Sunburst 或 Flare 无法让 Codex 精确采用该型号，保存校验因而拒绝。

**版本语境纠正：本轮实测正式 Docker 容器内 Codex 为 0.153.0，宿主机为 0.162.1。** 先前报告引用宿主机版本描述正式运行环境不准确；排障必须以执行请求的容器版本为准。检查到的 `ImagegenArgs` 提供提示词、参考图片路径及最近图片数量，没有精确图片型号参数。此限制描述本轮实测运行环境，不推断其他版本永远不支持。

## 修复后的行为

### Codex 内置图片工具

- 设置页显示「Codex 内置图片工具（自动选择）」，不再展示可任意填写的精确型号或 Sunburst / Flare 快捷选项。
- 新的 Codex 默认配置使用内部选择值 `codex-image-auto`。它表示使用该工具，不是服务商图片模型 ID，也不是实际型号的证明。
- 已保存的 `gpt-image-2` 继续兼容。读取页面、拉取目录不会迁移或改写已有 profile；用户明确点击保存后，页面提交自动选择值。
- 生成指令要求内置工具调用一次，不再要求一个工具参数无法选择的具体型号。旧配置同样遵循这一事实边界。
- Codex 图片目录返回空列表、`source=unsupported`、`discovery_supported=false`、`exact_model_selection=false`。此查询不运行文本模型目录 RPC、不启动图片生成，不用固定型号补成假目录。

### 支持图片生成的 API 连接

- 仍支持精确配置 `gpt-image-2.5-sunburst` 与 `gpt-image-2.5-flare`；新 API 图片配置默认 Sunburst。请求按保存的完整 ID 发送，不静默替换型号。这些 ID 已与 [OpenAI 官方图片生成接口文档](https://developers.openai.com/api/reference/resources/images/methods/generate)核对；API 支持不等于 Codex CLI 具备相同选择参数。
- 裸名 `gpt-image-2.5` 继续提示选择完整 ID。保留服务商自定义型号的手动输入；目录刷新成功或失败都不覆盖尚未保存的输入。
- `codex-image-auto` 不能作为 API 连接的模型 ID 保存或发送。精确 API 支持表示参数与传输合同可用，不保证任意账户、端点或代理都提供该型号。
- API 的 `/models` 可能没有模态字段。返回项仅是该连接提供的配置候选，不代表已验证图像能力；页面说明这一限制。不能按名称猜测并屏蔽自定义别名，也不能把目录成功当作出图成功。

### 保存与测试

保存只更新配置，不触发模型或图片调用。图片测试使用已保存配置；草稿有改动时先要求保存，再启用测试。测试结果区分请求配置、服务商报告的实际型号及是否核验匹配。未返回实际型号时明确显示未知，不能宣称已核验。Codex 自动选择和历史兼容值均不伪装成实际图片型号。

## 目录与缓存合同

新增 `GET /api/studio/providers/{connection_id}/image-models`，使用 `refresh=true` 主动查询服务商。响应包含 `models`、`source`、`discovery_supported`、`exact_model_selection`、`message`、`fetched_at`。

API 查询使用 `image_base_url`，未配置时才使用该连接的 `base_url`。缓存保存为 `discovered_image_models` 与 `image_models_fetched_at`，不读取或覆盖文本目录的 `discovered_models`，也不改图片 profile。

网络请求前快照连接与凭据，请求期间不持有设置锁。写回时重新检查连接身份、端点、能力、修订时间及凭据；删除或并发修改连接时返回冲突，拒绝写入旧结果。修改端点、身份、能力或替换/清除密钥时清理图片目录缓存；仅重命名且连接身份保持时可以保留缓存。

刷新失败返回明确错误，保留原图片缓存但不把它冒充新鲜结果；不会退回文本目录。错误路径不返回密钥或远端错误正文。前端按连接隔离目录状态与型号草稿，切换 Codex 与 API 不会把自动选择值带入 API 型号。

## 独立审查结论

本轮审查未发现剩余阻塞项。重点核对了目录与能力分离、凭据和端点并发变更、缓存失效、网络期间的锁边界、旧配置兼容、自动选择值的传输边界、前端异步响应所属连接、未保存配置测试限制，以及型号来源的诚实展示。

API 未声明模态时保留候选是有意的兼容边界，不能据此声称这些型号全部可以生成图片。当前实现也没有验证 Codex 实际使用的是哪一个图片模型；该未知状态应保留到供应商提供可核验的返回值。

## 验证记录

修复前先建立了可失败的回归，覆盖图片目录独立接口、Codex 自动选择保存与客户端边界、设置页目录和输入行为。修复后的验证包括：

- 图片目录使用正确端点；文本与图片缓存互不覆盖；刷新错误、并发改连接及锁释放行为。
- Codex 不使用文本目录冒充图片目录；拉取目录不改旧配置；显式保存自动选择后可以重新读取。
- Codex 自动选择与旧值都只调用一次内置工具，提示词不声称精确型号；API 拒绝自动选择标识；未报告实际型号保持未核验。
- 设置页保存、连接切换、手动型号保留、失败提示、草稿测试限制，以及封面工作室的自动选择显示。

已完成的前端全量为 **266 项通过**，lint 和生产构建通过。独立审查另行运行后端目录/能力/客户端专项 **11 项通过**、设置页 **23 项通过**。

后端最终全量 **2,758 项通过**（185.42 秒）。其中包含实际 HTTP 保存入口：旧配置在显式保存前字节不变，PUT 自动选择返回 200，重新 GET 后配置有效，且禁止任何 CLI 和图片生成调用。此前两处测试仍匹配旧提示词原文，更新后保留「仅调用一次、失败不自行重启」断言；图片相关专项 **62 项通过**。全量 5 条警告来自现有 Python 多线程 fork 和 Google SDK 类型弃用提示。

验证使用模拟服务商、临时配置和图片 fixture，没有为本轮修复执行真实模型或图片生成。保存成功与测试通过不能作为 Sunburst / Flare 已在用户账户实际出图的证明。

## 文件与证据位置

- `core/image_capabilities.py`、`core/image_client.py`：自动选择、精确型号与实际型号边界。
- `core/provider_settings.py`：独立图片目录、缓存与并发写回校验。
- `api/models.py`、`api/routes.py`：目录响应和测试图片型号元数据。
- `web/src/routes/Settings.tsx`、`web/src/routes/CoverStudio.tsx`、`web/src/api/client.ts`：保存交互、候选目录和型号说明。
- `tests/test_provider_settings.py`、`tests/test_image_client.py`、`tests/test_codex_cli_backend.py`、前端设置与封面工作室测试：回归证据。

审计目录：`/private/tmp/novelos-image-catalog-20261011/`。最终验证日志为 `frontend-tests.log`、`frontend-lint.log`、`frontend-build.log`、`backend-tests-final.log` 与 `image-regression-final.log`；早先红灯结果保留在同目录用于追溯。

## 部署与浏览器验收

已部署到正式本机 `http://localhost:5174`。部署前两次检查 14 本作品、讨论和后台进程，无活动任务、无 Codex 子进程。前后端均 healthy，161 个运行源码/Skill 文件 SHA 与主工作区相同。

- 后端镜像：`sha256:3b3f05a698642418f756c023c060cec90dc1b5d62dae3026b749723dbc60253e`。
- 前端镜像：`sha256:1f942dbfef950ee003b6215b90710331417b0a66bdb4eb2993d4a74b8354b876`。
- 回滚标签：`novel-os-backend:pre-image-catalog-20261011`、`novel-os-frontend:pre-image-catalog-20261011`。

正式接口验证：Codex 图片目录为空且明确不支持精确选择，读取旧 profile 仍有效且候选数为 4；14 本作品和关联讨论任务查询通过。

正式构建的隔离浏览器验收通过。Codex 目录使用真实 GET，保存由浏览器拦截以保护正式配置：提交自动选择值、参数完整保留，刷新仍显示自动选择；实际服务端保存另由隔离配置的 HTTP 回归验证。API 场景使用模拟连接与目录：不混入文本缓存，目录刷新失败/成功均保留手填型号，Sunburst 和 Flare 保存时保持精确 ID，重新读取一致。未保存的设置不能触发测试图片。页面无 JS 异常，1440px 桌面和 390px 窄屏布局通过；窄屏截图等待侧栏收起动画完成后复核。

1,296 个原有封面、来源和交付文件及 Studio 配置 SHA 保持不变。没有真实模型或图片调用，未新增 API 连接或修改已有批准、预算、封面选择。浏览器隔离 context 已关闭，用户 Chrome 下载行为恢复 default。工作区保留既有改动，未提交或推送。

验收截图：[Codex 桌面](assets/2026-10-11-image-model-catalog/codex-auto-desktop.png)、[Codex 窄屏](assets/2026-10-11-image-model-catalog/codex-auto-mobile.png)、[模拟 API 连接](assets/2026-10-11-image-model-catalog/api-models-desktop.png)。运行证据见审计目录的 `runtime-verification.json`、`runtime-sources.json`、`pre-deploy-idle.json`。
