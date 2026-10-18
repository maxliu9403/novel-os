# DeepSeek 接入 · Settings 设计稿

静态 HTML 设计稿，用于审批 DeepSeek 文本接入与独立视觉路由的 Settings 交互。样式沿用当前 Studio 的玻璃拟态、浅蓝紫背景、SF Pro / PingFang 字体、胶囊按钮与 8–12px 圆角。

- 打开：[`index.html`](index.html)（浏览器直接打开即可，无需服务）
- 品牌资源：`assets/novel-os-logo.svg`、`assets/novel-os-mark.svg`
- 截图：`screenshots/`（Playwright，1440×1000，2× DPR；900px 窄屏为 1×）
  - `01-connections.png` 服务连接
  - `02-text-routes.png` 文本路由（默认 DeepSeek）
  - `03-images-vision.png` 图像生成 + 独立视觉审阅
  - `04-images-degraded.png` 视觉审阅未配置的降级预览
  - `05-pricing.png` DeepSeek 价格预设
  - `06-add-deepseek.png` 添加 DeepSeek 连接弹窗
  - `07-narrow-text.png` / `08-narrow-images.png` 窄屏

## 已确认的产品决策

1. **一步到位做独立 vision 路由**：`cover_director` 回归纯文本；封面语义评审、独立盲读、海报建议使用单独的「视觉审阅」连接（默认 Codex）。
2. **默认文本模型为 `deepseek-reasoner`**：其余写作角色继承默认；DeepSeek 的推理强度由模型决定，不再显示无意义的 effort 选项。
3. **不做一键「DeepSeek 写作 + Codex 图像」配置卡片**：通过连接与路由正常配置。
4. **补 DeepSeek 价格预设**：费用估算页提供两档参考价，可一键载入并继续编辑。
5. **直接切默认路由**：默认写作模型指向 DeepSeek，封面/海报的视觉审阅独立配置。

## 页面与状态

| 页面 | 说明 |
|---|---|
| 服务连接 | Codex 仍保留登录、图像生成、视觉理解；DeepSeek 只声明文本能力。能力说明解释三者的边界。 |
| 文本路由 | 默认写作模型为 DeepSeek + `deepseek-reasoner`；其余角色继承；封面指导为文本策划，视觉审阅不在本页。 |
| 图像生成 | 新增「视觉审阅」卡片（连接 + 模型 + 状态），下方保留现有封面图像模型；可预览“未配置视觉审阅”的降级状态。 |
| 费用估算 | DeepSeek 参考价预设、汇率、两档 DeepSeek 单价与 Codex 单价。 |
| 添加连接弹窗 | DeepSeek 服务商、基础地址、API 密钥；图像生成/视觉理解能力明确禁用。 |

## 交互

- 顶部标签切换四个页面。
- 「添加连接」打开 DeepSeek 配置弹窗；Esc 或点击遮罩关闭。
- 「图像生成」页的「预览降级状态」切换视觉审阅未配置时的警告与卡片降级。
- 「费用估算」页的「载入 2 个模型参考价」显示载入反馈。
- 所有保存/测试按钮只显示静态 toast，不调用真实接口。

## 实现映射（审批后执行）

- `core/provider_settings.py`：新增 `deepseek` 模板；新增 `image_input` 能力与 `vision_profiles.cover_review`；DeepSeek 模型发现 fallback。
- `core/llm_client.py`：`complete_with_images` 的视觉能力白名单，DeepSeek 明确拒绝。
- `core/studio_settings.py` / `api/routes.py`：视觉路由解析与封面评审、海报建议的降级。
- `web/src/routes/Settings.tsx`：第三个能力开关、视觉审阅卡片、DeepSeek 说明与降级预览；`web/src/api/client.ts` 类型。
- `core/usage_costs.py`：DeepSeek 参考价预设（`deepseek-chat` / `deepseek-reasoner`）。
- 测试与文档：provider settings、model router、llm client、cover/poster 降级、pricing；README / API.md / MEMORY.md。

## 待审批

- 视觉审阅卡片的交互是否符合预期（连接 + 模型 + 状态；未配置时降级为人工审核）。
- 价格参考值是否按当前官方价格页替换（稿件中为示例，实施时以官方页面为准）。
- 是否保留页面底部的「本次设计新增」说明区（正式页面不显示，仅设计稿使用）。
