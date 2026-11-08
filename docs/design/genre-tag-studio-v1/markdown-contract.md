# Markdown 知识合同 v1

2026-11-05 · 已实现。本文为代码合同，取代设计稿中的旧 profile、legacy_h5 和额外 placement 对象方案。一个条目一个 Markdown 文件；资料不是可执行 Skill，不授予工具权限。

## 文件格式

```yaml
---
schema: novel-knowledge.v1
id: tag.second_chance
kind: tag
name: 第二次机会
aliases: [Second Chance]
genre_ids: [genre.romance]
publication:
  labels: {zh-CN: 第二次机会, en-US: Second Chance}
  definition: 曾经失败或错过的同一段亲密关系，在实际改变后获得新的机会。
---
```

Genre 使用 `kind: genre`、`genre.*` ID、空 `genre_ids`；Tag 使用 `kind: tag`、`tag.*` ID，至少关联一个已启用 Genre。ID 正则为 `^(genre|tag)\.[a-z][a-z0-9_-]{1,74}$`；启用后不可更改身份。名称最长100字符。名称/别名冲突按所属 Genre 检查，不用相同中文自动合并不同机制。公开 metadata 可省略，代码只用名称作为最小公开定义，绝不自动发布私有“核心承诺”。推荐填写简短明确的双语公开说明。

`publication` 只允许 `labels`（zh-CN、en-US，至少一种，每项1–100字符）与 `definition`（1–600字符）。写作版本每次启用变化增长；公开 revision 只在 labels/definition 改变时增长。关联索引只用于引擎选择，不作为另一份 H5 全球目录发布。

文件≤24 KiB、YAML头≤8 KiB；拒绝未知头字段、重复键、YAML别名、类型不符和重复分节。字段不允许 `legacy_h5`；新版没有静默映射到旧 H5 的路径。保存草稿允许分节未完成，启用要求下列六节都有正文。Markdown 原字节（包括结尾换行）保存并计算SHA。

## 作者编写的六节

| 标题 | 内容 |
|---|---|
| `## 核心承诺` | 读者必须感受到的关系、冲突或体验；不止题材名称 |
| `## 故事机制` | 压力怎样改变选择，谁承担代价，回报如何挣得 |
| `## 发散空间` | 可变化的目标、关系起点、环境和结局；示例不等于固定剧情 |
| `## 组合原则` | 与其他标签相互改变的方式、必须由作者解决的大分歧 |
| `## 场景与表达` | 可见行动、对白策略、视角、节奏和应避免的套路 |
| `## 简介与兑现` | Synopsis 的吸引承诺、剧透边界及正文兑现要求 |

可选 `## 封面表达` 只进入封面规划。其他分节作为附录保存，投影清单明确列为排除，不进入模型；研究全文不会自动成为运行提示词。推荐800–1600中文字是编辑目标，不是启用质量门槛。

## 确定性阶段编译

`core/creative_types.py:compile_snapshot` 是唯一编译入口。每个阶段均保留所有选中条目的ID、版本、原文SHA、完整核心承诺；不会用模型摘要代替原文，不按位置偷偷丢Tag。

| stage | 纳入分节 |
|---|---|
| brainstorm | 核心承诺、故事机制、发散空间、组合原则、简介与兑现 |
| story_design | 六个必要分节 |
| chapter_plan | 核心承诺、故事机制、组合原则、场景与表达、简介与兑现 |
| draft | 核心承诺、故事机制、场景与表达 |
| review | 核心承诺、故事机制、场景与表达、简介与兑现 |
| publication | 核心承诺、简介与兑现 |
| cover | 核心承诺、封面表达（存在时） |

单阶段编译结果≤96 KiB；超限返回各条目字节贡献，请作者精简，而不是截断后发送。`genre-context.v2` 携带 `projection_manifest`、`selection_sha256` 和 `knowledge-compiler.v1`。编译零模型调用。review 指现有写作中的编辑/连续性/风格环节，不是已删除的 M3。

## 冻结与执行

`selection-snapshot.v1` 冻结一个Genre、1–20个去重且关联合法的Tag、完整MD、不可变版本和SHA、公开taxonomy、父快照SHA和自身SHA。重新采用资料创建子快照，沿用Workshop修订/重启确认；已开始正文的run继续使用其不可变交接，不从最新资料库回填。

本书如何兑现这些承诺，由现有故事地图、作者已采用方案、骨架及章卡表达；不再引入第二套逐Tag placement JSON与额外付费审查。阶段性剧情无需每章出现，但每章都能看到相关机制指导，并结合当前章卡、既有事实与未来计划边界执行。计划不等于正文已兑现。

模型负责创作方案，代码负责选择身份与分类。骨架缺少或擅改分类时，唯一服务端绑定层负责生成正确对象；破损/重复块明确失败。网页与CLI统一验证骨架 taxonomy 与冻结快照一致。CLI入口：

```bash
venv/bin/python core/orchestrator.py run --help
# 按 run 帮助补齐本书参数，同时提供：
# --project /absolute/project --prompt /absolute/prompt.md --knowledge /absolute/knowledge.json --quality-policy evidence_v1
```

`--knowledge` 不接受stdin骨架；快照≤2MiB。动态分类骨架缺少快照会在调用模型前失败，不猜测标签。普通手工旧格式文件读取保留，但不拥有新版知识效果。示例以生产 [resources/novel-knowledge](../../../resources/novel-knowledge/) 为准。
