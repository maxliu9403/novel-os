# 封面工作室、作品费用与图片型号

日期：2026-10-11。主工作区：`/Users/max/OpenSource/Novel-OS`。

## 产品决定

- 用户要求所有小说共用的设计与计费框架。本次不重新分析故事、不生成新图、不改变已有封面的批准、选择或图片额度。
- 新批次仍默认 4 张；《He Chose His Hidden Family…》已有 3 张保持不变。
- 费用按可配置的模型单价和汇率估算，以人民币展示；不是 Codex 订阅的实际扣款。
- API 图像配置支持 `gpt-image-2.5-sunburst` 和 `gpt-image-2.5-flare`，新建 API 配置默认 Sunburst。已有配置不静默升级。

## 界面与流程

使用 `frontend-design` Skill，将封面页整理为浅色出版工作室：纸张层次、衬线标题、克制的蓝紫色、紧凑的四阶段进度和完整比例的候选画廊。已批准方向折叠展示，待批准方向保留操作入口；原文、来源身份和故事细节可展开检查。布局适配窄屏，保留键盘焦点与减少动态效果偏好。

“骨架全局整合”读取的是选定作品的完整骨架，默认 `outputs/input/prompt.md`，不是另行扫描整本正文。分段提取、全局整合、逐段核查、全局因果核查保持不变；来源相同可复用已完成分析，视觉改版不削弱来源覆盖或质量门。

费用账本进入作品详情、封面工作室与头脑风暴讨论；“设置 → 费用估算”管理单价与换算值。金额分头脑风暴、正文创作、封面设计和总计。缓存与推理 Token 是输入/输出的子项，不再次相加。

## 用量与估算合同

- 新用量由供应商实际响应或 Codex CLI 事件记录；归属具体作品或讨论，按调用尝试保存到 `outputs/usage/`。不保存提示词、正文、图片或密钥。
- 分阶段记录涵盖讨论与书名、正文流程、封面分析、方向、出图及审核。线程继承归属；失败、重试、取消后的可观测用量仍保留。
- 讨论启动成作品后，通过项目关联合并讨论账本，避免复制账本造成重复统计。
- 历史只回收当前工作区已有诊断和可验证的调用记录。没有保留的 Token 不从字数估计，不扫描无关个人会话。不完整账本展示“已计费用 · 估算小计”或“待核算”。
- 单价为空代表未配置，0 代表明确免费。已知部分费用保留为小计；未知部分不按零计。单价和汇率修改后重新估算已记录用量，原始计数不改变。
- 初始美元换算值 7.0 是可编辑参考值，界面明确说明并非实时汇率。初始 `gpt-5.6-sol` 文本参考价为输入 $4、缓存输入 $0.4、输出 $20 / 百万 Token；标准短上下文，其他服务档位与供应商定价应自行配置。
- 图像费用使用显式配置的每张价格；未混用文本与图像 Token 单价。Codex 出图的宿主文本用量与图像记录分开，工具没有返回的图像 Token 保留未知。

接口：`GET /api/projects/{id}/costs`、`GET /api/workshop/sessions/{id}/costs`、`GET/PUT /api/studio/pricing`。费用设置保存在 Studio 设置旁的 `pricing.json`，也可用 `NOVEL_OS_PRICING_PATH` 指定。

## 图片型号修复与边界

原客户端、配置校验及返回结果校验把 Codex 通道硬编码为仅接受 `gpt-image-2`。本次把 API 的精确型号支持与 Codex 工具能力分开验证：

- API 原样发送选择的 Sunburst / Flare 完整 ID；裸名 `gpt-image-2.5` 给出可操作的明确提示。
- 当前本机 Codex CLI 0.162.1 的图片工具参数没有独立型号选择字段，`exec --model` 指的是宿主文本模型。因此 Codex 旧通道保留，并提示使用 API 连接精确选择 2.5；不会通过修改提示词冒充已切换型号。
- 请求型号与供应商返回型号分别保存。供应商报告不匹配型号时拒绝接纳；没有回报实际型号则 `model_verified=false`。官方已知 snapshot 与对应别名可匹配。
- 旧配置缺省值在显示、执行和迁移后统一保持 `gpt-image-2`。新建 API profile 才默认 Sunburst。正式运行中的图片 profile 本次保持原值。

## 验证记录

独立审查已发现并纳入修复：旧配置显示/执行型号分裂、缺缓存报价时漏算已知输入小计、历史目录中间软链跨作品读取、CLI 直接入口导入失败、多 turn 用量部分字段处理，以及 SDK 重试/取消后的统计边界。

最终验收已完成，部署于 `http://localhost:5174`：

- 后端全量 **2,731 passed**（176.47 秒）；前端全量 **248 passed**。末轮画廊顺序、审核标记和窄屏设置调整分别重跑对应专项，均通过。最终 TypeScript 构建、ESLint、`git diff --check` 通过。
- 首轮全量的 4 个失败已处理：取消测试的旧替身缺 `model`，改为真实构造器配合假 transport，原取消/无重试断言保留；Skill 测试兼容排版短横线，并确认四图默认和旧批准保留。之后后端全量通过。
- 真实本机 **14 本作品**的费用接口均返回正确归属与三阶段统计，阶段和模型聚合的 Token/调用数量与总数一致；讨论关联没有重复累计。
- Secret Daughter 的历史账本回收 **520,263 Token**（451,820 输入、68,443 输出，缓存输入为其中 121,600）。可定价部分按初始参考价/汇率为 **¥15.881869**；这只是部分费用，不是整本小说成本。233 条可识别调用记录中 224 条没有完整 Token，另有未配置价格型号。界面按“估算小计 / 用量不完整”展示，旧正文阶段显示“尚无记录”。
- 1512×982 桌面和 390×844 窄屏实际浏览器验收通过；画廊主图 `object-fit: contain`，三张原图均为 1024×1536。A/B 标为待人工审核，C 标为需修复。候选画廊位于费用账本前，长说明限制展示行数，完整设计仍可查阅。最终窄屏无横向溢出，四个设置标签完整可见。
- 浏览器验收没有触发生成、测试模型、重评或配置保存。关闭独立验收 context，并恢复用户 Chrome 默认 context 的 `Browser.setDownloadBehavior(default)`。
- 对 **1,219 个**既有封面、交付与来源文件逐项核对，SHA 全部不变；原 Studio 配置 SHA 也不变，既有 3 图、批准、选中和交付包保留。没有新真实模型调用。
- 容器内 **160 个**源码/Skill 文件 SHA 与主工作区一致，前后端均 healthy。未提交或推送这个原本就包含大量改动的工作区。

运行镜像：后端 `sha256:2b164272a6065c5860f96f623a3a56bb05c44358c85f45756ae6bd563ba74e4d`；前端 `sha256:8384acaf1035ecaa506e923876b82640a5b090ca6ccf14c71fa28583c6ce278c`。回滚标签分别为 `novel-os-backend:pre-studio-20261011` 与 `novel-os-frontend:pre-studio-20261011`；需要回滚时先确认没有活跃任务，再以这两个已保留镜像恢复容器，数据卷不回滚。

日志及机器可读证据位于 `/private/tmp/novelos-studio-cost-20261011/`，包括 `backend-tests-final.log`、`frontend-tests.log`、`runtime-verification.json` 和 `runtime-sources.json`。Python 多线程 fork 与 google.genai 的 5 条弃用警告未导致测试失败。

验收截图：[候选画廊](assets/2026-10-11-studio/studio-gallery-desktop.png)、[作品费用](assets/2026-10-11-studio/costs-dashboard.png)、[窄屏费用设置](assets/2026-10-11-studio/pricing-mobile.png)。

## 官方参考

- [Image generation guide](https://developers.openai.com/api/docs/guides/tools-image-generation)
- [Images generate API 与完整模型 ID](https://developers.openai.com/api/reference/resources/images/methods/generate)
- [OpenAI 模型价格](https://developers.openai.com/api/docs/pricing)

参考日期为 2026-10-11。价格与服务能力会变化，界面的单价是用户可维护的估算配置。
