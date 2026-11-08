# 新旧样书质量对照：Saving Her Son’s Enemy / A Night of Her Own

2026-11-04。新任务 `a3f117e1cb71`，对照任务 `f249a13144d5`。

**结论：新作在核心冲突、关键人物动机和可见的外部回报上有提升；整体叙事更集中。但正文仍严重依赖规则与责任解释，狼人身体及共同体体验反而弱于旧作。可判为部分质量目标得到验证，不能判为 M1–M3 全部验收，更不能据此宣布所有作品都能稳定产出高质量正文。**

本次只读业务数据，保存评议与项目记忆；没有改写小说、应用代码、设置或任务，没有恢复创作、启动应用内 M3、重试出版文案或部署。

## 阅读范围与对照条件

主审完整通读旧作 12 章、新作 16 章的 final 正文；之后才核对骨架、已有评议、章节规划和运行证据。另外安排两名隔离上下文的独立模型审者，各自全文通读一本，未提供骨架、既有评分、预设问题或版本优劣指引。其意见由主审核对原文，不以多数票替代判断。模型模拟阅读不等于真人反馈、留存或付费数据。

- [旧作独立试读](/Users/max/OpenSource/Novel-OS/docs/reports/evidence/2026-11-04-novel-quality-comparison/blind-a.md)
- [新作独立试读](/Users/max/OpenSource/Novel-OS/docs/reports/evidence/2026-11-04-novel-quality-comparison/blind-b.md)
- [正文 SHA、章名、字数与运行快照](/Users/max/OpenSource/Novel-OS/docs/reports/evidence/2026-11-04-novel-quality-comparison/snapshot.json)

28 个 final 文件全部对应当前 canonical final head，晋级记录分别为 12 / 16 个 committed，检查结束时正文 SHA 未变。没有把 candidate、初稿或计划冒充成稿。

| 条件 | 上一本 | 本次新作 |
| --- | --- | --- |
| 作品 | A Night of Her Own | Saving Her Son’s Enemy |
| 主线 | Nora 在照护劳动、姐姐控制与女儿自主之间争取有限生活空间 | Mara 救下儿子的仇人，承担自己隐瞒事实的过错，并推动旧案各方分别担责 |
| 正文 | 12 章，约 12,785 英文词 | 16 章，约 16,452 英文词 |
| 平均 / 最短 / 最长章 | 1,065 / 880 / 1,254 词 | 1,028 / 929 / 1,113 词 |
| 确认目标 | 13,200 词 | 16,800 词；规划单章目标不超过 1,100，实际正文允许合理浮动 |
| 题材资料 | 狼人 v4、女性觉醒 v2 | 相同，冻结快照 SHA 完全一致 |
| 正文阶段记录模型 | Codex / gpt-5.6-sol | 相同 |
| 读者投入方法 | reader-investment.v1 | reader-investment.v2 |
| 请求投影 | 无新版投影标记 | creative-context.v2，区分全局设计、章规划与评议提示 |

字数使用同一词法，排除 Markdown 章节标题；与应用或空白分词计数略有差异。新作篇幅增加约 29%，单章平均反而略短，说明加章确实缓解了拉长单章的问题，但这不是文学质量提升比例。

两讨论保存的初始用户消息相同，当前基础输入除章数、`length_planning` 外相同；后续作者选择、人物、机制、故事路线与总篇幅不同。因此这是同源构想的作品对照，**不是严格控制变量的引擎 A/B 实验**。正文阶段模型相同也不能证明所有参数、随机采样和人工参与完全相同。

## 哪些提高，哪些没有

| 维度 | 对照结论 | 正文依据 |
| --- | --- | --- |
| 核心冲突与开篇入口 | 提升，尤其易于一句话抓住矛盾 | 旧作“救人后女儿被要求代偿”已有效；新作直接让母亲救活伤害儿子的人，再揭出她自己曾包庇，职业、亲情、自身过错在同一现场碰撞。 |
| 关键选择的个人动机 | 提升 | 旧作 Owen 停止共鸣的具体内因主要由 Nora 猜测；新作 Daniel 害怕承认过错后伤痛被视作应得，Jonah 承认自己脱身后为复仇返回，均由本人说出。 |
| 因果与代价持续性 | 新作更清楚，旧作本来已有基础 | 救命消除可复核残响，女主必须另找证据；拒签失去陪诊位置，承认造假带来停职；伤残与信任损失没有被结局撤回。新作未发现与旧作肩伤指代同等级、双边证据明确的因果错配，但不宣称零漏洞。 |
| 正义回报与关系修复 | 外部回报提高；亲情细节两本各有长处 | 新作救治指控撤销，Gareth 真正失去职权，母子得到有限亲近；旧作完整晚餐、杯声回扣和女儿的幽默更有生活温度。 |
| 中段推进与阅读效率 | 仍需修订，不能判明显提升 | 新作旧案推进较集中，但第 9、11、12、14 章多次重述证据范围和责任区分；信息在制度中推进，不总等于读者获得新的体验。 |
| 人物声音与潜台词 | 没有明显改善 | 两本都频繁出现人物讲清原则、旁白再解释一次；新作的医师、儿子、证人和主持者在关键场面共享接近的论证语言。 |
| 狼人题材的因果作用 | 仍然存在 | 新作变形消除残响，让救命与保全证据不能兼得；不能简单说只是改了一个标签。 |
| 狼人题材的实际体验 | 弱于旧作 | 旧作有狼形、共鸣、近身挡咬、返形牵拉伤腿的持续动作；新作第 3 章连变化后的具体形态都不清楚，之后主要是记录、质询和听证。 |
| 同题材多样性 | 有路线变化，尚未通过通用验收 | 新作从劳动负担转为旧案与母子矛盾，绝非只换人名；但两作都沿用“医师—被逐伤者—遗漏记录—权力施压—拒绝交易—有限亲情修复”的高层结构。同一原始提示本就限制比较，不能据此证明普遍同质化或普遍多样性。 |

不提供凭印象的综合分数或“提升百分比”。本轮没有实际付费分界，付费节点评价为不适用；没有外部作品比对，外部原创性检索为未审。

## 最值得保留的进步

### 1. 读者有更直接的情绪入口

新作第 1 章先写止血与生命危险，随后让 Mara 认出 Jonah，再让行动受限的 Daniel 当面要求交人。她救人不是无代价的善举，儿子的愤怒也有真实来源。女主自己隐瞒过事实，使读者不必等到结局才发现她并不完美。

[新作第 1 章：认出 Jonah](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_001_final.md:33)、[儿子的要求](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_001_final.md:137)。

不过，旧作开篇也完成了“压迫—自我看见—付出实际资源—女儿获自由”的小回报。新作前 3 章的救治选择跨章完成，不能只因生命危险更大就认定所有开篇技术都更好。

### 2. 复杂性开始来自人物想要什么

新作第 6 章 Jonah 说 **“Because I wanted Daniel hurt.”**，女主随即把器械、布和杯子摆齐，暂时无法再次触碰他。这里把复仇动机与女主的愤怒都变成了可感体验，而不是只宣布“双方都有责任”。随后她仍完成照护，才让伦理选择成立。

[动机与整理器械](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_006_final.md:81)。

第 8 章 Daniel 拒绝完整陈述，因为他害怕别人知道自己堵住过出口，就认定他活该残疾。这是明确的个人恐惧，比“他坚持沉默以增加调查难度”更可信。第 13 章他最终自己陈述，是要夺回对自身经历的解释权，而非简单报答母亲。

[Daniel 的恐惧](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_008_final.md:23)、[他选择陈述的理由](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_013_final.md:83)。

### 3. 胜利更容易被读者看见

第 14 章撤销救治指控、解除 Gareth 职权，同时让女主承担造假处分。第 15 章不是只说“恶人受到惩罚”，而是写他的钥匙打不开原来的门、排班不再经他批准。女主也真实经历六周停职、收入和病人流失。

[具体裁定](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_014_final.md:89)、[权力丧失成为行动事实](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_015_final.md:17)。

这提高了公平、认可和自主的回报感。不过它是带代价的伦理清算，不是持续反杀式爽文；作者选择的路线本就强调“诚实与修复，而非轻快胜利”，不能为了爽感擅自改成全员认错或主角无损获胜。

### 4. 结尾没有拿痊愈换团圆

Daniel 邀请母亲陪诊，但明确 **“As my mother. Not my physician.”**。终章他只请她补充一个事实，她说完就停；最后由儿子决定步速，母亲跟随。这里兑现了角色成长，又没有取消永久伤残或强迫宽恕。

[陪诊邀请](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_015_final.md:99)、[终章行为兑现](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_016_final.md:61)。旧作结尾的晚餐、探访与杯响同样有效；新作不是在所有情绪质地上都胜过旧作。

## 尚未解决的四个问题

### 1. “正确”仍经常写成反复解释

残响不是独立证据，首次清楚交代很重要。但第 3 章已经写明，第 9、11、12、14 章又反复完整说明。Ruth 只听见命令和信号、没看到最后冲突的限制也同样重现。伤残、疼痛、行走限制、康复需求常作为一整组被重复列举。

尤其第 12 章 **“Do not substitute my character for corroboration, favorable or unfavorable.”** 很准确，却更像原则总结。人物说完后，旁白往往还会再总结其意义。

[第 3 章首次建立](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_003_final.md:71)、[第 9 章复述](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_009_final.md:7)、[第 12 章论证语言](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_012_final.md:81)。

**读者收益的缺口**：程序身份发生变化，例如私人承认变成公开证词，但读者早已知道事实。如果仍花主要篇幅重复内容，公开承担的恐惧、抵抗和人际反应就会被挤小。应保留必要的事实边界，减少同义解释；不是让角色不再核实证据。

### 2. 最关键的狼人体验没有充分落到正文

新作不是完全没有题材机制：变形清除伤口残响是有效的核心两难。但第 3 章只写骨骼、组织和身体变化，没有清楚呈现转化完成后的狼形；第 6 章恢复期 Jonah 已穿着衬衣，回到人形的衔接也不明确。后文社会生活几乎只有诊所、记录室与听证室。

辅助检索发现 16 章 final 没有出现 `wolf`、`wolves`、`werewolf`，但**词频不是题材评分标准**。决定判断的是：读者实际看不到鲜明的狼人身体、感官、共同体生活如何参与大部分场景。旧作第 7–9 章在这方面更具体。

[新作变化现场](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/manuscript/chapter_003_final.md:19)、[旧作狼形挡咬](/Users/max/OpenSource/Novel-OS/docker-data/projects/a-night-of-her-own-b3f80487/outputs/manuscript/chapter_008_final.md:37)、[旧作返形代价](/Users/max/OpenSource/Novel-OS/docker-data/projects/a-night-of-her-own-b3f80487/outputs/manuscript/chapter_009_final.md:87)。

**“题材影响因果”与“读者体验到题材”是两个验收项，新作前者成立，后者不足。** 不应靠增加 Alpha、月亮或命定伴侣名词解决，更不能强塞作者未选择的爱情线。

### 3. 动机更清楚，但声音和生活仍不够丰富

Daniel 的痛苦、Mara 的羞惭、Ruth 的职业顾虑各自不同，却常用同一套完整的证据与责任论述表达。Jonah 和 Gareth 更多承担案件功能；前者为何对 Daniel 有如此具体的报复欲，后者的权力欲怎样嵌入日常关系，都有加深空间。

新作第 6 章摆器械、第 10 章移动水杯、第 16 章等儿子说完是很好的反例：这些动作已经能讲故事，不需要每次再把主题翻译出来。旧作 Ruth 垫杯布时同时提出控制条件，Leah 说道路是双向的，显示旧作在亲情复杂性和生活幽默上仍有优势。

### 4. 章节较短，但有多次把同一问答切到下一章

第 1 章末要求交人，第 2 章以“No”开头；第 7 章末问是否活该受伤，第 8 章才答；第 10 章末询问母亲是否承认伤害，第 11 章才答；第 11、12、13 章连续把发问留给下一章回答。

这些章节并非全部没有自身变化，也没有证据认定为付费欺骗；问题是连续使用同一种切点，会让“更多短章”变成“谈话被切碎”。新作的篇幅设置成功控制了章长，却还需要按场景回报选自然断点，而非只在问号处结束。

## M1、M2、M3 实际验证到哪里

**M1：类型与作者选择确实进入设计，关键动机更具体，但类型体验与多样性未全面验收。** 两本题材快照相同且故事路线不同，说明不是只读取固定模板。另一方面，两本都有医师、逐出者、伤腿、遗漏记录与有限家庭修复；其中一部分来自相同构想，一部分可能来自系统偏好，当前样本无法区分。需要不同构想的留出样本，而不是靠换人名计数。

**M2：新版方法真实参与了生成，局部效果成立，去流程化未过关。** 新 run 冻结 reader-investment.v2 和 creative-context.v2；实际 Scribe 请求回执已保存相关上下文。关键动机、代价持续与结局回报有正文证据，但 v2 已明确要求减少重复规程，正文仍反复出现。这说明再添一句“不要重复、增强戏剧性”不够。

第 12 章规划同时要求完整公开自认、历史知识边界、每项来源、残响不可复核、儿子真实伤害等，再在 `preserve_facts` 重列。它们需要保持正确，却不都需要每章通过长对白再完整陈述。**这是值得继续处理的提示与任务划分问题；目前有上下游一致的症状证据，没有控制实验能证明其是唯一原因。**

[第 12 章实际规划](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/chapter_012_outline.md:8)。

**M3：本次没有发现已执行的应用内阅读评议，不能声称 M3 已提升本书。** 两本 `quality/methods` 只有政策与锁，没有阅读评议结果；`quality/long-form` 只有对短篇标记不适用的规划报告。新作 16 个 evaluation report 都为 deterministic / continuity-engine.v1，`semantic_dimensions` 为空；它们不是吸引力评分。真实模型的连续性和风格审核确实运行了，但不能等同于全文 M3。

第 12 章风格报告明确只完整读取第 10–11 章及第 1 章样本，更早正文没有逐句全读；第 14 章连续性报告也限定了证据范围。跨多章重复更容易在全文阅读中暴露。本轮两名独立模型试读属于本次人工发起的分析，不回填成应用内 M3 成果。

[风格审核读取范围](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/feedback/chapter_012_style_report.md:7)、[连续性审核范围](/Users/max/OpenSource/Novel-OS/docker-data/projects/saving-her-son-s-enemy-be38860f/outputs/feedback/chapter_014_continuity_report.md:12)。

**骨架减重：请求投影已有工程作用，作者可读骨架本身仍重。** 旧骨架 175,381 B，新骨架 178,904 B；foundation 分别 302,394 B / 337,003 B。新 Scribe 请求第 1 章约 165,722 B、第 12 章 426,605 B，相比旧书对应 182,950 B / 472,476 B 较小，但故事不同，不能据此精确归因投影收益。新书第 16 章仍达 538,301 B。减重、文学质量和真实延迟必须分别验收。

## 下一步系统优化的优先顺序

本轮提出后续建议，不在分析过程中启动改稿或模型任务。

1. **先让 M2 区分“必须保持的事实”与“当前场景必须让读者经历的变化”。** 历史规则保持可查；规划只指定本章新发生的选择、阻力和后果。编辑对相邻章节查重，已有规则只在发生变化或真实争议时重述。用第 9、11、12、14 章做回归样例，保留全部事实与处分，检查模拟试读是否仍清楚而更少想跳读；不设置机械删字比例。
2. **将题材验收拆成机制与体验。** 除“移除规则会否改变选择”外，核对代表场景里身体、空间、关系、社会生活的实际影响；允许不同书的表现方式不同，不能用关键词配额或固定套路强行装饰。
3. **运行并利用真实 M3，而不是把它存在于系统里当作完成。** 审查正文版本，先记录证据化问题再由作者决定是否改稿。重点检测重复功能、声音趋同、关键动机、题材体验和兑现；任何改稿要复核相关章与因果，不建立无限审核重试。
4. **在现有字数范围内先改阅读体验，再决定是否扩篇。** 保留第 6 章坦白后的动作、第 10 章实际照护、第 15–16 章损失与陪诊；把已知案情复述让位于角色抵抗、反应和生活。增加章数不能替代这一步。
5. **分开做作品验收和系统归因实验。** 此书可验证实际问题是否改善；引擎改进则应固定同一骨架、同一关键场景、模型与参数做前后对照，并再用不同题材留出样本验证。真人试读另行收集，不把模型评价换算为留存保证。

## 正文完成与交付状态

5174 实际 API 与磁盘状态一致：新作 16 章已 committed，结局检查报告 pass，运行当前为 `paused / publication.copy`，最近更新 **2026-11-04 17:00:34（北京时间）**，`can_resume=true`、`active_job_id=null`。错误为：

```text
publication copy remained invalid after two repair attempts: not_chapter_one_copy
```

这发生在出版文案环节，不是第 16 章没有写完，也不是本次再次遇到 themes / 第 6 章状态解析错误。本轮没有深入诊断或重试该问题，不能宣称完整出版包已经生成成功。

旧作仍保持先前 `paused / ending.review` 的历史状态；其 12 章完整正文仍可用于文学比较。工程终态不同不能替代上述正文判断。
