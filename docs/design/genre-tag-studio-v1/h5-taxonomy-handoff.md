# H5 动态 Genre / Tags 交接手册

2026-11-05 · Novel OS 导出端已实现并通过本地合同验收；**H5 导入端尚未升级和联合验收**。无需开发 Novel OS 内的 H5 预览展示。本手册补充[现行 ZIP 导入规范](../../h5-import-handoff.md)，不能用新包的引擎校验通过冒充线上 H5 已兼容。

## 1. 不变的协议

继续使用 Manifest V4、`novel-h5-import.v1`、`ready`。正文来自 EPUB；作者、章节映射、非正文、分卷、更新身份、封面和 SHA-256 规则不变。分类变化不能重建书籍/章节身份、清空阅读进度或解锁权益。

| 对象 | 上限 |
|---|---|
| ZIP | 64 MiB；解压128 MiB；1024条目 |
| EPUB | 16 MiB；解压32 MiB；600条目；512 spine（含 Introduction） |
| Introduction | 最多一篇且位于开头，不计正文与免费章节数 |
| JSON / XHTML | 单文件4 MiB，原解析深度限制不变 |
| 出版封面 | PNG/JPEG；8 MiB；单边8192px；16MP；候选最多24张 |

引擎参考图入库的12MiB限制不等于出版封面的8MiB限制。原参考图、来源全文、分析、写作MD及讨论记录不导出。

## 2. 唯一分类对象

新包 `novel-classification.v2` 内嵌 `novel-taxonomy.v1`。完整真实对象见 [classification.example.json](../../examples/h5-import/dynamic-taxonomy/classification.example.json)。仍通过既有manifest role和 `meta/h5-import.json.classification.path` 定位分类sidecar；EPUB OPF元数据 `novel-os:classification` 必须与sidecar严格一致。不得只改catalog_version或手改ZIP文件。

```json
{
  "schema_version": "novel-taxonomy.v1",
  "namespace": "novel-os.publisher-example",
  "genre": {
    "id": "genre.romance", "revision": 1,
    "labels": {"zh-CN": "浪漫", "en-US": "Romance"},
    "definition": "亲密关系的变化影响主要选择与回报。"
  },
  "tags": [{
    "id": "tag.second_chance", "revision": 1,
    "labels": {"zh-CN": "第二次机会", "en-US": "Second Chance"},
    "definition": "同一段失败或错过的亲密关系，在实际改变后获得新的机会。"
  }]
}
```

上面仅展示taxonomy部分。外层保留现行分类的读者频道、长度等非题材字段，`primary_genre_id` 必须等于 `taxonomy.genre.id`；`story_type_ids` 必须按顺序等于 `taxonomy.tags[*].id`。v2不使用旧secondary/tone/setting并行标签数组；这些创作含义可以成为显式Tag。

| 字段 | 实际验证 |
|---|---|
| namespace | 最长120字符；`^[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+$` |
| id | `^(genre|tag)\.[a-z][a-z0-9_-]{1,74}$`，kind与前缀一致 |
| revision | 整数≥1 |
| labels | zh-CN/en-US至少一种，各1–100字符 |
| definition | 1–600字符，作为纯文本显示 |
| genre / tags | 恰好1Genre，1–20个去重Tag；未知字段拒绝 |

Markdown正文不是发行文案。`publication.labels/definition` 是作者维护的公开信息，未填时仅以条目名称做最小公开描述。只有公开信息改变才增加目录revision，普通写作指导改动不增加它；改变核心含义应新建ID。Tag的全局适用Genre索引留在引擎，本包只声明本书组合。

## 3. 命名空间与冻结

部署时设置 `NOVEL_OS_PUBLISHER_NAMESPACE` 为该发行方的持久命名空间；本地默认 `novel-os.local` 仅供开发。H5必须把命名空间与已认证导入来源绑定，不允许任意ZIP冒用其他发行方。目录主键使用 `(namespace, kind, id)`，不能按中文名称或slug合并。

公开taxonomy与写作快照在创建/明确重新采用组合时一起冻结。已有书导出不读取当前资料库的“最新名称”。因此资料停用、删除、重命名不会改写在途小说或使其无法导出。旧书要采用新公开修订，应明确生成新书籍修订并重新编译所有产物。

SHA由生产编译器统一计算：classification_id按既有规范化JSON散列，文件SHA按实际UTF-8字节，EPUB及包SHA按最终字节。以仓库 `core/novel_classification.py`、`core/publication_taxonomy.py` 和真实夹具为准；不要让模型写散列，也不要为了适配旧字典静默改ID。

## 4. H5 实现工作

1. 保留v1固定目录读取；新增v2严格解析。验证全部包、taxonomy身份、分类SHA、EPUB/sidecar同源、章节映射和尺寸之后再写库。
2. 同一事务保存目录修订、书籍分类快照和关联。相同ID/修订/公开内容重复导入幂等；相同ID/修订却内容不同报冲突；旧修订不覆盖更新的目录。事务失败不留下孤立分类/书章。
3. 书籍详情显示其导入时快照；聚合目录可以使用最新名称，但查询始终按稳定ID。不同命名空间的同名项不得自动合并。
4. 管理台与读者端从API读取有可见作品的Genre/Tags，服务器计算筛选、数量及分页；新增Tag无需前端枚举发版。标签关联来自已导入作品，不从一本书推断全平台唯一关系。
5. Synopsis沿用现有作品简介字段，不新建竞争字段。labels/definition按纯文本渲染，不作为HTML/指令或外部资源执行。
6. 明确声明支持 `novel-classification.v2` 和 `novel-taxonomy.v1`；未升级目标在预检返回具体不支持原因。

引擎动态目标收据为：

```json
{
  "target_profile": "novel-h5-export.dynamic-taxonomy.v1",
  "validator_revision": "novel-os:dynamic-taxonomy.v1",
  "required_h5_capabilities": ["novel-classification.v2", "novel-taxonomy.v1"],
  "engine_contract_status": "passed",
  "external_import_status": "pending"
}
```

`catalog_sha256` 是本书公开taxonomy的规范化散列。此target_profile不是旧H5已认证支持的声明。新系统没有“丢弃不认识的Tag后导出旧包”的降级选项。

## 5. 已交付夹具与联合验收

[夹具目录](../../examples/h5-import/dynamic-taxonomy/README.md)包含真实导出器生成的合法包、分类被篡改而未更新SHA的非法包、完整分类JSON和3例导入验收计划。引擎本地结果：合法包通过、非法包 `file_digest`；初次/重复导入的H5结果仍待执行。

H5还需完成：同书正文/封面/作者/分类单独更新；同名异ID、更名、同revision冲突、旧修订、伪造命名空间；缺失/重复Tag、混合schema、EPUB分类不一致；全部原有大小/路径/Introduction边界及旧包回归。保留原书身份与权益测试。

Novel OS源码当前在未提交工作区，未提供不存在的提交SHA。双方发版时在验收记录补真实commit、H5响应、数据库迁移/回滚与目标版本。H5端数据升级由H5任务实施，本轮没有修改其代码或线上数据库。
