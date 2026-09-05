# 读者导语生成、交付与 H5 展示设计

日期：2026-08-31
状态：待用户书面审阅
涉及仓库：

- `/Users/max/OpenSource/Novel-OS`
- `/Users/max/workspace/goPrj/src/ai-novel-h5`

## 1. 摘要

本设计在 Novel OS 中增加一个正式的书级 `publication.copy` 阶段。它在整书检查
通过后，从所有已批准的 Final 章节头生成并验证两段读者文案：

- `hook_lead`：8-20 个英文单词，在一句话中压缩整本小说的核心冲突；
- `spoiler_free_blurb`：120-180 个英文单词，用具体压力、stakes 和未决选择展开同一个
  整书核心冲突，同时保持无剧透。

两段文案都必须让读者立即明白“谁正在失去什么、什么力量在阻止她、为什么现在必须
做出选择”，并产生继续阅读才能得到答案的 open loop。只描述第一章事件、只写类型情绪
或使用通用宣传语都不能通过质量门。

结构化文案独立于第一章正文保存。编译器在 Markdown、EPUB、PDF 和 DOCX 中把它
投影到第一章之前，但不修改第一章 Final artifact。交付阶段同时生成一份满足 H5
现有 `PublicationPackage V1` 合同的目录，H5 后端继续使用现有导入逻辑，不新增数据库
字段或公共 API 字段。

H5 书籍详情页使用现有 `hook_lead` 和 `description` 字段，在书籍元信息之后、阅读操作
与章节目录之前展示一段无卡片边框的引语式导语。正文默认折叠为桌面 6 行、移动端
5 行；只有真实发生视觉溢出时才显示展开箭头。

完整生命周期为：

```text
ending.review（启用时）
  -> book.check
  -> publication.copy
  -> compile
  -> delivery.package
```

## 2. 问题定义

当前 Prompt 已经可以包含 `story_lead_contract` 和一段批准文本，编译器也能被动解析
章节中的 `## STORY_LEAD:` 标记。但是生产运行中仍没有稳定的读者导语，原因不是单个
提示词遗漏，而是书级生命周期缺失：

1. Architect foundation schema 不持久化 `story_lead_contract`；
2. Scribe 读取 outline/context pack，不直接读取完整原始 Prompt；
3. `_compile_book()` 只读取标题、作者、类型和 Final 章节；
4. `CompiledBook` 没有结构化出版文案字段；
5. 交付包没有可供其他系统消费的 `hook_lead` 或 `spoiler_free_blurb`；
6. H5 虽已有所需字段，但目前把 `description` 放在后置的 `About this story` 区块中，
   并通过字符数猜测是否需要展开。

因此，继续要求 Scribe 把导语写进第一章会让出版文案与正文争夺所有权，也无法提供
来源哈希、质量验证、可恢复阶段和跨系统结构化契约。

## 3. 目标

1. 每次未来的完整小说运行都生成可直接面向读者的短钩子和无剧透导语。
2. 文案严格来源于通过 `book.check` 的 Final 章节，不引用可变 manuscript projection。
3. 两段文案都准确承载贯穿开篇、中段和后段的整书核心冲突，而不把开篇事件误当成
   全书冲突。
4. 每个事实性表述都能追踪到 Final 章节证据，不添加正文未支持的信息。
5. 导语不是第一章的一部分；第一章的 revision、SHA-256 和正文保持不变。
6. 四种出版格式都在第一章之前呈现导语，EPUB 同时写入 `dc:description`。
7. Novel OS 输出可被 H5 现有导入器逐字节验证的 `PublicationPackage V1`。
8. H5 在书籍元信息和章节区域之间，以适合长文阅读的方式展示导语。
9. 文案具有直接阅读吸引力：第一眼冲突清晰、情绪 stakes 具体、压力会升级，并留下
   必须阅读正文才能回答的问题。
10. 阶段失败、重试、来源变化和旧产物保留都具有明确行为。

## 4. 非目标

1. 不为历史小说或已完成 run 回填文案。
2. 不生成包含完整结局的 synopsis，也不增加 `book_synopsis` 字段或文件。
3. 不修改、重写或重新晋升任何章节正文。
4. 不从广告点击、转化、收入或 A/B 实验数据生成文案。
5. 不新增 H5 数据库列，不改变公共 catalog JSON 字段名。
6. 不让 H5 从 Markdown 标题或正文中反向解析导语。
7. 不在本期增加导语人工编辑器、审批 UI 或历史版本浏览器。
8. 不改变 H5 `PublicationPackage V1` 的既有 schema。

## 5. 核心决策

### 5.1 书级文案独立拥有内容

`publication.copy` 是读者导语的唯一生产所有者。章节 Final artifact 只拥有章节正文；
编译器只负责把结构化出版文案投影到不同格式；H5 只负责展示已经交付的字段。

现有 `## STORY_LEAD:` 解析继续作为手工编译和旧输入的兼容入口，但它不是新运行的
主要合同。当结构化 `publication.copy` 存在时，如果任一章节同时含有
`STORY_LEAD` 标记，compile 必须以“重复导语来源”阻塞，不能静默选择一个版本。

### 5.2 使用两个既有 H5 概念

不再用一个模糊的 `synopsis` 表示不同用途：

| 字段 | 用途 | 英文长度 | 是否允许结局剧透 |
|---|---|---:|---|
| `hook_lead` | 一句话压缩整书核心冲突并建立 open loop | 8-20 words | 否 |
| `spoiler_free_blurb` | 展开同一整书冲突、升级和 stakes | 120-180 words | 否 |

`hook_lead` 不能只是类型口号，例如 “A story about love and betrayal”。它必须同时包含
主角、主要对抗力量，以及 stakes 或未决选择。`spoiler_free_blurb` 必须说明主角想保护或
获得什么、什么持续力量阻碍她、压力如何跨越开篇继续升级，以及读者必须继续阅读才能
回答的问题。

### 5.3 整书核心冲突合同

Writer 开始前必须先得到一个通过 Guardian 验证的 `whole_book_core_conflict`：

```json
{
  "protagonist": "The primary reader-identification character",
  "goal": "What the protagonist must protect, obtain, or change",
  "opposition": "The person, relationship, institution, or internal bind that persists",
  "stakes": "The concrete personal cost of failure",
  "escalation": "How the same conflict becomes harder across the book",
  "unresolved_choice": "The decision or question the publication copy may leave open",
  "evidence": {
    "opening": [{"chapter": 1, "source_quote": "Exact quote"}],
    "middle": [{"chapter": 6, "source_quote": "Exact quote"}],
    "late": [{"chapter": 10, "source_quote": "Exact quote"}]
  }
}
```

核心冲突不是 inciting incident 的改写。它必须在开篇 25%、中间 50% 和后段 25% 中分别
有至少一条 exact source quote，证明同一个人物目标、对抗力量或 stakes 持续存在并升级。
四章小说按第 1、2-3、4 章覆盖；更多章节按章号比例分段。`late` 证据用于证明持续压力，
不能把最终解决方式写入可见文案。

任何一个分段缺证据、三个分段实际描述的是不同主线，或 conflict 只能解释第一章时，
`publication.copy` 必须阻塞。次要冲突可以增强具体性，但不能取代整书主要 narrative
engine。

### 5.4 直接阅读吸引力合同

“吸引用户去看”通过文本质量门实现，不使用或伪造广告转化数据。两段文案共同满足：

1. **第一眼清晰**：读者无需背景知识就能识别主角、主要对抗和损失风险；
2. **情绪代价具体**：至少一个 stakes 是可感知的人、关系、身份、家园、安全或选择，
   不能只写 “everything” 或 “her future”；
3. **压力在升级**：文案显示冲突会继续恶化，而不是只复述发现秘密的瞬间；
4. **主动性**：主角面对必须采取行动或做出选择的局面，不被写成纯粹等待者；
5. **Open loop**：结尾留下一个正文才能回答的问题，但不使用空泛点击诱导；
6. **类型承诺准确**：愤怒、暧昧、悬疑或温暖来自小说事实，不制造正文没有的卖点。

`A story of...`、`Everything changes...`、`Secrets threaten to unravel...`、
`Will love conquer all?` 等通用句式如果没有同句或相邻句中的故事专属事实，判为
`generic_reader_hook` 并阻塞。

### 5.5 复用现有 Agent 模型配置

不新增第六个可配置 Agent：

- Style Curator 所用 provider/model 负责生成文案；
- Continuity Guardian 所用 provider/model 负责独立的事实、剧透和故事承诺验证；
- 确定性 validator 负责 schema、长度、来源引用、重复和复制检查。

主生成模型记录在 `StageResult.provider/model`。生成与验证两个调用的完整 provider、model、
prompt version 和响应哈希都记录在 `publication-copy.json`，但不记录凭证或完整请求头。

## 6. 生命周期与所有权

| 阶段/模块 | 所有权 | 输入 | 输出 |
|---|---|---|---|
| `book.check` | 整书连续性 | 已晋升 Final heads、StoryState | 通过/阻塞结果 |
| `PublicationSourceBuilder` | 可信来源集 | ArtifactStore Final heads、promotion receipts | 有序 source set |
| `WholeBookConflictBuilder` | 整书核心冲突 | source set、跨章节 exact quotes | conflict contract |
| `PublicationCopyWriter` | 读者文案 | conflict contract、source set、标题、语言、类型 | 候选 JSON |
| `PublicationCopyValidator` | 质量与事实边界 | 候选、Final heads、ending contract | validation report |
| `PublicationCopyStore` | 书级文案持久化 | 已验证 artifact | 原子 JSON 文件 |
| `compile` | 多格式投影 | Final heads、publication copy、styles | book exports |
| `H5PackageProjector` | H5 V1 交付合同 | Final heads、promotion receipts、publication copy | H5 package root |
| `delivery.package` | 客户交付集合 | exports、H5 package、cover | manifest、ZIP |
| H5 Importer | 导入完整性 | PublicationPackage V1 | catalog book/chapters |
| H5 `DetailView` | 读者展示 | `hook_lead`、`description` | 可折叠导语 UI |

`PublicationCopyWriter` 和 `PublicationCopyValidator` 不能修改 StoryState、章节 artifact 或
promotion receipt。`H5PackageProjector` 不能自行改写章节换行或 Markdown。

## 7. 可信来源与输入绑定

### 7.1 来源选择

`publication.copy` 只在 `book.check` 通过后执行。`PublicationSourceBuilder` 按章节号读取：

1. `ArtifactStore.get_head(chapter, "final")`；
2. 对应 artifact 的原始 UTF-8 bytes；
3. 对应已接受的 chapter promotion receipt；
4. 章节号和最终章节标题。

可变的 `outputs/manuscript/chapter_NNN_final.md` 只可用于完整性修复后的投影检查，不能成为
文案生成来源。缺失 Final head、receipt 不匹配、空正文或 SHA 不匹配都会在调用模型前
阻塞该阶段。

### 7.2 Source set 指纹

有序来源列表使用固定字段和紧凑 UTF-8 JSON 序列化：

```json
[
  {
    "chapter": 1,
    "revision_id": "REVISION_ID",
    "sha256": "CHAPTER_SHA256"
  }
]
```

`source_set_sha256` 是上述完整数组 bytes 的 SHA-256。阶段 `input_hashes` 至少包含：

- `source_set_sha256`；
- `book.check` 的 stage-result SHA-256；
- title/language/genre 元数据的 canonical SHA-256；
- ending contract SHA-256（存在时）；
- publication prompt/policy version。

Final head、整书检查结果或文案策略任一变化，`publication.copy` 以及所有下游阶段都必须
失效并重跑。来源完全相同时，resume 复用已有 checkpoint，不再调用模型。

## 8. `publication-copy.json` 合同

唯一权威文件为：

```text
outputs/publication/publication-copy.json
```

Schema v1：

```json
{
  "schema_version": 1,
  "title": "The Empty Chair Beside Her",
  "language": "en-US",
  "reader_heading": "Before the Story",
  "hook_lead": "Pregnant and betrayed, Claire must choose between preserving her marriage and protecting her daughter.",
  "spoiler_free_blurb": "At thirty-six weeks pregnant, Claire Bennett is counting diapers, leave days, and every dollar in the nursery fund when two dinner charges expose the lie behind her husband's latest factory emergency. Ethan insists she is hormonal, then disappears during a medical scare and leaves an empty chair beside her when their daughter is born. Claire has spent years believing that keeping two parents under one roof is the safest thing she can give a child. But as joint savings vanish, relatives repeat Ethan's version of events, and he poses as a devoted father without doing the work, she begins keeping a different kind of record: bank statements, unanswered calls, feeding times, missed pickups, and every promise he breaks. The records can prove what Ethan has done. They cannot decide what Claire is willing to lose. Will she keep protecting the image of a family, or build the real stability her daughter needs?",
  "whole_book_core_conflict": {
    "protagonist": "Primary protagonist",
    "goal": "Concrete goal",
    "opposition": "Persistent opposition",
    "stakes": "Concrete cost of failure",
    "escalation": "Cross-book pressure escalation",
    "unresolved_choice": "Spoiler-free open question",
    "evidence": {
      "opening": [{"chapter": 1, "source_quote": "Exact source quote"}],
      "middle": [{"chapter": 6, "source_quote": "Exact source quote"}],
      "late": [{"chapter": 10, "source_quote": "Exact source quote"}]
    }
  },
  "source": {
    "run_id": "RUN_ID",
    "book_check_stage_sha256": "SHA256",
    "source_set_sha256": "SHA256",
    "chapters": [
      {
        "number": 1,
        "revision_id": "REVISION_ID",
        "sha256": "SHA256"
      }
    ]
  },
  "generation": {
    "conflict_provider": "PROVIDER",
    "conflict_model": "MODEL",
    "conflict_prompt_version": "whole-book-conflict.v1",
    "conflict_response_sha256": "SHA256",
    "writer_provider": "PROVIDER",
    "writer_model": "MODEL",
    "writer_prompt_version": "publication-copy-writer.v1",
    "writer_response_sha256": "SHA256",
    "validator_provider": "PROVIDER",
    "validator_model": "MODEL",
    "validator_prompt_version": "publication-copy-validator.v1",
    "validator_response_sha256": "SHA256",
    "generated_at": "RFC3339"
  },
  "validation": {
    "policy_version": "publication-copy-policy.v1",
    "status": "pass",
    "length": {
      "unit": "words",
      "hook_lead": 14,
      "spoiler_free_blurb": 152
    },
    "checks": {
      "whole_book_core_conflict": true,
      "hook_core_conflict": true,
      "blurb_core_conflict": true,
      "protagonist_stakes": true,
      "spoiler_free": true,
      "source_supported": true,
      "not_chapter_one_copy": true
    },
    "reader_pull": {
      "status": "pass",
      "checks": {
        "first_glance_clarity": true,
        "concrete_emotional_stakes": true,
        "escalating_pressure": true,
        "protagonist_agency": true,
        "open_loop": true,
        "truthful_genre_promise": true
      }
    },
    "claim_evidence": [
      {
        "claim": "A factual claim in the publication copy.",
        "chapter": 1,
        "source_quote": "An exact quote from the approved Final artifact."
      }
    ]
  }
}
```

所有字段必填；空列表只允许用于没有事实性陈述的 `claim_evidence`，但正常导语必须包含
具体冲突，因此生产产物实际上至少有一条证据。parser 拒绝未知顶层字段、重复 JSON key、
非 `pass` 状态和非 64 位小写十六进制 SHA-256。

`generated_at` 在同一个成功 stage 的后续 compile/package 重建中保持不变，保证权威
artifact 和常规交付副本可重复验证。

## 9. 生成与验证流程

### 9.1 Writer 输入与输出

Source set 未超过单次输入预算时，WholeBookConflictBuilder 先从完整的有序 Final 章节文本
提取第 5.3 节的 conflict contract；超过预算时从第 9.5 节定义的 evidence ledger 构建。
确定性代码验证 opening/middle/late 的全部 exact quotes 后，Writer 才接收 conflict contract、
书名、语言、类型和相同证据边界。正文或 ledger 必须放在带章节号和 revision id 的数据
边界内，不能把正文中的指令性文本当作系统指令。

Writer 只能返回：

```json
{
  "reader_heading": "Before the Story",
  "hook_lead": "...",
  "spoiler_free_blurb": "..."
}
```

不接受 Markdown fence、分析说明、候选数组或额外字段。解析失败进入受限 repair 调用，
repair 只修复 JSON 和已报告的质量项，不允许改变来源集。

Writer 必须遵守以下结构：

- hook：在 8-20 words 内压缩 `protagonist + opposition + stakes/unresolved choice`；
- blurb 前 35 words：出现主角和打破原有生活的具体冲突，不能先写背景或类型口号；
- blurb 中段：展示同一核心冲突至少两层不同压力和主角的主动应对；
- blurb 最后 1-2 句：落到具体 stakes 与 unresolved choice，建立 open loop；
- 不得公开 `late` evidence 中的解决方式、胜负、最终关系状态或 ending image。

### 9.2 长度策略

`LanguageLengthPolicy v1` 使用 artifact 的 `language` 选择确定性计数器：

- English/en locale：含内部 apostrophe 或 hyphen 的字母数字序列计为一个 word；
  `hook_lead` 8-20 words，`spoiler_free_blurb` 120-180 words。
- 其他以空格分词的语言：使用 Unicode 字母/数字 token；范围同上。
- 中文、日文、韩文：排除空白和 Unicode 标点后计 content characters；
  `hook_lead` 16-36 characters，`spoiler_free_blurb` 240-360 characters。

Artifact 明确记录实际单位和计数。上下限均为包含关系；不能通过把标点或 Markdown 当作
单词来通过检查。

### 9.3 确定性质量门

模型验证前先执行以下检查：

1. JSON schema、字符串 trim、单句 hook、长度范围；
2. 不含 Markdown heading、HTML、URL、促销 CTA 或书名外的伪元数据；
3. 不含 ending contract 明确列出的 spoiler phrase；
4. `claim_evidence.source_quote` 必须是对应 Final chapter 的精确子串；
5. 规范化第一章前 500 words，与 hook/blurb 比较连续 8-word windows；任何共同 window
   都视为复制正文并阻塞；
6. hook 与 blurb 之间不能有连续 10-word window 的重复；
7. hook 必须是单句，且不能只由书名、类型词和已识别的通用宣传语组成；
8. reader heading 只能是一行纯文本，不能冒充章节标题。

### 9.4 Continuity Guardian 语义质量门

Source set 未超过单次输入预算时，Guardian 独立读取候选文案、ending contract 和所有
Final heads；超过预算时按同一章节分组逐项验证候选 claim，再合并为严格 JSON：

- hook 和 blurb 是否都准确表达同一个 `whole_book_core_conflict`；
- 核心冲突是否贯穿 opening/middle/late，而非只复述第一章；
- 主角目标、持续 opposition 和 stakes 是否具体且对读者可理解；
- 英文 blurb 前 35 words 是否已经建立 protagonist、opposition/stakes 中至少两项，而非
  先写通用背景；
- 是否满足第一眼清晰、情绪代价、升级、主动性、open loop、类型承诺六项 reader-pull
  rubric；
- 是否泄露解决方式、最终关系状态、最终财产/身份结果或结尾意象；
- 文案承诺是否由小说兑现；
- 每个事实性 claim 的章节号和 exact supporting quote。

确定性代码再次验证所有 quote bytes。任何 semantic check 失败都把结构化反馈交给 Writer
做最多两次定向修复。两次后仍失败则 `publication.copy` 阻塞，compile 不执行。

### 9.5 输入过大

`PublicationSourceBuilder v1` 按最多 120,000 个 Unicode code points 构造一个连续章节组，
并为系统指令、输出和验证保留独立预算。单章超过上限时只允许在段落边界继续切分，且
每一段仍携带同一个 chapter number 和 revision id。完整 Final source set 超过一个组时，
逐组生成带 exact quotes 的证据 ledger，再由 Writer 只读取合并 ledger；Guardian 按相同
分组逐项验证 claim。

分组不能丢弃章节、只采样开头或只信任 StoryState 摘要。每个 ledger quote 必须通过
原文子串检查，合并后的 evidence ledger 与全部 source chapter SHA 一起进入 source set
绑定。这样长篇小说与短篇小说共享同一质量合同。

## 10. 编译投影

### 10.1 `CompiledBook`

`CompiledBook` 增加结构化 `publication_copy`，并生成三个 book-level blocks：

1. `story_lead_title`：`reader_heading`；
2. `story_hook`：`hook_lead`；
3. `story_lead`：`spoiler_free_blurb`。

这些 blocks 的 `chapter` 为 `None`，位于第一个 `chapter_title` 之前。章节列表、目录锚点和
Final chapter bytes 不包含这些 blocks。`word_count` 继续表示正文篇幅，排除三类出版文案
block。

`story_hook` 增加独立 style role，用略强于正文、弱于章节标题的排版表达一句话钩子。
它不使用超大字号，也不把导语做成营销卡片。

### 10.2 格式行为

- Markdown：书名/作者之后输出 reader heading、斜体 hook 和 blurb，再输出第一章标题。
- EPUB：导语位于第一章页面内容之前但不进入目录；`dc:description` 精确使用
  `spoiler_free_blurb`；`dc:language` 使用 artifact language。
- PDF：标题页信息之后、第一章之前排版导语，允许自然分页但不能覆盖第一章标题。
- DOCX：使用对应 paragraph styles，顺序与 Markdown 相同。
- HTML preview：与上述 block 顺序一致，供 Studio 预览。

所有 renderer 消费同一个 `CompiledBook`，不能各自重新读取 JSON 或章节文件。

## 11. H5 `PublicationPackage V1` 投影

### 11.1 输出根目录

`delivery.package` 原子生成：

```text
outputs/deliverables/h5-publication/
  pkg-RUN_ID-SNAPSHOT_SHA12/
    meta/publication_package.json
    meta/finalization.json
    meta/delivery.json
    chapters/01.md
    chapters/02.md
    ...
    正文.md
```

`package_id` 精确为 `pkg-{run_id}-{snapshot_sha256前12位}`，其中 snapshot 是第 11.4 节
绑定 package 与 finalization raw bytes 的哈希。任何标题、文案、章节、receipt 或时间字段
变化都会得到新的目录名。该不可变子目录本身就是 H5 filesystem adapter 或 R2 prefix 的
source root，不需要 H5 解压 Novel OS 的 `book-package.zip`。Novel OS 现有 ZIP 仍会包含
当前 package_id 的目录，供人工下载与传输。

写入时先在同级临时目录完成全部文件、校验和 self-validation，再把临时目录原子 rename
为从未存在的 package_id。目标已存在时只接受逐文件 byte-identical 的幂等重放；任何
差异都阻塞，不能覆盖。失败时保留既有不可变目录，不留下可导入的半成品。

### 11.2 `publication_package.json`

严格输出 H5 已有字段，不增加 Novel OS 私有字段：

```json
{
  "version": 1,
  "platform": "novel-os",
  "primary_title": "Exact title",
  "alternate_titles": [],
  "hook_lead": "8-20 word hook",
  "spoiler_free_blurb": "120-180 word blurb",
  "tags": [],
  "chapters": [
    {
      "number": 1,
      "title": "Exact chapter title",
      "path": "chapters/01.md",
      "rune_count": 1234,
      "body_sha256": "SHA256_OF_EXACT_BYTES"
    }
  ],
  "total_runes": 1234,
  "counting_policy": "utf8-runes",
  "final_checks": {
    "body_hashes": true,
    "chapter_order": true,
    "publication_copy": true,
    "whole_book_continuity": true
  },
  "finalization_source": "novel-os RUN_ID book.check"
}
```

`alternate_titles` 和 `tags` 只使用已有、已验证的 StoryState 元数据；缺失时输出空数组，
不能由模型临时编造。章节 Markdown 使用 ArtifactStore Final head 的 exact UTF-8 bytes，
不规范化换行、不补标题、不移除状态块。Projector 同时执行 H5 已有的章节号连续、标题
非空且不重复、path 精确和 tag 合同检查；不合格时阻塞而不是悄悄改名。

H5 V1 要求至少四章。少于四章的 Novel OS 项目仍生成出版文案和常规书籍格式，但
`delivery.package` 明确记录 `h5_publication: inapplicable_less_than_four_chapters`，不生成
伪造章节来满足导入器。

### 11.3 `finalization.json`

```json
{
  "merged_manuscript_sha256": "SHA256_OF_正文.md",
  "finalized_at": "BOOK_CHECK_FINISHED_AT",
  "receipts": [
    {
      "chapter_number": 1,
      "body_sha256": "FINAL_HEAD_SHA256",
      "verdict": "accepted"
    }
  ]
}
```

Projector 必须先验证每个 Novel OS promotion receipt 指向同一个 Final revision 和 SHA，
再投影 H5 所需的 `accepted` receipt。`finalized_at` 使用已经持久化的
`book.check.finished_at`，重建时不取当前时钟。

`正文.md` 按 H5 的既有算法生成：保持每章 raw bytes，在相邻章节之间插入两个 LF，
最后再追加一个 LF。它的 SHA 必须与 `merged_manuscript_sha256` 相同。

### 11.4 `delivery.json`

`publication_package.json` 和 `finalization.json` 使用确定性 UTF-8 JSON 编码并保留最终
raw bytes：

```text
snapshot_sha256 = SHA256(publication_package_raw || finalization_raw)
```

中间没有分隔符。`delivered_at` 使用已经持久化的 `compile.finished_at`，从而让同一
compile checkpoint 的 delivery 重试保持 byte-identical。Projector 完成后使用与 H5
domain 相同的规则做一次 self-validation，才发布目标目录。

### 11.5 现有 Novel OS 交付 manifest

现有 `package-manifest.json` 保持 schema v1，但为新文件使用明确 role：

- `outputs/deliverables/meta/publication-copy.json`：`publication_copy`，其 bytes 与
  `outputs/publication/publication-copy.json` 完全一致；
- 当前 `h5-publication/{package_id}/**`：`h5_publication_object`；
- 常规 `book.*`：`book_export`。

`book-package.zip` 继续使用稳定排序和固定 ZIP timestamp。`publication-copy.json` 的交付
副本必须与权威 artifact SHA 相同。若磁盘上保留其他 package_id，package builder 只纳入
本次 `delivery.package` 明确传入的当前根，不能把 stale package 混入 ZIP。

## 12. Pipeline、恢复与失败行为

### 12.1 新阶段顺序

`pipeline_runner` 在 `book.check` 后增加：

```text
publication.copy
  artifact: outputs/publication/publication-copy.json

compile
  artifacts: outputs/deliverables/book.{md,epub,pdf,docx}

delivery.package
  artifacts:
    outputs/deliverables/package-manifest.json
    outputs/deliverables/book-package.zip
    outputs/deliverables/h5-publication/{package_id}/meta/publication_package.json（适用时）
```

`_compile_book()` 不再内部调用 `build_delivery_package()`。Run 只有在
`delivery.package` 完成后才标记 `completed`。

### 12.2 失败分类

- provider timeout、rate limit、5xx：按现有 stage retry/backoff 处理；
- JSON/长度/语义失败：先执行最多两次定向文案修复；仍失败则 stage `blocked`；
- Final head、promotion receipt 或 source quote 不匹配：立即 `blocked`；
- renderer 或 filesystem 暂时错误：对应 compile/delivery stage `failed` 或 `retryable`；
- H5 self-validation 失败：`delivery.package` 阻塞，不发布新 H5 目录或 ZIP。

不存在“空导语继续编译”的降级路径。`book.check` 已完成时，可直接重试
`publication.copy`，不重写章节。compile 或 delivery 重试复用同一个已验证文案 artifact，
不重复付费生成。

### 12.3 Stale 与完整性恢复

Final head SHA 变化会使 source set 改变，旧 publication copy 只能保留为诊断证据，不能
继续交付。Pipeline 从 `book.check` 重新验证，并重跑 `publication.copy`、compile 和
delivery。

如果仅可变 Final projection 丢失，而 ArtifactStore head 和 promotion receipt 均未变化，
现有完整性恢复先重建 projection；source set 不变，因此允许复用 publication copy，随后
重建 compile 和 delivery 产物。

失败模型响应保存到 run-scoped feedback 路径并记录 SHA，但不会复制到 deliverables。
日志只显示 provider、model、attempt、finding code 和文件路径，不输出密钥或完整正文。

## 13. H5 数据流

现有字段映射保持不变：

```text
publication_package.spoiler_free_blurb
  -> books.intro
  -> public catalog description
  -> FixtureBook.description

publication_package.hook_lead
  -> books.hook_lead
  -> public catalog hook_lead
  -> FixtureBook.tagline
```

H5 后端工作只增加一个 Novel OS `platform: "novel-os"` 的 golden/import fixture，证明
现有 importer 接受该投影。公共 API、数据库 migration 和 catalog domain 不变。

## 14. H5 详情页设计

### 14.1 信息顺序

`DetailView` 调整为：

```text
返回导航
封面 + 类型 + 书名 + 作者 + 阅读数据 + 收藏
读者导语（hook + spoiler-free blurb）
阅读/解锁操作
Chapters / Contents
```

移除后置的 `About this story` 副本，页面中 `book.description` 只出现一次。目录、锁定状态、
阅读进度和主要操作逻辑不变。

### 14.2 视觉表达

导语是页面中的无框文本区域，不做浮动 card，也不嵌套进 hero card：

- 左侧使用 Lucide `Quote` 作为低对比语义标记；
- `hook_lead` 使用 display/serif 字体和中等字号，作为导语首句；
- blurb 使用阅读正文色、稳定行高和不超过约 66ch 的行宽；
- 顶部分隔线沿用现有 detail page 的 `var(--line)`；
- day/night mode 只使用现有 token，不增加独立单色主题或渐变装饰；
- 不做展开高度动画，避免布局抖动和 reduced-motion 分支。

桌面初始显示 blurb 6 行，`max-width: 767px` 显示 5 行。hook 不参与行数裁剪。长单词
使用 `overflow-wrap: anywhere`，320px 宽度不能出现横向滚动。

### 14.3 真实溢出检测

不再使用 `book.description.length > 180`。组件用 blurb element ref 比较：

```text
overflowing = scrollHeight > clientHeight + 1
```

首次 layout 在折叠状态使用 `scrollHeight > clientHeight + 1`。组件同时缓存计算后的折叠
高度；展开后改用 `scrollHeight > collapsedHeight + 1`，避免内容展开后 `clientHeight`
等于完整高度而错误隐藏按钮。测量发生在 book id/description 变化、字体加载完成和容器
resize 后。`ResizeObserver` 负责宽度变化；测试环境提供可控 mock。折叠状态在切换书籍
时重置。

只有 `overflowing` 为 true 时才显示 44x44 的 chevron icon button：

- 折叠：Lucide `ChevronDown`，`aria-label="Expand story introduction"`；
- 展开：Lucide `ChevronUp`，`aria-label="Collapse story introduction"`；
- button 设置 `aria-expanded` 和指向 blurb id 的 `aria-controls`；
- 原生 button 提供键盘 Enter/Space 行为和 focus-visible；
- icon 设置 `aria-hidden="true"`，hover title 与 aria-label 一致。

description 为空时仅显示 hook，不显示空段落和按钮；hook 也为空时整个导语区域隐藏，
以兼容旧的非 PublicationPackage fixture。新 H5 package 的两个字段仍然都是必填非空。

## 15. 测试策略

### 15.1 Novel OS 单元测试

1. PublicationCopy schema round-trip、unknown/duplicate key、SHA 和 trim 检查；
2. English/CJK 边界计数，包括 8/20、120/180 和 apostrophe/hyphen；
3. 第一章 8-word window 复制、hook/blurb 重复和单句 hook 检查；
4. claim quote 必须匹配指定 Final artifact，不允许匹配其他章节或 mutable projection；
5. opening/middle/late 核心冲突证据完整，单一开篇事件或三条不相干主线必须失败；
6. hook 缺 protagonist/opposition/stakes，或 blurb 前 35 words 没有核心冲突时失败；
7. `generic_reader_hook`、无升级、无主角主动性、无 open loop 的结构化 finding；
8. spoiler、unsupported fact、无具体 stakes 的结构化 finding；
9. 超长小说 evidence ledger 覆盖所有章节且 quote 可回溯；
10. 相同 source set 产生相同输入指纹并复用 checkpoint。

### 15.2 Pipeline 与编译测试

1. 顺序严格为 `book.check -> publication.copy -> compile -> delivery.package`；
2. publication failure 不生成或更新 book exports；
3. compile 从 ArtifactStore Final heads 读取，忽略伪造 projection；
4. 第一章 artifact SHA 在生成导语前后完全相同；
5. structured copy 与章节 `STORY_LEAD` 同时存在时阻塞；
6. Markdown/HTML/PDF/DOCX/EPUB 的导语都在第一章标题之前；
7. EPUB `dc:description` 和 language 正确，TOC 不增加虚假章节；
8. compile retry 不触发新的 Writer 调用；
9. 完整 run 的 completed phase 为 `delivery.package`。

### 15.3 H5 package 合同测试

1. 每章文件 bytes、rune count、SHA、顺序和总数完全一致；
2. `正文.md` 使用 H5 的确定性合并算法；
3. finalization 每章只有一个 accepted receipt；
4. delivery hash 使用两份 JSON raw bytes 无分隔拼接；
5. Projector 中途失败保留旧完整目录；
6. Novel OS 输出 fixture 通过 H5 现有 filesystem source 和 Importer 测试；
7. H5 package 少于四章时明确不适用，常规导出仍成功；
8. 现有 `package-manifest.json` 和 ZIP 的排序、role、hash 保持确定性。

### 15.4 H5 前端测试

1. hero、导语、阅读操作、chapters 的 DOM 顺序；
2. hook 与 description 各只渲染一次，不再出现 `About this story`；
3. 无溢出时不显示 chevron；有溢出时按钮出现；
4. click、Enter、Space、`aria-expanded`、`aria-controls` 和 label 正确；
5. book 切换和 resize 后重新测量并重置折叠状态；
6. 空 description、空 hook 和超长单词行为；
7. CSS contract 覆盖 6/5 行、44px 触控面、无 viewport font scaling；
8. Playwright 在 320px、390px、768px、1440px 检查 day/night 两种主题，无重叠、
   无横向滚动，展开前后章节区域自然下移。

### 15.5 回归命令

实施计划会使用仓库已有命令，至少覆盖：

```text
Novel OS: focused pytest -> full pytest
H5 API: focused Go tests -> affected package tests
H5 storefront: focused Vitest -> typecheck -> lint -> full storefront tests
H5 visual: Playwright desktop/mobile day/night screenshots
```

## 16. 实施顺序

1. Novel OS：先用 RED tests 固定 schema、来源绑定、验证器和生命周期。
2. Novel OS：实现 publication copy 生成/验证与 checkpoint。
3. Novel OS：把结构化导语接入 CompiledBook 和所有 renderer。
4. Novel OS：拆分 `delivery.package`，生成并 self-validate H5 V1 projection。
5. H5 API：加入 Novel OS golden fixture，证明现有导入合同无需修改。
6. H5 storefront：移动并重构导语组件，加入真实溢出检测和无障碍行为。
7. 两仓完整回归与跨仓 fixture 验证。

## 17. 验收标准

1. 新的 4 章以上小说 run 完成后必有通过验证的 `publication-copy.json`。
2. `hook_lead` 为 8-20 个英文单词，并在一句话中包含主角、持续 opposition，以及 stakes
   或未决选择。
3. 120-180 词 blurb 在前 35 words 内建立同一个整书核心冲突，中段展示压力升级，末尾
   建立具体 open loop。
4. 核心冲突具有 opening/middle/late 三段 Final evidence；两段文案均通过 reader-pull
   六项质量门，不泄露结局，所有事实 claim 有 Final evidence。
5. Markdown、EPUB、PDF、DOCX 均在第一章前显示导语，第一章 SHA 未变化。
6. EPUB metadata 包含与 artifact 完全一致的 `dc:description`。
7. `h5-publication/` 可被 H5 `PublicationPackage V1` importer 原样接受。
8. H5 详情页在元信息和 chapters 之间只显示一份导语。
9. 短 blurb 没有展开按钮；长 blurb 默认 5-6 行并可通过鼠标和键盘展开。
10. 320px 到 1440px、day/night 模式下无文字遮挡、非预期布局跳变或横向溢出。
11. publication copy 失败时 run 停在 `publication.copy`，不发布无导语的新交付包。
12. 不存在历史小说回填、完整 synopsis 或广告转化数据处理代码。
