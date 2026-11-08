# H5 读者频道交接：男频、女频与动态分类

2026-11-08。本文补充 [H5 ZIP 导入交接规范](h5-import-handoff.md) 和 [动态 Genre / Tags 交接手册](design/genre-tag-studio-v1/h5-taxonomy-handoff.md)。**引擎本地合同校验不等于 H5 已完成适配；本轮未运行 H5 导入或修改 H5 数据库。**

引擎侧于2026-11-08 18:33（UTC+08:00）最终部署至5174，前后端版本均为 `audience-author-recovery-20261108`，健康检查通过；运行资料库已安全追加39项、跳过3条删除记录、0冲突。后端综合870项、并发32项及最终相关回归108项分别通过，不汇总为互斥测试总数；前端655项、lint、TypeScript、Vite通过，频道及历史恢复等浏览器复验通过。仅重启Novel OS前后端，未改网关、调用真实模型或恢复业务任务。该发布不改变下述 H5 `pending` 状态，也不代表真实小说质量已经验收。详见[引擎实施记录](reports/2026-11-08-audience-author-workflow.md)。

## 1. 协议边界

继续使用 Manifest V4、`novel-h5-import.v1`、`ready`，正文来自 EPUB。读者频道使用现有分类对象的 `audience.channel`，不增加 ZIP 私有文件，也不新建竞争字段。作者、正文与非正文映射、分卷、书籍和章节身份、封面及散列规则不变。

既有限制继续适用：ZIP 压缩后 64 MiB、展开 128 MiB / 1,024 项；EPUB 压缩后 16 MiB、展开 32 MiB / 600 项、最多 512 个 spine 条目（含 Introduction）。最多一篇开头 Introduction，不计入正文章数。出版封面仅 PNG/JPEG，单张 8 MiB、单边 8,192 像素、16MP；不把参考图片的导入限制当作出版限制。

新创建讨论必须由作者明确选择男频或女频，冻结在 `selection-snapshot.v2.audience_channel` 中。新选择为一个 Genre 和一至两个 Tag。写作引导、骨架、正文与封面使用冻结选择；发行字段由代码绑定，不由模型猜测。

## 2. H5 应读取哪个字段

通过 manifest 中 `role == "novel_classification"` 和 `h5-import.json.classification.path` 定位分类 sidecar；两条指针必须一致。其 `audience` 片段为：

```json
{
  "audience": {
    "channel": "female",
    "age_band": "unknown"
  }
}
```

这是分类对象的片段，不是一个新增文件或完整分类示例。完整动态分类仍为 `novel-classification.v2`，内嵌 `novel-taxonomy.v1`；结构示例见 [动态分类示例](examples/h5-import/dynamic-taxonomy/classification.example.json)。

| `audience.channel` | 展示 | 含义 |
|---|---|---|
| `male` | 男频 | 作者选择的读者频道 |
| `female` | 女频 | 作者选择的读者频道 |
| `general` | 通用 | 历史记录或既有通用定位；保留其原值 |

频道不是主角性别、性向、叙事视角、年龄段或结局。`age_band` 是独立字段；选择男频或女频不自动改变年龄段。不得根据 Romance、角色姓名、Tag 名称、封面性别或描述中的关键词重新推断频道。

新创作入口只接受 `male` / `female`，但这不是要求 H5 拒绝历史 `general` 包：先前动态分类 v2 也可能合法使用 `general`。H5 仍应接受既有三个枚举。旧格式缺字段的处理沿用旧协议及其身份计算，不得先修改原 JSON 再验 SHA；错误类型和未知非空枚举应明确拒绝，不能静默替换为女频。对于没有分类组件的既有元数据更新，沿用组件缺失即不更新的规则，不能清空数据库频道。

EPUB OPF `meta[property="novel-os:classification"]` 内的**完整分类对象**必须与 sidecar 一致，包括 `audience.channel` 和 `age_band`。不得只读取页面显示的 `Audience` 文本，或只改 ZIP 里的某一份 JSON。

## 3. 频道与动态 Genre / Tags 分开处理

频道是固定枚举；Genre 与 Tags 是发行方提供的动态公开目录。目录主键继续使用 `(namespace, kind, id)`，不能按中文名称合并。

- `primary_genre_id == taxonomy.genre.id`；`story_type_ids` 按顺序等于 `taxonomy.tags[*].id`。
- 一本书可以使用一个 Genre 与跨 Genre 的 Tags；H5 不应恢复旧的 Genre–Tag 硬绑定。
- 同一个 Tag 可出现在男频和女频作品中。筛选项和数量可根据已导入、可见的作品聚合，不能把第一本书的频道写成该 Tag 的全局限制。
- 新创作界面的一至两个 Tag 限制不追溯改写历史书。动态发行合同保留一至二十个 Tag 的历史容量；H5 不因旧包超过两个而拒绝合法包。
- 资料库的 `audience_channels` 是写作资料适用性，属于引擎内部；一本书的 `audience.channel` 才是 H5 应导入的最终选择。

写作指导 Markdown、频道方法全文、用户补充、模型提示、参考资料和私有创作依据报告不进入 H5 ZIP。H5 显示公开 `labels` / `definition`，按纯文本渲染；公开标签不能被当作提示词执行。

## 4. 更改频道时必须同步的身份和 SHA

频道参与分类语义身份。应由引擎重新编译完整产物，再导入；不能人工改单字段后继续使用原来的散列。

| 对象 | 频道改变后的处理 |
|---|---|
| 分类 `classification_id` | 按既有规范化身份算法重算，包含 `audience` |
| 分类 sidecar 文件 SHA-256 | 按最终 UTF-8 文件字节重算 |
| EPUB 内嵌分类 | 同步完整分类对象，重新构建 EPUB |
| EPUB SHA-256 | 按重新构建后的 EPUB 字节重算 |
| `h5-import.json` 分类/EPUB 文件描述项 | 同步路径、长度、媒体类型和 SHA |
| `versions.classification_sha256` / `versions.epub_sha256` | 分别与实际文件描述项一致 |
| `import_revision_sha256` | 按既有 `book_id + versions` 规范化算法重算 |
| manifest 文件索引与 `package_revision_sha256` | 包含更新后的分类、EPUB 和导入 sidecar 描述项，统一重算 |

`catalog_sha256` 是公开 `taxonomy` 对象的散列。频道位于外层 `audience`，仅更改频道且 taxonomy 未变时，`catalog_sha256` 可以不变；不能以它未变为理由跳过分类更新。仅修改私有写作指导，不自动增加公开目录修订号。

正文没有改变时，章节正文的摘要、`book_id`、`chapter_id` 及读者权益保持原有身份；EPUB 元数据变化不等于新建一本书。H5 应在完整验证后原子更新同一本书的分类和频道，不重置收藏、购买、已解锁章节或阅读进度。

算法以 [分类实现](../core/novel_classification.py)、[公开目录投影](../core/publication_taxonomy.py)、[导入清单编译](../core/h5_import.py) 为准。各 SHA 的语义和文件字节摘要不可混用。

## 5. H5 实施与验收

1. 核对当前 ZIP 校验器对 `novel-classification.v2` / `novel-taxonomy.v1` 的支持。频道字段本身沿用旧结构，不代表旧固定字典已经兼容动态目录；不允许只改 `catalog_version` 绕过验证。
2. 在书籍模型中持久化已校验的频道，并保留书籍分类快照。提供固定 `male` / `female` / `general` 过滤值；界面可展示为男频、女频、通用。
3. Genre / Tags 的展示和分页计数从公开目录与可见作品关联中读取；频道与 Genre / Tags 过滤可组合。多语言名称不是查询主键。
4. 重复导入幂等；同书修改频道保持原书/原章身份与权益。验签、EPUB 一致性或目录冲突失败时，整笔导入不落库。
5. 保留原有路径、压缩炸弹、重复文件、图像解码、Introduction、作者、分卷和哈希验收，不因本次字段适配而放松。

联合测试至少覆盖：男频与女频合法新包、历史 `general`、旧包缺字段的既定处理、未知枚举、EPUB/sidecar 频道不一致、只改分类而未重算 SHA、同一 Tag 跨频道展示、同书频道更新、重导入幂等及权益不变。

引擎测试入口为 [test_dynamic_taxonomy_delivery.py](../tests/test_dynamic_taxonomy_delivery.py)，使用真实本地编译与 ZIP 检查路径，分别参数化 `male`、`female`、历史 `general`，核对 sidecar / EPUB / 导入清单的一致性以及私有资料不进入包。该测试不会调用模型或访问 H5。H5 的业务导入、数据库更新及阅读筛选仍需由 H5 任务执行，并记录双方实际代码版本、响应与回滚方案；在此之前，外部验收状态保持 `pending`。

## 6. 可重建的正反样包

[verify_audience_delivery.py](../scripts/verify_audience_delivery.py) 使用生产 EPUB／ZIP 编译与校验器创建合成两章样包，没有真实小说、写作提示词或模型调用。安装项目依赖后从仓库根目录运行，输出到一个新的目录：

```bash
python scripts/verify_audience_delivery.py --output-dir /private/tmp/novel-audience-acceptance
```

本轮已在该目录生成 `README.md`、`verification-report.json` 和以下文件；交接时复制整个目录并核对报告中的 SHA，换机可用脚本重建，不要求复用临时目录的绝对路径。重建时 ZIP 身份／字节可能不同，以当次报告为准。

| 文件 | 本地实测 | 验证目的 |
|---|---|---|
| `valid-male.zip` | passed | 男频完整交付一致 |
| `valid-female.zip` | passed | 女频完整交付一致 |
| `valid-general.zip` | passed | 保留历史通用频道 |
| `invalid-unknown_channel.zip` | `malformed_package_contract` | 内外频道一致、外层散列正确，仍拒绝非法枚举 |
| `invalid-mismatch.zip` | `malformed_package_contract` | 外层散列正确，但 EPUB 与分类 sidecar 频道不同 |
| `invalid-hash_corrupt.zip` | `file_digest` | 分类文件字节变更而散列未更新 |
| `new-v2-missing-channel.json` | 输入校验拒绝 | 记录新建 v2 缺频道被 `SessionInput` 拒绝的离线证据，非待导入 ZIP |

无频道的新建输入在导出前就会被拒绝，因此不存在“先生成缺失频道的新包，再交给H5修复”的合法工作流。`valid-general.zip` 仅验证历史兼容，不允许新建入口以 `general` 跳过选择。外部 H5 尚未运行这些样包；联合验收还应验证失败时无数据库残留、重复导入及权益保持。
