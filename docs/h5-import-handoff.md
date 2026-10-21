# H5 ZIP 导入交接：EPUB 正文、分类、分卷与独立更新

本文取代早期对接说明中关于正文优先级、`serialization_id` 唯一索引、仅凭 MD snapshot 去重的建议。按当前引擎实现编写，接入方为 H5 开发团队。

## 1. 已锁定的产品行为

- 正文只从 `book.epub` 解析。`book.md`、`正文.md`、`h5-publication/*/chapters/*.md` 均忽略；JSON 提供身份、分类、章节定位、卷和系列关联。
- Introduction 展示为导读；不算正文第 1 章、不占免费章数、不扣币。
- 一本 80 章小说分 4 卷是 `multi_volume`，目录标签为 Volume；章节在全书内始终为 1—80。
- 同书换卷不刷新免费试读，按全书章号扣币，已解锁章节、阅读进度、收藏绑定原书原章。
- `series_installment` 是跨书关联；每本分册保持独立 `book_id`。系列总章号不参与本书扣币。
- 封面、分类、卷标题更新不重建书籍或章节，不重置任何已解锁权益。
- 作者名是书籍展示元数据。作者名变化只更新同一 `book_id` 的作者字段，不改变书籍、章节、正文或权益身份。

## 2. 包结构和版本

```text
book-package.zip
├── book.epub
├── book.md                         # H5 不读取其正文
├── meta/
│   ├── h5-import.json              # 新的 EPUB 导入合同
│   ├── novel-classification.json   # 每本书的完整分类
│   ├── novel-serialization.json    # 卷、系列及策划信息
│   └── publication-copy.json       # 发布文案；有时存在
├── covers/
│   ├── cover-set.json              # 封面方案、候选、选择关系
│   ├── pending/cover-01.png ...
│   └── selected-cover.png         # 扩展名以 manifest 为准
├── h5-publication/pkg-*/...        # 旧 MD 证据投影；可有可无
└── package-manifest.json
```

不同对象的版本独立演进：

| 对象 | 本次版本 | 用途 |
|---|---|---|
| `package-manifest.json.schema_version` | `4` | 文件索引、书籍身份、整个包的版本 |
| `meta/h5-import.json.schema_version` | `novel-h5-import.v1` | EPUB 正文入口和章节映射 |
| EPUB `novel-os:chapter-map.schema_version` | `novel-epub-map.v1` | 编译器写入的正文/非正文显式映射 |
| 分类 `schema_version` | `novel-classification.v1` | 分类字段 |
| 分类 `catalog_version` | `novel-types.2026-09` | 允许的标准 ID 字典 |
| 序列化 `schema_version` | `novel-serialization.v1` | 出版结构包装 |
| `serialization.format.schema_version` | `narrative-format.v1` | 用户确认的篇幅与分卷 |
| 旧 `publication_package.json.version` | `3` | 原有 MD 投影，继续保留 |

先读取 manifest。新合同路径使用 `manifest.h5_import.path`，或唯一的 `files[].role == "h5_import"`。分类和分卷同样按 manifest 的路径/role 定位。路径指针和 role 查找都存在时应指向同一文件。文件名只是约定；EPUB 路径以 sidecar 的 `epub.path` 为准。

`files[]` 每项提供 `path / media_type / size / sha256 / role / cover_selection_state`。封面候选另有 `candidate_id / display_order`。`package_revision_sha256` 覆盖所有这些文件描述项，包括独立封面和导入清单；不包含 ZIP 自身或 manifest 自身，避免自引用。

### 作者字段（向后兼容的可选字段）

当前打包器在 `meta/h5-import.json.author` 和 `package-manifest.json.author` 同时写入相同字符串，sidecar 仍为 `novel-h5-import.v1`，manifest 仍为版本 4。字段对解析器保持可选，以兼容尚未包含作者字段的旧包；新包若两个字段都存在但值不同，应拒绝更新并报告合同冲突。

作者来源按以下顺序解析，判断时使用“字段是否存在”，不要用非空真假值代替：

1. sidecar 自有字段 `author`；
2. manifest 自有字段 `author`；
3. EPUB OPF 的 `dc:creator`；
4. 都不存在时保留数据库中已有作者，旧包不得把作者清空。

Novel OS 在新书创建/输入阶段没有人工作者名时，调用已配置的 LLM 为该书生成笔名，并把结果一次性持久化到 `outputs/state/story_state.json.metadata.author`；后续重试和导出复用同一个值。人工作者名优先。交付打包器只读取持久化值；它不调用模型、不生成名字，也不在失败时编造替代名。旧项目没有持久化作者时，打包器使用 EPUB `dc:creator`，两处都没有时写空字符串。

H5 数据库映射为 `books.author_name`。作者名不是书籍身份，禁止用于书籍去重、`book_id` 推导、租户/账号绑定或作者账号登录关联。只按已经定位出的 `book_id` 更新展示字段。

TypeScript 导入示例：

```ts
const owns = (value: object, key: string) =>
  Object.prototype.hasOwnProperty.call(value, key);

function authorText(value: unknown): string {
  if (typeof value !== "string") throw new Error("author must be a string");
  return value; // 摘要按合同原字符串计算；校验器不能 trim、截断或替换后再保存。
}

const sidecarHasAuthor = owns(sidecar, "author");
const manifestHasAuthor = owns(manifest, "author");
if (sidecarHasAuthor && manifestHasAuthor && sidecar.author !== manifest.author) {
  throw new Error("author contract mismatch");
}

const resolvedAuthor = sidecarHasAuthor
  ? authorText(sidecar.author)
  : manifestHasAuthor
    ? authorText(manifest.author)
    : epubDcCreator !== undefined
      ? authorText(epubDcCreator)
      : undefined; // 旧包没有任何作者来源：保留 books.author_name。

if (resolvedAuthor !== undefined) {
  const authorHash = sidecar.versions.author_sha256
    ?? sha256Canonical({author: resolvedAuthor});
  if (authorHash !== book.author_sha256) {
    await tx.updateBookAuthorByBookId(book.id, resolvedAuthor, authorHash);
  }
}
authorElement.textContent = book.author_name; // 作为文本展示，不写入 innerHTML。
```

Novel OS 集成入口：

- `GET /api/projects/{project_id}` 的响应字段 `.author` 返回当前持久化作者名。
- `PATCH /api/projects/{project_id}` 传入 `{"author":"Mara Vale"}` 可人工覆盖；空字符串会被拒绝，后续导出复用该值。
- 新书未提供作者时，Novel OS 使用配置的 Architect 模型生成一次并持久化，不在每次读取或导出时重复生成。
- 旧项目作者为空时，可从 Novel OS 仓库根目录执行 `python core/book_author.py <project>`；该命令使用同一 Architect 路由补写持久化作者，模型调用或结果校验失败时返回错误，不编造替代名。

### 固定媒体类型

引擎对以下交付格式固定输出 MIME，不依赖宿主机或 Docker 的系统类型库；扩展名匹配忽略大小写。sidecar 中嵌套的文件描述项使用同一规则。

| 文件格式 | `media_type` |
|---|---|
| EPUB | `application/epub+zip` |
| Markdown | `text/markdown` |
| JSON | `application/json` |
| PDF | `application/pdf` |
| DOCX | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| PNG | `image/png` |
| JPG / JPEG | `image/jpeg` |
| WebP | `image/webp` |

未知附件保留系统推断，识别不到时使用 `application/octet-stream`；它不是已知 EPUB 或封面格式的合格替代值。MIME 是类型声明，不替代实际 EPUB / 图片解析和文件摘要校验。

如果旧包只有 `book.epub` 的 MIME 声明错误，应由引擎更新后重新打包，并重算 `package_revision_sha256`，不要直接修改 JSON 后沿用旧摘要。仅这一字段修正时，正文、封面、分类、卷和章节身份均保持不变，`import_revision_sha256` 不变，package revision 和 ZIP 原始字节摘要改变。若同时修正其他嵌套文件描述项，按实际变更重新计算相关文件及包摘要。

H5 遇到类型声明与文件角色或实际格式冲突时，返回具体路径及期望/实际类型，保持已有书籍数据；不要仅按后缀跳过类型校验。此修复不改变 schema 版本或旧包兼容规则，也不自动把 v1 包升级为完整 v4 包。

## 3. EPUB 章节精确定位

完整可解析 JSON 见 [h5-import.json 样例](examples/h5-import/h5-import.json)。下面是第 23 章字段节选：

```json
{
  "number": 23,
  "chapter_id": "novel-os:<project-instance-id>:chapter:23",
  "kind": "chapter",
  "volume_id": "volume_02",
  "volume_number": 2,
  "chapter_in_volume": 3,
  "volume_role": "escalation",
  "series_id": "",
  "series_book_number": null,
  "epub": {
    "item_id": "c23",
    "href": "chap023.xhtml",
    "zip_path": "OEBPS/chap023.xhtml",
    "spine_index": 23,
    "xhtml_sha256": "<64位小写十六进制>",
    "xhtml_size": 721
  }
}
```

占位符和 size 只是字段说明；联调用完整样例中的实际数值。

解析过程：

1. 打开 sidecar 指定的 EPUB，验证整个 EPUB 的字节数和 SHA-256。
2. 读取 EPUB `META-INF/container.xml`，找到 OPF。它应等于 `epub.opf_path`，当前是 `OEBPS/content.opf`。
3. 在 OPF manifest 按 `item_id` 查找对应条目，检查其 `href` 等于 sidecar 的 `href`。
4. `href` 相对于 OPF 所在目录；解析后的 ZIP 成员路径应等于 `zip_path`。
5. 读取该 XHTML 原始字节，核对 `xhtml_size / xhtml_sha256`，然后交给现有 EPUB 内容解析器。
6. 用 `chapter_id` 对接本地章节，用 `number` 排序、判断免费章节和扣币。章节标题可从该 XHTML 的正文标题提取；标题不参与匹配。

`spine_index` 是 **从 0 开始、包含序言等非正文** 的 EPUB 阅读位置，仅供交叉校验。`number` 是 **从 1 开始、只计正文** 的全书章号。

第一章的稳定映射是 `number=1 / item_id=c1 / href=chap001.xhtml`。有序言时它的 `spine_index=1`；序言的 `spine_index=0`。不要使用 `spine_index+1` 作为章号，也不要仅按 EPUB 文件名或目录排序推导章号。

引擎还在 OPF 写入 `meta[property="novel-os:chapter-map"]`。其中正文记录包含 `number/item_id/href`，`non_chapter_items` 明确列出非正文，供 H5 交叉验证。

### Introduction 和分卷扉页

`h5-import.json.non_chapter_items[]` 当前会包含：

```json
{
  "kind": "introduction",
  "epub": {
    "item_id": "intro",
    "href": "intro.xhtml",
    "zip_path": "OEBPS/intro.xhtml",
    "spine_index": 0,
    "xhtml_sha256": "<SHA-256>",
    "xhtml_size": 1000
  }
}
```

导读 XHTML 同时使用 `epub:type="introduction"`；正文使用 `epub:type="chapter"`。旧 `STORY_LEAD` 标记在本次 EPUB 编译时也会提取为独立 intro 文档。

当前编译器不额外生成分卷扉页，H5 根据 JSON 卷记录渲染分组目录。合同预留的非正文种类为 `introduction/title_page/volume_title_page/copyright/afterword`，仅接收显式映射。出现未分类的 spine 项、重复映射、未知 kind 时，进入导入错误流程，不按标题猜测。

`chapter_count`、`format.total_chapters`、卷的 `chapter_start/chapter_end` 全部只计算正文。80 章加 Introduction 仍为 80 章。

## 4. 分卷、系列与权益

读取 `meta/novel-serialization.json`，通过 `serialization.volumes` 获取卷标题和边界：

| 全书章号 | volume_id | 卷内章号 |
|---|---|---|
| 1—20 | `volume_01` | 1—20 |
| 21—40 | `volume_02` | 1—20 |
| 41—60 | `volume_03` | 1—20 |
| 61—80 | `volume_04` | 1—20 |

H5 持久化时用 sidecar 每章的绑定值，并验证：

```text
chapter_in_volume = number - chapter_start + 1
chapter_start <= number <= chapter_end
每一章恰好属于一个卷，所有卷连续且无重叠
```

第 21 章属于 Volume 2 的第 1 章，计费身份仍是本书第 21 章。假设免费窗口为前 3 章，条件为 `number <= 3`；`chapter_in_volume <= 3` 会错误地给每卷再次免费。

`volume_role` 是 `setup/turning_point/escalation/climax/aftermath`，旧无结构合同的 EPUB 映射可使用 `unspecified`。这些策划角色不决定计费。

真正的系列分册使用：

```json
{
  "mode": "series_installment",
  "series": {
    "series_id": "wren-harbor-family",
    "title": "The Wren Harbor Family",
    "book_number": 2,
    "planned_books": 4
  }
}
```

共享 `series_id` 的书可聚合展示，按 `book_number` 排序。不同分册各自有 `book_id`，本书章号从 1 开始。本次数据提供关联，跨分册优惠或免费权益沿用 H5 已有产品规则，不由引擎 JSON 推导。

`volume_id` 只在一本书内唯一，`series_id` 使用 H5 租户及来源命名空间。相同系列位置遇到不同书籍身份时提示运营处理，不静默覆盖。

`serialization.volumes` 的 `payoff/central_conflict/obligations` 等含策划信息和结局线索，应存于运营后台。读者接口仅投影卷号、卷名、章节范围；勿把完整策划 JSON 下发阅读器。

## 5. 书籍身份与独立版本

`book_id = "novel-os:" + project_instance_id`，来源是引擎持久化的 `outputs/state/project_identity.json`。同项目重编译、移动目录、部署副本继续使用同一身份；真正创建另一本书时建立新的项目身份。

| 字段 | 语义 | 是否书籍身份 |
|---|---|---|
| `book_id` | 同一逻辑小说 | 是 |
| `chapter_id` | 同一书同一正文章号 | 章节身份 |
| `classification_id` | 分类语义摘要，多本书可相同 | 否 |
| `format_id / serialization_id` | 结构摘要，多本书可相同 | 否 |
| EPUB `dc:identifier` | 由编译内容生成，重编译可能变化 | 否 |
| `run_id / pkg-*` | 生产运行、历史投影版本 | 否 |
| `delivery.snapshot_sha256` | 旧 MD 投影与定稿回执摘要，不含独立封面 | 否 |

**移除早期建议的 `books(serialization_id)` 唯一约束，也不使用上述摘要合并书籍。**

推荐关系及索引（以下 `book_fk` 是 H5 本地书籍主键）：

```text
books: UNIQUE(tenant_id, source_system, source_book_id)
chapters: UNIQUE(book_fk, source_chapter_id), UNIQUE(book_fk, number)
volumes: UNIQUE(book_fk, volume_id), UNIQUE(book_fk, volume_number)
series: UNIQUE(tenant_id, source_system, source_series_id)
book_series: UNIQUE(book_fk); 系列位置冲突单独报告
import_receipts: UNIQUE(book_fk, package_revision_sha256)
```

结构和分类摘要使用普通索引。账单和已解锁权益始终引用 H5 原有的章节主键；更新时 upsert 原记录，避免 delete/reinsert。

`chapter_id` 当前由书籍身份和全书章号组成，支持同章修订、追加章节。中间插章、删章重排和章节拆合需要显式迁移旧章到新章、阅读进度和权益；本次合同没有自动权益迁移表。仅改变卷边界不改变章号和章节身份。

### 分别判断哪些部分发生更新

`h5-import.json.versions` 包含：

| 摘要 | 计算对象 | H5 处理 |
|---|---|---|
| `author_sha256` | 规范 JSON `{"author": author}` | 仅更新 `books.author_name` 和作者版本；保持书、章、正文及权益身份 |
| `epub_sha256` | `book.epub` 完整 ZIP 字节 | 更新 EPUB 存档和解析缓存；变化可能仅来自内嵌元数据 |
| `content_sha256` | 正文章号 + 每章 XHTML 原字节摘要的有序数组 | 更新正文版本；不含 intro、CSS、封面、分类 |
| `front_matter_sha256` | 非正文种类 + 对应 XHTML 字节摘要的有序数组 | 更新导读等非正文内容 |
| `selected_cover_sha256` | 独立已选封面文件原字节 | 替换封面 |
| `cover_metadata_sha256` | `cover-set.json` 原字节 | 更新候选、选择关系及运营记录 |
| `classification_sha256` | 完整分类 JSON 文件原字节 | 更新分类索引、标签快照 |
| `serialization_sha256` | 完整序列化 JSON 文件原字节 | 更新卷、系列关系；保持章节权益 |
| `publication_copy_sha256` | 发布文案 JSON 文件原字节 | 更新运营文案 |

`import_revision_sha256` 聚合 `book_id + versions`。`package_revision_sha256` 覆盖 manifest 所列全部文件，包括封面候选。**先按 `book_id` 定位书，再比较各组件；整个包版本一致才可以完全跳过。** 旧 MD snapshot 一致不代表封面、分类或 EPUB 一致。

完全跳过的条件是传入包与**当前生效**的包及组件一致。历史 receipt 仅证明曾经导入过：A 已导入、随后 B 换封面后再传入 A，应报告历史版本重放，或在用户明确选择恢复模式时恢复 A；不要仅凭历史记录存在而悄悄跳过，也不自动按文件时间回滚。

推荐伪代码（仅展示 ready 分支；必须先处理下表的其他状态）：

```ts
await verifyPackageBytesAndContracts(zip);
const resolvedAuthor = resolveAuthorByFieldPresence(
  sidecar,
  manifest,
  epubDcCreator,
); // undefined 表示旧包完全没有作者来源，应保留数据库现值。
const resolvedAuthorHash = resolvedAuthor === undefined
  ? undefined
  : incoming.versions.author_sha256
    ?? sha256Canonical({author: resolvedAuthor});

await db.transaction(async tx => {
  if (incoming.status !== "ready") {
    await handleLegacyOrMetadataOnlyWithoutReplacingChapters(tx, incoming);
    return;
  }
  const book = await tx.upsertBookBySourceId(tenant, "novel-os", incoming.book_id);
  if (await tx.matchesCurrentAppliedPackageAndComponents(book.id, manifest, incoming)) return;
  await handleHistoricalReplayOrExplicitRestore(tx, book, manifest);

  // 对比各组件，更新需要变化的部分。缺失的可选组件不代表删除。
  if (
    resolvedAuthor !== undefined
    && resolvedAuthorHash !== undefined
    && resolvedAuthorHash !== book.author_sha256
  ) {
    await tx.updateBookAuthorByBookId(book.id, resolvedAuthor, resolvedAuthorHash);
  }
  await applyEpubChapterChanges(tx, book, incoming);
  await applyClassificationAndVolumes(tx, book, incoming);
  if (incoming.cover.selection_action === "replace") {
    await applySelectedCoverIfHashChanged(tx, book, incoming.cover.selected);
  }
  await preserveExistingChapterIdsAndEntitlements(tx, book);
  await tx.upsertImportReceipt(book.id, manifest.package_revision_sha256, incoming.versions);
});
```

伪代码中的函数是 H5 待实现的职责，不是已经提供的 JS SDK。

`metadata_only` 先查找已有书，缺少目标书时返回需要 EPUB；只处理本次存在的组件，绝不把 `chapters=[]` 写回旧书。`legacy_epub` 交给已有 EPUB 解析器，未确认章节映射时不覆盖已绑定章节/卷。组件摘要为 null 表示本次缺失，保留数据库当前组件及其版本；导入 receipt 记录本次原始摘要，当前生效版本记录合并后的状态。

## 6. 哈希口径

所有 SHA-256 输出 64 位小写十六进制。文件哈希始终计算解压后的文件原始 bytes，保持 BOM、换行、XML 属性和空白，不先 decode/trim/normalize 或 JSON parse 再 stringify。

- `files[].size`：字节数。
- `epub.sha256`：整个 EPUB ZIP 文件字节。
- `chapters[].epub.xhtml_sha256`：EPUB 内对应 XHTML 成员解压后的字节；包括章节标题和标签。不等于抽取纯文本的摘要。
- 原有 `publication_package.chapters[].body_sha256`：相应 MD 章节文件的 UTF-8 字节。原有 `rune_count` 计算 Unicode code points，不等于 UTF-8 bytes 或 JS 字符串 `.length`。
- Markdown 与 XHTML 的表示不同，两个摘要通常不同；H5 不拿 MD 摘要验证 EPUB 文本。

本次派生摘要采用 `UTF8(JSON)`，对象键按字典序排序、无缩进和空格、字符串保留 Unicode、数组顺序保留、无结尾 LF。这里参与新摘要的值只含字符串、整数、null、布尔和数组/对象，避免跨语言浮点格式差异。

```text
content_sha256 = SHA256(canonical([
  {"number": 1, "xhtml_sha256": "..."},
  {"number": 2, "xhtml_sha256": "..."}
]))
front_matter_sha256 = SHA256(canonical([
  {"kind": "introduction", "xhtml_sha256": "..."}
]))
author_sha256 = SHA256(canonical({"author": "Mara Vale"}))
import_revision_sha256 = SHA256(canonical({"book_id": "...", "versions": {...}}))
package_revision_sha256 = SHA256(canonical(manifest.files))
```

`manifest.files` 按路径升序排列。对文件验证直接使用原始 bytes 更稳妥；语义分类摘要规则在分类字典文档中单独列出。

## 7. 封面完整合同

完整 V2 示例见 [cover-set.json](examples/h5-import/cover-set.json)，包含全部 brief、concepts、candidates、选择关系及时间字段，没有截断占位。

封面集合主字段：`cover_set_id/project_id/brief_schema_version/compiler_version/source_prompt_sha256/foundation_sha256/status/requested_count/selected_candidate_id/revision/created_at/updated_at/brief/concepts/candidates`。

候选主字段：`candidate_id/concept_id/status/relative_path/media_id/sha256/width/height/content_type/provider/model/request_id/generation_prompt/safe_request_parameters/error/created_at/prompt_revision/attempt_history/quality_report`。`brief_schema_version` 为 1 或 2；H5 将 brief、生成提示词、质量报告作为不透明运营元数据存储，不依赖它们渲染书库。

| 规则 | 说明 |
|---|---|
| 候选身份 | `candidate_id`；不要用数组下标作为数据库身份 |
| 展示排序 | `candidates[]` 原始顺序，或 manifest 的 `display_order`（从 1 开始） |
| 方案关联 | `candidate.concept_id -> concepts[].concept_id` |
| 选中关系 | `selected_candidate_id -> candidates[].candidate_id` |
| 候选状态 | `pending/ready/failed/rejected/selected` |
| 集合状态 | `generating/partial/ready/selected/failed/stale` |
| 路径 | candidate 的 `relative_path` 是项目相对路径，通常以 `outputs/deliverables/` 开头；ZIP 内删除这个固定前缀。优先使用 manifest 按 `candidate_id` 查到的 ZIP 路径 |
| 已选文件 | 唯一的 `role=selected_cover`，独立于 pending 目录；用实际 media_type 和路径，支持 jpg/png/webp |
| 哈希对应 | 有选中候选时，候选 `sha256` 应等于独立 selected 文件的 `sha256` |
| 未选中 | `selected_candidate_id=""`；仅候选不自动替换读者封面 |
| 无独立封面 | `selection_action=keep_existing`，保留 H5 原封面；合同没有隐式删除封面的含义 |

历史封面重新选择时，pending 路径可能已被新版本占用，因此 **选中封面取独立 selected 文件**。历史候选以媒体库或 attempt_history 为源，H5 不用一个同名 pending 文件冒充历史原图。更新 `cover_metadata_sha256` 即使选中图片相同，也应同步候选/选择关系。

## 8. 分类字典

文件 [novel-classification-catalog.json](examples/h5-import/novel-classification-catalog.json) 是完整标准目录；[分类说明](novel-classification.md) 列出字段和每个 ID 的维度及中英文名称。样包的 [novel-classification.json](examples/h5-import/novel-classification.json) 则是这一本书的分类。

`primary_genre_id` 为单选；`secondary_genre_ids/story_type_ids/tone_ids/setting_ids` 为各维度数组；`filter_type_ids` 是保序去重并集，供混合筛选。`audience.channel` 是 `female/male/general`，`length.form` 是 `short/long/unknown`。

数据库按标准 ID 查询，`display_labels` 用于展示。推广指数本次不接入。导入时保存原始分类 JSON 和 catalog_version，便于后续重新投影标签。

## 9. 兼容、冲突与异常

| 输入或异常 | 处理规则 |
|---|---|
| Manifest v4 + sidecar ready | 完整校验 EPUB map，执行新流程 |
| sidecar `metadata_only` | 包中无 EPUB，只允许更新已有书的封面/分类等；新建正文书需要 EPUB |
| sidecar `legacy_epub` | 旧 EPUB 没有显式 map，保留 EPUB 正文解析路径；不自动关联卷或按 spine 猜章号，重新编译可取得新 map |
| 新包 sidecar 与 manifest 的 `author` 不同 | 停止作者及整包事务性更新，报告作者合同冲突 |
| 旧包缺少 `author` / `author_sha256` | 按 sidecar → manifest → EPUB `dc:creator` 顺序读取；均不存在时保留 `books.author_name`，不把缺失解释为空字符串 |
| Manifest v1/v2/v3，无 sidecar | 走现有 EPUB 解析器；分类可由唯一的分类文件补入；旧包没有稳定 book_id 时由运营明确选择目标书或新建，禁止按书名/结构摘要合并 |
| 缺少分类文件 | 正文可导入，分类进入待补录；已存在分类时缺失不清空 |
| 缺少序列化文件 | 允许单组目录兼容，自动多卷关联待补齐；保留已有卷数据 |
| 没有独立已选封面 | 保留原封面，不从候选中任取一张 |
| 多份分类/序列化/import role，多个 EPUB body_source | 元数据歧义，停止此次更新并返回冲突路径 |
| JSON 与 EPUB 内嵌分类/分卷/map 冲突 | 停止此次自动导入，返回具体不一致字段；引擎重编译修正 |
| 同包还含旧 MD publication JSON 且分类/分卷与根 JSON 冲突 | 报告冲突，不使用时间戳猜哪个最新；MD 正文仍不导入 |
| 指针引用的文件不存在、size 或摘要不符 | 标记损坏，保持已有书/封面/权益，输出具体文件路径和错误类型 |
| 不支持的新 schema/catalog | 不按旧字段静默解释，保留上传记录并报告需升级的版本 |
| 非连续章号、卷重叠、章落在卷外、未知 spine 项 | 停止正文/卷的事务性更新，返回章号或 item ID |

独立“只替换封面”操作可只校验封面部分；它不要求缺失的分类数据。完整导入若检测到损坏或冲突，维持原书状态，避免导入一半造成章数和权益错位。ZIP 解析器应检查重复路径、绝对路径、`..`、展开大小；所有存储路径以导入临时目录为边界。

## 10. 实际样包与验收

样包由当前 EPUB 编译器和交付打包器真实生成：

- [01：Introduction + 80 章 + 4 卷](examples/h5-import/01-introduction-80-chapters-4-volumes.zip)
- [02：同一本书，仅换选中封面](examples/h5-import/02-same-book-cover-only-update.zip)
- [03：另一书籍身份、相同正文和结构](examples/h5-import/03-different-book-same-structure.zip)
- [逐文件与版本对比报告](examples/h5-import/verification.json)

正文是简短技术夹具，PNG 是标注测试用途的有效图片；它们用于对接验收，不代表完整小说或 AI 封面质量。包内含完整 V2 cover-set、4 个候选、EPUB、分类、序列化与 H5 导入清单。

验收顺序：

1. 导入包 01：只产生一本书、80 个正文章节、4 个卷；Introduction 单独展示。
2. 免费窗口设为 3：只有全书 1—3 章免费，Volume 2 第 1 章（全书 21）保持付费。
3. 解锁第 21 章并记录阅读进度，再导入包 02：书数、章数、主键、权益、进度保持；封面从候选 1 更新为候选 2。
4. 再导入包 02：幂等，无重复书、卷、章节或收费。
5. 按校验报告确认两包 EPUB 字节与 content_sha256 完全相同；只有 selected_cover_sha256 和 cover_metadata_sha256 改变，import/package revision 随之改变。
6. 导入包 03：允许建立第二本书；即使 EPUB/结构相同，book_id 不同仍独立建书。serialization_id 无唯一约束。
7. 故意修改一段 EPUB、一个分类字段或复制出第二个 role 条目：导入报告损坏/冲突，既有数据保持完整。

重新生成样包：

```bash
venv/bin/python scripts/build_h5_handoff_samples.py
```

该脚本同时验证 ZIP、各文件摘要、EPUB map、分卷、序言章号和封面更新差异，不调用模型。
