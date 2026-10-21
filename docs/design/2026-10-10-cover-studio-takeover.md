# 【优化小说封面设计逻辑】接管与交付记录

后续工作室视觉、费用账本和 GPT Image 2.5 型号支持见 [2026-10-11 追加交付报告](../reports/2026-10-11-studio-costs-and-image-models.md)。下述三图交付记录仍按原批准和历史时间点理解。

日期：2026-10-10 至 2026-10-11，Asia/Shanghai。  
状态：通用框架已同步并部署；三张候选完成本地排版与真实审核，A/B 无硬阻塞，C 因门框拉伸伪影阻塞。本轮未选中新封面，旧交付保留。

## 1. 接管范围与决策依据

本轮将以下两个历史会话的后续工作统一执行：

| 历史会话 | 会话 ID | 本轮承接内容 |
| --- | --- | --- |
| 查询小说创作任务 | `01a10c13-9ed0-71c1-ae48-ee4925fbc5f1` | 封面约束全链路修复、深度 review、剩余验证、同步与交付 |
| 优化小说封面设计逻辑 | `01a11a3d-72c4-79a2-890f-fb720b7e2bde` | 完整骨架驱动的电影海报式封面、故事关系和人物选择、减少重复分析、实际候选图输出 |

依据为 [项目记忆](../../MEMORY.md)、[历史会话索引](../memory/session-index-2026-10-10.md)、[约束交接方案](2026-10-09-cover-constraints-studio-handoff.md) 和当前 [封面 Skill](../../skills/novel-cover-studio/SKILL.md)。历史对话用于还原决定，实际执行以当前来源、Git、服务及运行记录为准。

较晚决定覆盖较早提案：保留 `COVER_HANDOFF` schema-v2，只增加薄的可选 `cover_constraints`。不重新引入 `cover_design_contract`、`candidate_moments` 或由头脑风暴再次设计图片的平行合同。2026-10-09 的“已完成”和历史“533 项测试通过”均不构成本轮验收结果。

**交付对象是适用于不同小说的封面输出框架。** 用户在 2026-10-11 再次明确：不能只优化本书。书名、人名、发行定位和本次三个场景只属于项目输入与运行审计，不得进入通用默认值、构图模板或人物配额。

用户已选择 **He Chose His Hidden Family for Twenty Years…** 作为第一本实际出图验收作品，要求高质量优化并输出一组 **3 个候选封面**。这次出图验收重点是解决旧方向“同一女主换道具、主要冲突看不出来”的问题。该案例的真实图片不能替代其它题材的图片质量验证；跨题材合同回归验证框架边界，两者分别记录。

通用质量合同：

- 来源完整且可追溯；原文中的人物、因果、时序、知识与剧透边界不能被营销定位或漂亮画面改写。
- 依据实际压力来源设计可见的原因、回应和选择。人际、自然、制度、内在压力采用适合各自故事的视觉证据，不一律要求多人同框或同一类情绪。
- 方向之间改变读者关心的问题、关系组织和画面结构；换衣服、换颜色或换道具不足以形成新方向。每个方向有清楚的主动作、空间关系和可读的缩略图层次。
- 完整标题、字体、画面尺寸与保护区在消耗图片调用前预检；文字信息不足以证明最终图片质量，仍须检查真实成品。
- 盲读先保存，事实审核后进行；审核不能看到预设答案再反写盲读。未知保持未知，失败回到对应的来源、设计、评估或排版环节。
- 调用额度、不可变配方、文件摘要、候选与最终选中分别管理；新书、新 owner、新来源不能串用旧分析、批准或交付。

## 2. 最终职责边界

| 内容 | 头脑风暴 / 小说骨架 | 封面工作台 |
| --- | --- | --- |
| 人物身份、年龄、关系、事件、因果与结局 | 提供已确认故事事实 | 完整读取、交叉验证、引用来源 |
| 用户指定事实、必须考虑内容、禁止内容、剧透上限 | 如有明确要求，记录进可选约束 | 贯穿方向、编译、审批和质量审核 |
| 封面时刻、动作与反应、人物空间关系、视觉悬念 | 不另行生成封面设计合同 | 独占设计职责 |
| 场景 / 宣传复合构图、镜头、光线、色彩、标题排版 | 不负责 | 设计、比较、冻结可执行方案 |
| 外貌、衣着、天气、光线等未规定细节 | 可保持未规定 | 可提出 `model_inferred` 的封面专用视觉方案 |
| 图片、质量证据与最终选中 | 不负责 | 保存原图、标题成品、尝试记录、审核和选择记录 |

视觉推演不能写回故事正典，也不能从英文、北美市场或目标读者推断人物族裔。缺少发型、服装颜色或光线不能自动阻断；缺少身份、核心关系、冲突节点或明确事实边界才需要补足来源。

默认来源是选定项目的完整 `outputs/input/prompt.md`。完整正文、foundation、旧单人 brief 不得无声替代该来源。旧骨架没有 `COVER_HANDOFF` 或 17 节模板仍可使用，只要它具有完整故事信息；不得为了出封面重复创作整本小说。

## 3. 来源、分析与缓存

执行顺序为：检查来源 → 冻结快照 → 全源分析 / 复用有效缓存 → 方向设计与审核 → 编译冻结配方 → 绑定批准 → 生成候选 → 两阶段图像审核 → 明确选择 → 核对交付包。

来源快照必须保留完整原文、来源类型、owner scope、修订信息和摘要。模型引用需要落到该快照的原文与字节范围；`COVER_HANDOFF` 只是事实投影，不能自行证明约束所引章节存在。

缓存身份包含来源 bundle 和分析配置。相同来源、政策、配置的完整分析复用；切换方向、重编图片提示词、重评保留图片，不重新分析整本骨架。部分或失败的分析不得静默退回压缩 handoff 继续出图。来源或配置变化产生新的缓存身份，但修改当前模型设置不会自动撤销此前完整分析或批准。`require_current_head` 检查当前来源是否仍与批准相符；明确使用 `pinned_snapshot` 的方向继续绑定原快照，不自动追随新稿。两者都必须通过原分析状态、owner、来源与配方摘要及批准有效性检查。

本轮加入 `cover-segments.v2`，在已有 UTF-8 字节预算内合并连续小块，不跨文档，保持每个原文字节与证据绝对偏移。对于本次 12,812 字节骨架，离线核对结果为：旧 v1 有 46 个提取段、32 个复核单元，最低 80 次文本请求；v2 有 1 个提取段、1 个全源复核单元，最低 4 次。这个数字是正常分析流程的请求下限，不含协议修复、方向规划、图片生成和图像评估，也不是已观测的线上用量。

历史分析按记录中的 `segmenter_version` 读取、校验和续跑；保留 v1 原分段、证据 ID、摘要及旧批准，不就地改写为 v2。新版本使用独立缓存身份。明确续跑旧分析时保留其分段版本，其它模型与推理配置仍不得随意变更。

## 4. `cover_constraints` 的约束路径

新旧 schema-v2 均保留兼容。没有用户专门约束时省略可选字段，不凭空补一组用户偏好。存在时必须 typed parse、往返保留，并从不可变源恢复到分析 brief，模型不得删除或自创用户限制。

| 约束 | 应如何执行 |
| --- | --- |
| `must_preserve_story_facts` | 保留已确认事实；进入导演、受保护提示模块与事实质量审核 |
| `must_show_if_supported` | 只有来源支持可视化时才必须覆盖；已确认人物还需实际入画，引用 ID 不能代替出场 |
| `must_not_show` | 检查可执行场景与复合图层；完整禁止短语和自然语言语义分别由确定性检查与现有语义审核处理 |
| `spoiler_ceiling` | 作为图片场景边界和验收规则，不能在提示词压缩时丢失 |
| `preferred_story_node_ids` / `preferred_character_ids` | 排序、选题提示，不等于每张图必须包括所有人物或节点 |
| `user_visual_preferences` | 封面偏好，不升级为小说事实 |
| `source_refs` | 只接受 `section:N`、`section:N.<准确标题slug或支持的别名>`、`section:N.node:<节点ID>`；不可使用独立 slug/node，也不可由 handoff 中的自我提及证明 |

来源引用最多 32 条且不可重复；节点锚点必须唯一，编号子标题属于正确的父节。无效引用在模型生成方向前拒绝。图像提示只接收已批准场景的事实边界、禁止内容和剧透上限，不把候选 ID、自由偏好或整套来源列表再发成第二份设计要求。

自由文本判断仍需语义审核；字符串检查不能被描述为已经全面证明故事正确。反过来，语义模型也不能绕过可确定的 ID、来源、cast、摘要与预算检查。

## 5. 本轮 review 发现与修复

本轮审查基线为隔离工作树 `f804bb9186d823619aa247c4f9dacd7bd1d8f9c5`，同时检查未提交 diff 和新增来源模块。原工作区已有改动必须保留，不能整目录覆盖或回滚到旧快照。

| 问题 | 本轮处理与验证重点 |
| --- | --- |
| 复合封面图层和互动动作绕过禁语检查 | 扫描实际可执行的 layer、interaction 和 hook 字段 |
| 否定指令误报，中文连续短语漏报 | 支持常用冠词、直接名词否定与中文表达；保留转折后肯定描写的阻断 |
| 来源子串、空 slug、编号子标题造成误认或误拒绝 | 精确标题 / 显式别名解析，按标题层级限定节范围 |
| 全源合成丢失约束，缓存 tuple/list 往返不一致 | 从冻结源恢复约束，持久化使用稳定 JSON 形状；迁移保留已提取证据 |
| 导演通过后工作流再次校验误拒绝合法来源 | 工作流从冻结快照取得已验证引用注册表，传入最终方向校验 |
| 真实快照证据无法证明已入画的 must-show 人物 | 按 canonical character ID 关联完整分析 registry 与同一个 ledger，仍检查实际 cast |
| 约束来源摘要含换行，保存后摘要改变导致批准失败 | 首次绑定前按持久化规则规范化空白 |
| 无约束旧方向因新增 snapshot 键改变 evidence digest | 仅真实约束来源需要该键时才加入，保留历史 ledger 与批准 |
| 提示压缩丢约束，事实图像审核看不到约束 | `cover-compiler.v16` 保护边界模块，Stage B 接收作者约束；Stage A 保持独立 |
| 首次全文分析被空行拆成过多调用 | 引入版本化 v2 预算合并，并保留旧 v1 记录与恢复路径 |
| 全书人物重要性被误当成每张封面的出场要求 | 事实审核明确区分全书 registry 与已批准单图 / 图层 cast，作者约束仍按原范围优先 |
| 要求每张图解释全书，抹平不同封面方向 | 依据方向中可见的原因、回应、选择与悬念验收；全书事实用于核对，不把未揭示答案变成必画内容 |
| 英文、Western 或欧洲定位被旧入口改写成北美 | 新旧入口均保留明确市场；旧入口缺市场时标记未指定，不从语言推断 |
| 原图侵占标题留白，重排可能绕过原审批 | 独立排版提案、精确预览批准、应用记录与真实复审；原配方和图片历史保持不变 |
| 盲读只存在于内存，事实审核可能污染前一阶段 | Stage B 调用前持久化独立 Stage A；失败和重评保留旧证据，并产生新的 evaluation ID |
| HTTP 传输重试可能超出批准的图片次数 | 生产图片客户端只发一次请求，不在底层无声补充图片尝试 |

隔离回归已覆盖真实快照 → mock 分析 → 方向 → 批准 → 冻结生成参数，包含合法 section 引用和 `must_show_if_supported` 人物；历史无约束分析与批准、配方字节保持不变也有用例。上述 mock 结果不证明图片的故事表现已达标。

本轮原始审查记录位于 `/private/tmp/novelos-spec-review-20261010.md`、`/private/tmp/novelos-standards-review-20261010.md`、`/private/tmp/novelos-cover-flow-audit-20261010.md`。前端与执行环境记录为 `/private/tmp/novelos-frontend-environment-check-20261010.md`。它们是本机工作记录；本文保存可独立理解的结论，最终测试总数与同步状态见本文末尾。

## 6. 本次选定作品的真实故事边界

项目 ID：`he-chose-his-hidden-family-for-twenty-years-and-abandoned-our-daughter-during-her-most-important-life-events`。  
默认源：`docker-data/projects/<项目 ID>/outputs/input/prompt.md`。  
本次读取大小：12,812 UTF-8 字节；SHA-256：`b9e97861759e9099cd5a6351c16008b6ed06275a96a6f1acd64fc36d3d3fcb63`。这是文档核对值，正式运行仍须用工具实际返回的 snapshot ID / digest 绑定，不能凭本文跳过检查。

用户已确认并写入源元数据：**North America；Women aged 40–60**。这是市场与受众信息，不是人物族裔来源。本书为英文、现实家庭悬疑 / 家庭伦理剧，21 章；正文已完成，本任务不重新启动正文创作。当前骨架没有 `COVER_HANDOFF` / `cover_constraints`，应按无此可选字段的正常完整来源路径执行。

书名必须逐字保留，包括末尾英文句点：

> He Chose His Hidden Family for Twenty Years and Abandoned Our Daughter During Her Most Important Life Events.

| 人物 | 来源确认的身份与封面需要保留的关系 |
| --- | --- |
| Eleanor Vance，50 | Richard 的妻子、Chloe 的母亲；曾支撑丈夫事业，后选择保护女儿并反击 |
| Richard Vance，55 | 药品销售出身的区域主管；对妻女冷漠，长期用家庭资源供养另一家庭 |
| Chloe Vance，23 | 护理专业优秀毕业生、Eleanor 与 Richard 的女儿；从讨好父亲转向与母亲结盟 |
| Julianne Foster，45 | Richard 的婚外关系对象，受其长期资助；不是 Eleanor 或 Chloe 的亲属 |
| Leo Foster，20 | Julianne 的儿子，受 Richard 资助并获赠新皮卡；不得擅自补出生父身份或亲缘证明 |

完整角色 registry 应保留这五名已命名角色，具体出场由故事事件与方向决定。骨架只提及未命名的律师；不能把旧 foundation 中的额外命名人物无声混入本次骨架正典。

### 毕业与生日是不同现场

Chapter 1 是 Chloe 的护理毕业典礼：Chloe 演讲，Eleanor 身旁为 Richard 留着空座；Richard 用 Cleveland 医院设备故障作借口缺席。

Chapter 3 的母女位于毕业午餐；她们看到转发的 Fort Wayne 烧烤生日视频：Richard 拥抱 Leo，并送出新皮卡钥匙，视频时间证明他的借口为假。生日庆祝和毕业典礼发生在不同地点，午餐观看又是另一个画面时点。

- `scene` 只能表现一个连续现场，不能让 Eleanor、Chloe、Richard、Julianne、Leo 虚构地一起参加毕业或生日。
- `campaign_composite` 可以将有据的两个现场做有设计的宣传复合，但每层必须声明地点、时点、作用与相互关系；不得暗示母女亲眼站在生日现场，也不得把视频回看当作同一瞬间的目击。
- 原作明确在生日视频里行动的是 Richard 与 Leo；其它角色是否属于该图层需有来源支持，不能为了凑人把五人全部放进同一事件。
- 屏幕小字、账单文字或一堆道具不能独自承担“他选择另一个家庭”的核心信息。缩略图需要读得出人物关系、选择及其后果。

### 可供导演选用的三个不同叙事方向

以下为来源支持的设计候选池，不是预先批准的构图，也不是新故事事实。

1. **缺席与偏爱的对照**：Chapter 1 / 3，毕业母女与 Richard 对 Leo 的父亲式偏爱形成反差（未证明生父关系）。适合明确分层的宣传复合；保留为何被抛下的悬念。
2. **女儿停止讨好**：Chapter 6–9，Richard 推开毕业文凭或打碎旧熊吊坠，Chloe 的反应发生改变。只冻结一个真实动作，不混合成不可能同时发生的表演。
3. **母亲划下边界**：Chapter 10，Eleanor 将 Richard 的西装和公文包移到门外、改变家庭空间的准入。突出主动选择，而非只有悲伤肖像。

至少一个方向需要直接读出完整核心因果关系，至少一个清楚表现主角具体选择；每个方向都须保留可见因果证据与可读的主角反应。人物人数不是质量指标。不得揭露 Chapters 18–21 的法院和解、资产归属、第二家庭崩解及一年后圆满结局；不添加超自然、暴力惊悚或豪门奢华来替代本书现实家庭冲突。

## 7. 批准、三图预算与图像验收

方向必须先经来源、人物、剧透、组合差异及导演语义审核，再以 `cover-compiler.v16` 编译为冻结 recipe。批准绑定准确来源、analysis、brief、direction、recipe、字体 / 标题布局和调用额度；内容变化后不能沿用旧批准。

本轮初始预算为 **3 个候选，各一次 `gpt-image-2`、`n=1` 调用**；`extra_image_attempts=0`。失败与手动重试不能隐式补充预算。分析修复、导演修复、图像审核与图像生成应分别记录真实用量，不能把“3 张图片”说成总共 3 次模型请求。

优先采用 `composed`：生成无文字 artwork，再用固定字体字节与已批准排版合成准确书名，保护人物脸部、手部及故事关键区域。本书标题较长，应在完整尺寸与手机缩略图分别验收换行、字序和可读性。不要添加作者行、宣传语、副标题、logo 或水印；源中的 Author 元数据不代表封面应印作者行。

保持竖版 2:3、PNG 或 JPEG，保留 provider 原生尺寸。实际 size、quality、format 以本轮正式配置快照为准，不把偏好尺寸写成实际完成尺寸，不用放大图冒充原生输出。

| 阶段 | 输入与必须保存的证据 |
| --- | --- |
| Stage A：独立盲读 | 无标题 artwork 和缩略图；不提供故事梗概、预期 hook 或设计解释。先保存实际看见的人物关系、动作、压力、异常、问题、未知与文字污染情况 |
| Stage B：事实与视觉审核 | 接收冻结的 Stage A、最终封面和故事事实 / 作者约束；验证忠实度、剧透、因果、表演、画面层次和标题，不可重写 Stage A |

缺失、污染或不可用的评估维度保持 unknown / human review，不补造分数或宣称通过。故事是否被读懂与摄影、排版是否优美分别评估：不能用漂亮脸孔掩盖核心冲突缺失，也不能用阴暗纪实感替代视觉设计。

设计语义失败回导演修改并重新批准；评估失败复评现有图片；排版失败优先使用保留 artwork 本地重排。新增图片调用必须处于明确批准的累计额度内。

候选生成、推荐、明确选中、交付包更新是四个不同状态。即使有推荐图，也不能将其自动替换为正式选中。用户尚未指定本轮最终选择时，交付三张候选及审核依据，保留旧选中与旧交付。用户明确选择后，再核对 selected cover、`cover-set.json`、manifest 和 ZIP 内的实际图片及摘要。

### 本地排版修订

当图片没有遵守预留标题区时，使用 [通用排版修订入口](../cover-typography-amendments.md)：propose生成精确预览 → 查看 → approve绑定摘要 → apply进行真实A/B复审。原recipe与旧attempt不改；overlay保持原图，contain_panel保留完整原图并等比缩放进实色画布。该授权只允许本地合成，图片增量0，不增加原批准的图片额度。

新布局不会自动清除原有故事或组合差异阻塞，也不能沿用旧人工接受。当前通过CLI及project/workshop API操作，现有Studio显示新候选和审核；没有新增可拖拽排版编辑器。v1只作用于原owner，已导入历史候选不能就地修订，含排版修订的workshop候选也不能跨owner迁移批准；系统在复制前明确拒绝，避免生成失去审批记录的导入候选。

## 8. 正式运行入口与环境

正式 Studio 为 [目标作品封面工作台](http://localhost:5174/projects/he-chose-his-hidden-family-for-twenty-years-and-abandoned-our-daughter-during-her-most-important-life-events/covers)。链接本身不证明运行成功；部署后另记健康与候选可见性检查。

复用正式 Docker 数据和媒体挂载。容器路径应为 `/data/projects/<项目 ID>`、`/data/media`、`sqlite:////data/novel_os.db`、`/data/studio_settings.json`；不输出或复制凭据。CLI 和 API 应使用同一设置文件、数据库、媒体目录和 `<WORKSPACE_ID>:project:<项目 ID>` owner scope；本次实例的工作区为 `default`。宿主工作树修改不会自动进入镜像，实际出图前需核对运行代码版本。

新书使用原生 CLI 时，必须是已初始化的 Novel OS 项目，具备 `outputs/state/story_state.json` 和完整骨架 `outputs/input/prompt.md`。仅有 Markdown 的任意目录不满足项目入口；尚未创建项目的骨架可以先在 Workshop 中完成封面流程。不要为通过校验而伪造项目状态文件。

以下变量均来自当前作品与上一阶段真实返回值；`WORKSPACE_ID` 使用原 owner，`PROJECT_PATH` 使用当前运行环境的项目路径。命令示例在仓库根目录和对应 Python 环境运行，`--count` 可选 3–5，本例为 3：

```bash
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" inspect-source --project "$PROJECT_PATH"
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" analyze --project "$PROJECT_PATH"
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" plan --project "$PROJECT_PATH" --analysis-id "$ANALYSIS_ID" --count 3
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" approve --project "$PROJECT_PATH" \
  --direction-id "$DIRECTION_ID" --approved-direction-sha256 "$DIRECTION_SHA256" \
  --expected-brief-sha256 "$BRIEF_SHA256" --confirm-source --extra-image-attempts 0
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" generate --project "$PROJECT_PATH" \
  --direction-id "$DIRECTION_ID" --approved-direction-sha256 "$DIRECTION_SHA256"
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" list --project "$PROJECT_PATH"
```

`ANALYSIS_ID` 来自完整分析结果；`DIRECTION_ID`、`DIRECTION_SHA256` 和 `BRIEF_SHA256` 分别使用 `plan` 返回对象顶层的 `direction_id`、`direction_sha256`、`brief_sha256`，不要混用内部 binding 或文件字节摘要。核对来源、事实、方向与额度后才批准；批准初始候选不等于授权额外重绘。新书的发行市场与目标读者按其自身信息填写，语言不代替市场。

出图后查看真实成品、缩略图和质量报告。只有用户明确选择后才执行以下命令；`COVER_REVISION` 来自该批次最新 `revision`，`ACTIVE_REVISION` 来自当前 `outputs/covers/index.json` 的 `revision`（全新且不存在 active pointer 时为 0），二者不能混用：

```bash
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" select --project "$PROJECT_PATH" \
  --cover-set "$COVER_SET_ID" --candidate "$CANDIDATE_ID" \
  --expected-revision "$COVER_REVISION" --expected-active-revision "$ACTIVE_REVISION"
```

需要人工接受的候选还必须提供真实的 `--quality-acceptance-reason`，不能用通用套话忽略质量门。选择后核对封面文件、manifest 和 ZIP；未选择时保留旧交付。`deploy.sh novel-cover` 当前的旧快捷路由不直接支持 inspect-source / analyze / plan / approve，不能将它们误当 prompt 文件名执行。

本轮仅为加载已验收代码而需要部署时，由根代理记录构建、更新和健康结果；不要为封面命令单独启动另一套长期并存的 5174 环境。

## 9. 本轮真实运行与验收

以下为已执行的实际调用、排版修订与成品复审结果；不依据设计或 mock 测试推定实际图片通过。审计根目录为目标项目 `outputs/covers/delivery-20261010/`。

| 验收项 | 实际结果 / 证据 |
| --- | --- |
| 主工作区同步内容、最终 Git 状态 | 65 个修复路径逐文件校验同步，保留原有脏工作区；同步清单在目标项目 outputs/covers/delivery-20261010/sync-complete.json。尚未 Git 提交或推送。 |
| 后端完整与定向测试命令、通过 / 失败数量 | 包含通用排版、单图审核范围与旧入口市场修复的主工作区全仓：2,678 passed / 4 warnings / 175.13 s，命令 venv/bin/python -m pytest -q -o addopts=''，日志 full-backend-final.log；此前排版阶段2,658项通过。首次受限沙箱因本地telemetry服务权限失败后中止；允许localhost mock服务后全仓通过，未修改业务或测试阈值。 |
| 前端 tsc / lint / test / build 最终核对 | 最后一次侧栏文案修正后重新通过：23 文件、226 tests / 10.18 s，lint与含tsc的build通过。侧栏改为“设计与审核”，不再对所有配置宣称2K；正式构建设置 VITE_API_BASE 空值，以同源 /api 请求。日志frontend-final-tests/lint/build.log。 |
| 正式镜像 / 代码版本、部署时间、健康检查 | 2026-10-11 00:56:27 +08:00 完成最终后端部署；image=sha256:452ea712a0e65ef6330b2d72637f058ea727c6287e720e1383348f12e625d058，Docker health=healthy，26个运行路径摘要与同步清单一致。复用依赖完全一致的本地运行时离线构建，保留 pre-cover-20261010 回退标签。证据deployment-final.json / runtime-final-sha256.json。 |
| 前端镜像与最终浏览器检查 | 2026-10-11 13:26:24 +08:00 更新前端，image=sha256:f8f115809aadeb8eecc281c3d3bc6f26f1fed4d01292aab04ba186d20ec9ed85，healthy。5174实际显示三张新版封面、A/B人工审核状态、C质量阻塞、额外额度0；控制台0错误，相关API和媒体均200。http-final-verification.json逐张核对经HTTP返回的PNG与审核成品SHA相同，截图browser-final-candidates.png。 |
| 实际数据挂载、DB、媒体与 owner scope 一致性 | 正式容器 /data 对应主仓 docker-data；DB=/data/novel_os.db，media=/data/media，owner=default:project:<项目 ID>。浏览器项目/封面/方向/源/分析接口全部返回200。 |
| image / director 模型、推理配置与尺寸（不含密钥） | OpenAI 官方内置 Codex；director=gpt-5.6-sol / high / 600s；image=gpt-image-2 / 1024x1536 / high / 300s。用户已明确授权完整骨架、衍生提示及3张图片向该服务传输与审核。 |
| Source snapshot ID、content / bundle digest、freshness policy | snapshot-6e249e717dcb4aab87795a00980bc70e；content=b9e97861759e9099cd5a6351c16008b6ed06275a96a6f1acd64fc36d3d3fcb63；bundle=176a926f5388b8f31c78ea25ab3780bb4c8b1433ddc8d5b34c5384f12ccf8bff；require_current_head。 |
| Analysis ID、版本、完整覆盖、缓存与真实请求用量 | 最终纠正分析 analysis-2aa40035a38060509cf9ff5ecb260835，analysis 内部内容摘要 analysis_sha256=fd020c5313a1d9de02bb8c880b362e3e262fae7a9193499c6fd5f2c0b19a6419（不含该字段本身）；analysis.json 实际文件字节 SHA=aae721c5a00aba10a4c6d4cf5b9ba7a1880ee67d92685ab9dcc93b69b772a0c8；工作流规范化完整 payload SHA=df7ed12e5af3d186064a4ddadef5354e7eeada2be02e1f09689263e09d7ae132（含analysis_sha256），三者含义不同。cover-segments.v2 覆盖 1/1 全文段、58 条提取证据。父分析7次调用及一次响应重验证保留；child复用提取，新增3次真实调用（纠正综合、来源审核、最终核验），均完成。六条 agent review 意见独立复查通过。父 analysis-97a784d84eb246edb27c78422469ca50 因曾有事实错误永久停用于规划/缓存，原9个备份文件逐字节未变。 |
| Direction ID、3 个 concept ID、语义审核、recipe hashes | 最终 direction-8c72019caaa442ddb3c07a695d6c54d7，SHA=95c13ba534ae00031087100b3ac8deaf2f819b7b3aedd8c1022d7962c8ab66a8；完整原文上下文设计/审核共4次调用，删除后续章节误引后审核与独立复核通过。三方向与配方见下表。较早 direction-3df7f518440b41459ca6c0372a8bb5ee 保留，未批准、未出图。 |
| 准确批准 hash、source 确认、初始 3 图 / 额外 0 额度 | binding_sha256=b44e8b68c41d0743f30743c13ea02296d41dc0e98de0e1d2695c5519fa3ac73b；recipes_sha256=5276a0eeed4294d9693b89f1dc3ef2cc693f1b5a6d8a5d63b39720f05a5025ee；source_confirmed=true；initial_image_calls=3，extra_image_attempts=0，batch_reserved=true。 |
| Cover-set ID、每张 candidate / attempt ID、真实图片尺寸与路径 | cover-e63f4fced87941758a74a62453e5aa70；原始批次 revision=8，最终 revision=13；3张 ready，全部原生 1024×1536 PNG；实际图片调用3次、本地排版3次、额外图片调用0次。原始结果保存 generation-original-result.json，原图及旧排版保留。候选与原始attempt见下表。 |
| Stage A / Stage B 结果、缩略图、unknown / blockers | 三张已正式 propose/approve/apply 本地排版，分别完成新 A/B 审核；单图审核范围修复后 B/C 再次新建盲读与事实审核。最终 A/B=human_review_required，硬阻塞为空；C=blocked，architectural_smearing。全部 evaluation_status=complete，盲读先独立落盘，未修改历史报告。 |
| 三图差异、故事表现与长标题排版的实际结论 | A是跨时空偏爱/受伤对照，B是证据桌边的对峙与记录，C是门口拒绝准入。原7行标题压住人物；正式本地4行成品保持完整标题并避开人物。A故事反差最直接；B的记录动作复审确认，但丈夫表演稍夸张、缩略图道具细节较弱；C的选择清楚，但门框伪影尚未解决。 |
| 用户明确选择 / 尚未选择，旧选中与旧包状态 | 用户未选择本轮新候选；原 active cover set=cover-fa7676647b0e4591930033e93f178f8b，active revision=1。三张排版应用与两次追加复审后，再次核对旧交付39个文件SHA均未变；原方向、图片批准、三个原始attempt和原画SHA均保留。 |
| 如有新选择：selected cover / cover-set / manifest / ZIP 一致性 | 本轮未选择新封面，不触发替换；旧 candidate-628e9465d4ea4fe48a41e08a9bbd9fa9 保留。 |
| 尚存问题、后续动作与预算边界 | A/B仍需人工明确选择，推荐先比较A；C需修复原画门框伪影。当前额外图片额度为0，不自动重绘或宣称三图全部达标。现有服务的图像修复是重新渲染，不支持局部补绘；如继续图片修复，须另有明确额度授权。完整长标题在很小缩略图上仍较密。 |

交付包括本交接文档、Studio内三张最新候选、候选审阅ZIP及完整质量报告。候选审阅包不替换既有 book-package.zip，不能等同于已选中或已发布。

### 已生成方向与原始尝试

| 方向 / concept ID | Recipe SHA-256 | Candidate / 原始 attempt |
| --- | --- | --- |
| A：direction-01-the-warmth-he-gave-away | 5d9cd7a56c82f2cc52b1f181b89c4ec823906fc932c33b2fb797d14dc32ad25e | candidate-bdd5e774c1514bcb8e5fabcf95e422c9 / attempt-a823a41d8f8144128251da702089a205 |
| B：direction-02-the-record-she-keeps | 48209e12be0d220b01d6c33c7b9d3520d84773b914c8d87e3f5cba6f04b3ef99 | candidate-f65d62f1d75c472d9f112c548f85cd02 / attempt-d77b438728e948e3958599766df4a0d6 |
| C：direction-03-the-code-she-changes | 57cb0d0078f3e75987bdab9e5adc9e8c14e85c5532dc306c8f45825caf486a15 | candidate-0d9a94bebeec470eb4109140b707e598 / attempt-eb3010f0d0f142059536aac744655610 |

实际交付尺寸是1024×1536，不是原生2K。无标题原图在 `outputs/covers/artwork/<artwork SHA>.png`；原始标题成品在 `outputs/deliverables/covers/pending/cover-e63f4fced87941758a74a62453e5aa70/cover-01.png` 至 `cover-03.png`。正式排版修订应使用候选最新的 relative_path，不能误将这些旧成品当最新输出。

### 最终候选与排版记录

最终 PNG 均位于该项目 `outputs/deliverables/covers/pending/cover-e63f4fced87941758a74a62453e5aa70/`。文件名、字节摘要、排版批准与最新独立审核如下；完整核对见 `delivery-20261010/final-delivery-verification.json`。

| 候选 | 最终文件 / SHA-256 | 排版 amendment | 最新 evaluation / 结论 |
| --- | --- | --- | --- |
| A | `typography-bf4a315404a04753b8777b10948e06f6.png` / `85e9d709650622d740c6393de0b020b8c678b0df6b7c4f8e271aef81ea513e85` | `typography-bf4a315404a04753b8777b10948e06f6` | `evaluation-5cc6bf054b3f4940a94cf0c567577c49` / `human_review_required`；阻塞：无 |
| B | `typography-f8a919aca79b4602bfbaf74fa07526ab.png` / `bab1ab1cd940479d5ad2c308c414aa8566ce7e7b9ea8fb441f8b35d00877dc99` | `typography-f8a919aca79b4602bfbaf74fa07526ab` | `evaluation-05f330247880460a92bf740ad78026bd` / `human_review_required`；阻塞：无 |
| C | `typography-56931171fc4f452590e7cc17c142e4bc.png` / `6f5bdb7829c3f00fbcefe482ac4cc1dfa4c2ba9f65ed7a53c03d57e3bae26f7f` | `typography-56931171fc4f452590e7cc17c142e4bc` | `evaluation-76b4039560cd45d7bd8458169e50872e` / `blocked`；阻塞：architectural_smearing |

审核变化必须保留：B 从全书故事误要求/记录动作争议变为方向范围内的可见证据对峙，最新复审确认女主记录动作，仍提示丈夫表演偏夸张；C 的“缺少女儿”误报已消除，新审核发现具体建筑伪影，因此仍然阻塞。没有为出结果删除不利意见或覆盖旧报告。

交付审阅目录为 `outputs/covers/delivery-20261010/reviewed-candidates-20261011/`，ZIP为同级 `cover-candidates-reviewed-20261011.zip`，包含三张PNG、完整质量JSON、摘要manifest、说明及本交接文档。最终选择留给用户，旧封面与旧图书包均保留。

### 通用能力的实际边界

- 完整来源上下文在设计、修复和语义审核全程保留；超过请求预算会明确失败，当前没有为超长文档实现自动的有据段落选择器，不能无声截断后声称全源审核。
- 本地排版支持统一字体、字号和颜色及准确换行；尚无逐词字号/变色的高级版式引擎。
- 创意与审美仍需实际图片验收。跨小说合同矩阵覆盖不同题材元数据、中英文、长短标题与两种构图，但模型使用fixture，不证明所有题材已经实图达标。
- 独立模型也会漏报：原图A的事实审核没有充分指出标题遮挡。最终交付必须结合实际成品检查，不只看模型分数。
