# oh-story-claudecode 深度分析及 Novel OS 接入建议

日期：2026-09-12。

## 0. 范围、版本与结论

- 外部仓库：`zenstory-ai/oh-story-claudecode`，此次下载并核对的提交为 `aa3574fe6887fe1204168167b747d709607e77b6`；提交时间 `2026-09-11T10:07:05-07:00`。`skills/story/VERSION` 与插件清单均为 `0.7.10`。
- 本地 Novel OS：`dea7d586026cc7d8e25b4b5a445a955602f23c84`。
- 方法：浏览官方仓库并只读检查实际源码、Skill、工作流、测试定义和实验记录。不仅依据 README。
- 未安装外部 Skill，未执行其 setup、hooks、采集器、模型、Dashboard 或测试；未改变引擎源码、服务、H5 项目或已生成小说。本文不是运行兼容性验证、渗透测试或作品质量实测。
- 当前新小说工作坊暂停，尚未启动正文生成。本报告不表示其他项目后台任务已经暂停。

**三个问题的直接回答：**

1. **它属于小说创作引擎，但更准确是“依托 AI 编程助手会话运行的创作工作流工具箱”。**它有真实的脚本、状态事务和检查器，不只是提示词；但其完整创作编排主要由宿主会话执行，而非独立后台全书任务服务。
2. **Novel OS 更偏生产与交付，oh-story 更偏创作方法、过程辅导和文件工作流。**两者都有规划、写作、检查、记忆；区别是运行权威、质量落地方式、目标市场与发布契约，不是简单的“有引擎/没引擎”。
3. **适合选择性吸收方法、评审规则与上下文组织思想，不适合直接整体安装后作为第二套内核。**保留 Novel OS 的唯一正式状态、模型路由、版本晋升、封面链及 EPUB/H5 交付。

本地逐项源码依据另见：[Novel OS 架构事实与接入边界](2026-09-12-novel-os-integration-surfaces.md)。下文外部链接固定到本次提交，避免 main 更新后结论漂移。

## 1. 它究竟实现了什么

### 1.1 分层架构

```text
用户 / Claude Code、Codex 等宿主会话
    ↓
story 入口及 13 个 Skills
    ├── 长短篇扫榜、拆文、写作
    ├── 导入、审查、去 AI 味、封面
    └── 基础设施部署、浏览器采集支持
    ↓
按任务读取的 references + 专业 agent 定义
    ↓
确定性工具
    ├── outline_view：按卷 / 剧情单元取上下文
    ├── build_writer_prompt：组装写手输入骨架
    ├── storyctl：章节检查、字数处置、提交
    ├── tracking_commit：状态事务与派生视图
    ├── author_memory_commit：分作用域作者偏好
    └── 文本 / 提纲 / 格式检查器 + 宿主 hooks
    ↓
本地故事文件、追踪 JSON、作者 / 读者时间线
    ↕
本地 Dashboard：浏览、搜索、编辑、删除文件
```

源头分别见：[长篇入口](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/SKILL.md)、[章节控制器](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/scripts/storyctl.py#L95-L223)、[Dashboard 路由](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story/scripts/dashboard-server.mjs#L758-L829)。

**它不是“没有 Web、没有状态、没有并发控制”。**Dashboard 有文件编辑 API；tracking 有项目锁和 revision 检查。只是这些能力不等于持久化生成任务队列、服务端 provider 管理、全书恢复编排或 H5 发布服务。

### 1.2 真正由代码实施的能力

| 能力 | 实际实现 | 解释边界 |
|---|---|---|
| 章节完成判定 | `storyctl chapter check/commit/accept-current-length`，重新读取正文、计数、运行检查，再提交 tracking | 判定脚本可执行，但语义审查主要仍由会话 / agent 完成 |
| 顺序与竞争写入 | 项目级 `.tracking-commit.lock`、`expected_state_revision`、append 必须为上一提交章 + 1 | 有本地并发写保护；不是跨机器的分布式小说调度 |
| 状态权威 | `_tracking-state.json` 是结构权威，Markdown 时间线、角色快照等由它派生 | 不从这些 Markdown 反向恢复全部事实 |
| 中断后的发现 / 恢复 | 先写派生文件，最后原子替换权威 state；`check` 对比派生文件与 state | 多文件不是单个数据库事务；中途失败可能出现可检测的视图不一致，提交成功后的原事务也会因 revision 改变而被拒绝 |
| 上下文分层 | 卷级常任、单元级、在用 / 退役批次；写作档排除排纲底稿 | 依赖上游正确声明作用域，遗漏事实不因有取段器而自动补回 |
| 文本故障检查 | 复读、截断、占位、工程词泄漏，以及中文套话候选 | 机械检查不是通用文学质量评估，也不是准确的 AI 来源检测 |

代码：[tracking 事务](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/scripts/tracking_commit.py#L903-L950)、[提交与检查](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/scripts/tracking_commit.py#L1123-L1188)、[卷纲取段器](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/scripts/outline_view.py)。

### 1.3 一个必须分清的概念：Reference Gate

“先完整阅读参考文件再写作”是一种明确的会话工作规则，但**不是代码已经验证模型读到了全部参考内容，更不是验证模型理解并运用了它们**。

该仓库自己的 `scripts/check-reference-gates.js:3-9` 明确说明：此测试固定的是提示词规则与路径，实际遵从情况要通过真实写作实验观察。另有真正的 runtime hooks，负责细纲存在性、tracking 连续性、上一章遗留问题等；二者不是同一种门禁。[Reference Gate 测试说明](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/scripts/check-reference-gates.js#L3-L9)

Codex 适配代码也有边界：章节路径匹配依赖 `正文/第N章…md`；Stop 内容扫描是 best-effort、非阻塞，代码返回 `continue: true`。把这套 hooks 复制进 Novel OS，并不会覆盖 `outputs/chapter_...` 的所有路径或所有 provider。[Codex guard](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-setup/references/codex/hooks/story_codex_hook.py#L1211-L1264)、[Stop 实现](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-setup/references/codex/hooks/story_codex_hook.py#L1522-L1546)

## 2. 与当前 Novel OS 的差异

| 维度 | oh-story-claudecode | 当前 Novel OS | 对本项目的意义 |
|---|---|---|---|
| 主要用户场景 | 作者在会话中选题、拆解、逐章 / 小批量写作与审稿 | Workshop 确认后，后台按阶段生产全书，再交付运营 | 保留当前产品形态，不将全书生产退回聊天接力 |
| 默认市场 | 起点、番茄、晋江、盐言等中文网文；含多种男频 / 女频技法 | 支持多种分类；当前商业合同明显针对家庭、女性情感与读者归属感 | 方法要为欧美英文女性读者重新校准 |
| 长短篇 | 长篇卷纲 / 单元 / 章；短篇常为单文件多小节，默认总量 8,000–20,000 | short_novel / standalone_long / multi_volume / series_installment，有确认与连续章号 | 类型名称相近，不等于字数、章数和卷含义一致 |
| 运行与恢复 | 宿主会话调度；tracking、hooks 辅助恢复；日更单轮最多 3 章 | run_id、stage checkpoint、输入输出 hash、resume/retry、质量暂停 | 不能用其 last_committed_chapter 替换 run manifest |
| 状态与正式稿 | tracking JSON 与派生文件、revision 检查 | foundation、proposal、不可变 artifact revision、Final head、canon journal、receipt | 只保留一条正式提交链，避免双写 |
| 写作方法 | 较细的题材资料、情绪设计、反转、对话、删改判断 | 已有商业 / 因果合同和五角色提示，也有 Workshop 技法 | 值得补的是具体执行与诊断方法，不是再加五个同名角色 |
| 免费 / 付费桥 | 有付费点设计；短篇检查设定与大纲标记是否对应 | 免费窗口商业报告绑定具体 Final、合同与 promotion，后续规划可被阻断 | 吸收方法，保留现有实际免费窗口与证据绑定 |
| 多卷 / 系列 | 创作层卷纲、终局资源和滚动规划 | 另有 H5 章节—卷映射与跨书系列身份 | 创作单元不直接当作 H5 volume；跨书系列也不等于共享 canon |
| 模型 | 依托宿主 CLI / agent；各端适配能力有差异 | CLI 与 API provider 路由、角色模型和推理强度 | 方法需编译成通用阶段输入，不绑定特定会话工具 |
| Web | 本地文件工作台，有读写删除 | Studio、项目/章节任务、模型配置、Cover Studio、交付 | 外部 Dashboard 无需替换当前前端 |
| 封面 | 题材关键词 → 风格表 → 2–3 种构图；默认数字绘画 | 故事 handoff、证据与方向审批、人物/冲突/摄影策略、多候选和历史 | 直接导入外部封面规则会逆转用户已确认方向 |
| 发布 | 本次源码检查未找到对应的 EPUB / H5 包生产合同 | Introduction、EPUB、分类、章节定位、稳定书章身份、独立更新摘要、ZIP | 交付层保持不动 |

外部依据：[长篇工作流与范围](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/SKILL.md)、[短篇流程](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-short-write/SKILL.md)、[付费点检查](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-short-write/scripts/check-phase2-contract.js#L304-L324)。本地逐项证据见配套报告第 2–8 节。

### 2.1 Novel OS 不是“只要多加规则就会更好”

此次本地源码审阅发现四个与接入直接有关的限制：

1. **上下文覆盖。**`core/context_pack.py:39-55` 对超过 12,000 字符的章取前 6,000 与后 4,000；普通连续性 Guardian 使用此函数。长章中部可能未进入这次审阅。商业 Guardian 另行接收完整候选，不能泛称所有评审都遗漏中段。普通 800–900 英文词章节也不一定达到该阈值，不能把它归因于每一本现有书。
2. **证据完整性与语义质量分离。**`core/pipeline_runner.py:3298-3309` 的晋升报告传空 `continuity_findings`，`core/quality/lab.py:78-79` 的语义维度为空。前置连续性与商业检查仍存在；问题是不能把这张晋升报告描述成独立综合文学认证。
3. **恢复依赖。**普通章节 checkpoint 尚未把任意外部技法包版本纳入依赖。若只替换方法文件，resume 可能继续复用旧输出，需要显式版本失效规则。
4. **并发配置。**`core/pipeline_runner.py:649-650` 会写进程级模型环境变量，ModelRouter 有相应全局回退；同进程跨项目配置存在潜在串扰，尚未在本次复现。Web jobs 也主要是内存线程，而不是持久化 worker 队列。

这些不否定现有生产链；它们说明改造重点应是**可见的正文质量证据、上下文覆盖与运行隔离**，而不是只扩大 prompt。

## 3. 最值得吸收的五项能力

### 3.1 功能性去 AI 味：先判断“这句话在做什么”

外部规则里最有价值的部分不是禁词数量，而是：

- 情绪已被冲突和对白表达时，不再机械补一个握拳、抿嘴、呼吸停顿。
- 细节应带来信息、选择、关系变化或动作后果，而非换一组身体部位继续表演同一种情绪。
- 去掉某处细节后没有叙事损失，可以删除；有功能则保留。
- 允许必要的直接表达，不把所有普通情绪词强行改为动作。

这适合当前英文家庭 / 背叛题材：自然感应来自人物的具体目的、说话选择与处境，不来自重复的生理描写。应把原则改写成英文评审规则，而不是翻译一张中文禁词表。[writing-craft 的具体规则](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/references/writing-craft.md#L18-L54)

**接入点：**Editor / Style 的现有阶段，先给带原文位置的建议，不新建一条无限重写流水线。修订后仍核对事实、POV、免费章承诺与字数，不为清零而改变故事。

### 3.2 主角因果权与兑现归属

其“谁决定、谁选择、谁承受、谁得到回报”的拆解，比“主角要有能动性”更容易评审。适合避免女主始终承受羞辱、最后由第三人包办反击。

Novel OS 已有 `active_choice`、`local_payoff`、`protagonist_causes_turn`，不是从零新增。增量应是：对这些字段要求**正文中的动作及后果证据**，而非再建同义台账。[外部读者契约](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/references/reader-contract-and-progression.md#L9-L60)

**免费窗口应用：**第 3 或 4 章截止前，读者既看到一个有价值的局部兑现，也看到下一步值得付费的选择及后果。并非越晚揭露、越多羞辱就越有留存。

### 3.3 写作期与规划期分离的上下文

外部的卷纲取段器把“本卷长期约束”“当前剧情单元”“排纲草稿”分开，这是比整本大纲截取更可控的设计。

**接入点：**扩展 `core/context_pack.py`，先按已批准合同建立相关事实的闭包，再放技法。记录实际装配内容、遗漏项、预算、方法版本。长章评审按场景覆盖并聚合，不只保留首尾。

关键事实不可因加入技法而挤出；“上下文更短”不自动等于“更正确”。它的 `build_writer_prompt.py` 也仍依赖主会话填写八类判断槽，并非完全自动完成语义召回。[装配器职责边界](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/scripts/build_writer_prompt.py#L2-L21)

### 3.4 客观事实、读者认知、POV 知识隔离

外部 tracking 有 `objective_fact`、`reader_knowledge`、`reveal_status`、`reveal_chapter`，派生作者 / 读者两套视图。未揭示事件不把未来计划章当作已揭示证据。[时间线实现](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/scripts/tracking_commit.py#L446-L552)

**接入点：**复用 Novel OS 的 `allowed_knowledge` 与秘密 / 事件状态，增加按角色、截至章节过滤的可见视图。三者需区分：读者知道的事，当前第一人称角色也未必知道。外部“作者 / 读者双视图”本身并未解决全部多角色知识建模。

### 3.5 有作用域的作者偏好与文风裁决

外部偏好有 book / genre / workflow / global 作用域，区分 active 等状态；query 只取相关 active 项并受字节预算限制。文风裁决按维度处理，当前要求和本书表达优先于通用技法；书级白名单不向父目录继承。[作者偏好查询](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story/scripts/author_memory_commit.py#L754-L811)、[文风裁决](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/references/style-resolution.md#L5-L27)

**对当前需求特别有用：**长期偏好“英文、欧美女性读者、少财务推演”可以按确认的范围保留；上一部作品的具体人物、反转、职业、场景不得因此迁到下一本。已有 Workshop 的 audience_profile 与确认记录应复用，不另外保存一份互相竞争的用户事实。

## 4. 不宜直接继承的部分

### 4.1 封面 Skill 与当前要求相反

源码具体规定：

- 按书名关键词选题材，多类型取预设优先级，未命中默认都市。
- 使用题材对应的字体、色彩与构图表。
- 三种构图中允许纯场景。
- 完整模板包含数字绘画，并明确建议避免照片感。

这与用户已确认的**人物必须在场、核心关系冲突可读、自然真人摄影、每书独特设计语言**明显不合。[封面规则](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-cover/SKILL.md#L52-L143)

因此，第一轮接入排除其封面 Skill。可单独学习版本保留、上传安全区等通用做法，但这些也不替代当前方向审批、图像历史、质量报告、回退及 H5 选图关系。

### 4.2 中文检查器与篇幅定义

`wordcount_core.py` 使用 `visible_chars_v1`，计算可见非空白字符，不是英文单词。部分题材、句式、标点和 200 字符段长等阈值明显按中文设计；退化检查虽含少量英文模式，不等于英文文学适配完成。[计数核心](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/scripts/wordcount_core.py#L12-L87)、[风格检测器](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/scripts/check-ai-patterns.js#L50-L86)

当前每章 800–900 English words 应继续使用明确的英文词数协议。英文省略、破折号、短句和自然对白需要文风判断，不应整体套用中文标点清洗器。

### 4.3 原文召回进入新作生产

外部拆文下游有原文与对标章节读取路径。它同时提倡使用自身素材，但这种接线会增加新作对原作措辞、人物功能与独特场景顺序的依赖。风险来自输入结构，并不代表对任何具体产物作抄袭判断。[章节材料加载](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-write/references/workflow-chapter.md#L15-L34)、[拆文下游合同](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-short-analyze/SKILL.md#L88-L126)

当前仓库的 `skills/novel-brainstorm-workshop/SKILL.md:73-80,335` 已要求原创隔离。适合保留抽象情绪机制、信息安排、因果和节奏分析；原文、可识别角色、独特事件组合留在分析区，不进入新小说运行时 Prompt。

### 4.4 整体部署 hooks / agents / 第二套追踪

外部 setup 并非粗暴覆盖：它有合并、幂等及用户配置保留规则。但它仍会安装自己的 agent、项目路由和 hooks，部分路径由它托管更新。这会引入另一套执行规则与目录假设。[setup 管理边界](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-setup/SKILL.md#L80-L127)

更重要的是，本地 `core/llm_client.py:358-363,432-460` 从 `agents/<role>/prompt.md` 获取提示，并将 Codex 作为 ephemeral/read-only/ignore-rules 的文本生成后端。**复制 Skill 到 ~/.codex/skills 不等于引擎运行时已接入。**API 模型也不会自动读这些文件。

### 4.5 市场方法可以借鉴，榜单结论不直接迁移

其扫榜有真实采集与数据质量流程，也明确单本排名不代表趋势。但默认样本和商业信号来自中文平台。[扫榜方法](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/skills/story-long-scan/SKILL.md#L9-L65)

对欧美读者可以借鉴“区分平台目标、记录样本与时间”的研究方法，不能把中文题材热度当作英文付费转化结论。当前 Workshop 也不是默认强制联网检索流程；市场研究应单独开启，不作为每章生成依赖。

## 5. 质量与成熟度：哪些有证据，哪些仍需实验

优点是仓库有较完整的跨平台测试定义、共享资源一致性检查、字数 / tracking / hooks 行为测试，而非只摆几篇演示文。还保留了失败实验与限制说明。[CI 定义](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/.github/workflows/cross-platform.yml)

其身体反应实验使用同一章节、同一模型、四组各三次生成；两次额外模型会话进行匿名样本评审。结果是有方向性的规则优化证据，但不是目标读者真人双盲试读，更不是欧美长篇付费留存实验。文风优先级实验也明确记录仍出现的视角增写和格式问题。[反套式反应实验](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/demo/craft-stock-reaction-eval/README.md)、[文风冲突实验](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/demo/style-precedence-eval/RESULTS.md)

**据此可以说：它认真做了写作过程工程，值得学习。当前证据不足以认定它的英文小说整体质量高于 Novel OS，也不足以承诺接入后销量或完读率提高。**同样，Novel OS 的大量工程测试通过也不代表读者一定喜欢生成的作品。

## 6. 建议的集成架构：方法适配，而非引擎套引擎

以下全部是拟议设计，尚未实现。

```text
经过审阅、固定版本的方法资料
    ↓ 适用性筛选：语言 / 受众 / 题材 / 篇幅 / 任务
Method Adapter（新增薄层）
    ├── Workshop：输出设计建议 → 用户确认
    ├── Architect / Scribe：输出有预算的指导片段
    └── Editor / Style / Guardian：输出带正文证据的评审建议
    ↓
现有合同、候选 revision 与 canon proposal
    ↓
现有连续性 / 商业 / 免费窗 / 长篇 / 终局门禁
    ↓
现有 Promotion → Final → EPUB → H5 ZIP
```

### 6.1 输入输出约定

方法输入应由引擎提供：

- 书 / 章 / 卷身份、已确认受众语言与范围；
- 当前合同及版本、已提交事实、当前候选正文 revision 和 SHA；
- 当前 POV 可见信息、应兑现 / 应保留的信息边界；
- 方法 ID、来源 commit、文件 SHA、适配版本与预算。

方法输出只允许建议、候选或 critique。评审建议至少包含 `finding_code`、`severity`、正文 revision/SHA、准确原文与位置、叙事问题及建议修复；位置由引擎核验。方法自报“通过”不直接获得 Final 权限。

**单一权威：**外部 `_tracking-state.json`、作者 profile 等不作为并行可写事实中心。需要的字段映射到现有合同或由 canon 派生。完整原作资料不进入模型生产输入。方法文档也不授予模型额外文件写权限。

### 6.2 错误分层，不复制另一套高失败率

- 技术 / 数据错误：来源 SHA 不符、坏 JSON、正文为空、章节错位、缺必要合同，按现有约束阻断。
- 明确叙事破坏：在原有已批准门禁中处理，例如 POV 提前知道秘密、重要承诺未兑现。
- 主观文风问题：先 advisory，要求具体证据，不用固定“禁词清零”触发全章反复重写。
- provider 401、容量、超时属于调用问题，与技法正确性分开；导入此仓库不解决凭据问题。

### 6.3 版本、恢复与回退

建议开关为 `off / advisory / enforced`，是未来配置名称示意，不是现有可执行命令。

1. 第一阶段 `advisory`，只生成报告，原稿与产物不变。
2. 在独立候选 revision 上试用一次有限修订，核对读感和合同保持。
3. 通过对照后，才允许少量证据充分、误报低的规则成为阻断项。
4. 方法版本和装配结果 hash 进入相应阶段依赖；新版本不静默重写既有 Final。
5. 关闭开关只停止新方法调用；修订撤回走既有 revision / promotion 路径，而不是直接覆盖正文文件或伪造旧 receipt。
6. EPUB、H5 字段及书章身份保持兼容；内部审稿材料不混入面向读者的正文或公开交付包。

## 7. 推荐实施顺序及验收

### 第一阶段：英文正文质量小试点

只选两项：**功能性去 AI 味 + 免费窗口主角选择 / 回报证据**。复用 Editor / Style / Commercial Guardian，优先改规则的具体性与评审证据，不增加一串常驻 agent。

对相同已批准设计生成 A/B 候选，使用相同模型、推理强度和章节预算；多个独立重复，不只挑最佳一次。第一批覆盖 1–4 章，并由熟悉目标市场的英文编辑或目标读者匿名评审：人物自然度、对白差异、因果清晰、局部满足和继续阅读意愿。

### 第二阶段：多卷上下文与知识边界

测试卷界 12→13 或相应已批准卷界、早期伏笔的后期回收、角色久别重返、长章中段关键事实、读者已知但 POV 未知。补上下文覆盖报告，明确哪些事实被省略及原因。

### 第三阶段：方法库与偏好治理

将验证过的方法按任务组织，维护来源 / 版本 / 适用语言。作者偏好与单书事实分离；市场扫描为可选工作流。第一批不迁封面、H5、运行调度和双状态存储。

### 所有阶段的共同验收

- 原设计的必要事件、秘密揭示时点、POV 和结尾义务保持；减少套话不以漏剧情为代价。
- 英文词数正确；Introduction 不占正文编号或免费名额。
- 不变更既有 book_id / chapter_id / volume_id；仅换封面和分类更新仍遵守独立摘要合同。
- 候选在明确晋升前不污染正式状态，重试不会重复推进。
- 引文与实际审稿版本一致；完整性通过和语义通过分别展示。
- 记录调用次数、耗时、成本、自动修订次数与人工推翻建议的比例。
- 在试点前确定判定标准；读感收益未稳定出现，保持旧路径，不把更多门禁当作进步本身。

## 8. 许可与最终建议

仓库采用 MIT；其文本允许修改和分发，并要求保留版权及许可声明。若复制实现或较大段方法文本，保存上游提交与 notices，避免失去来源。仓库许可证也不应被当作其中所有第三方小说、图片或采集材料的统一授权。[LICENSE](https://github.com/zenstory-ai/oh-story-claudecode/blob/aa3574fe6887fe1204168167b747d709607e77b6/LICENSE)

**最终建议：值得吸收，但不要整体替换。**

保留 Novel OS 的生产与交付体系，把 oh-story 的“如何写得更具体、如何判断一段文字是否有效”的方法，经英文适配后接入现有阶段。第一步就用免费 1–4 章检验效果，而不是先搬运全部 Skills 或另造第二套引擎。
