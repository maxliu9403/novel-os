# 独立 Tag Markdown 格式

2026-11-06，格式标识 `tag-prompt.v1`。支持用户提供的无 YAML 头部的 Tag 提示词；原有 `novel-knowledge.v1` 文档继续按其版本规则读取。

## 身份与原文

第一行使用 `# 中文名称 English Name`。首个二级标题前的引用行声明 `关联 Genre：English Name`，例如 `> 44 本 · 关联 Genre：Romance · 浪漫 · 分类：复仇/追妻`。

英文名称用于生成稳定 ID：转小写，空格、连字符、斜杠等分隔符规范为下划线。例如 Contract Marriage → `tag.contract_marriage`，Heir/Heirness → `tag.heir_heirness`，Romance → `genre.romance`。已保存资料不能通过修改英文名称来更换 ID；中文展示名称可以编辑。公开分类使用标题中的中英文名称，不把私有创作提示词放入 H5 分类目录。

这只是身份投影，不向用户 Markdown 插入任何头部。编辑、保存、下载、不可变版本、选择快照以及模型上下文中的原文保持一致，包括换行和末尾空白。SHA-256 按 UTF-8 原文字节计算。

## 分节与模型上下文

启用时仍校验六个必填分节：核心承诺、故事机制、发散空间、组合原则、场景与表达、简介与兑现。独立格式允许额外分节，新增的必写场景、人物配置、前十章节拍、常见失败模式、自检等全部保留。代码块里的标题不会被当成真实分节。

只将作者选择的 Tag 装入上下文，不把整个库一次发送。每个独立 Tag 以完整 `markdown` 字段进入头脑风暴、全书设计、章节规划、正文、已有联合审稿、出版文案和封面阶段；不再同时发送重复的解析后章节。当前请求阶段与已有创作模式/正典/响应协议仍由调用方明确指定。没有新增创作限制，也没有重新启用已移除的全书阅读评议功能。

含此格式的快照标记 `knowledge-compiler.v2`；仅含原格式的快照保留 v1 投影。已有作品的冻结资料不会因资料库覆盖而自动更新。新选择会绑定当前启用版本。

原有单文件 24 KiB、所选资料阶段上下文 96 KiB 的容量保护保留。超限明确报错，不悄悄删除原文；最终请求还受到各模型服务的完整请求限制。代码证明内容到达请求，不等于证明模型已正确执行全部创作要求。

## 批量导入

```bash
# 预览；不会写库或调用模型
python -m core.knowledge_import resources/knowledge-imports/romance-35-tags --store /data/creative_types/default

# 在对应运行环境中明确执行，重复 ID 覆盖为新启用版本
python -m core.knowledge_import resources/knowledge-imports/romance-35-tags --store /data/creative_types/default --apply
```

目录只读取顶层 `.md`，排除 `README.md` 索引。重复 ID 在同一事务里替换，新增 ID 创建并启用。先全部验证身份、分节、Genre 关联和名称冲突，再写入一个资料库文件；任何一份错误都不会部分提交。预览到写入之间如发生用户编辑，以 revision 检查拒绝覆盖。不同 ID 却同名时要求明确身份，不擅自合并。已删除 ID 不自动复活。

导入前自动保存 `import-backups/<内容哈希>.json`，包括原内置资料和所有旧版本。完全相同的再次导入不增加版本、不重写资料库。原文件及仓库中的原文包不被格式转换器修改。

后续日常维护可以直接在「Genre 与 Tags」编辑和保存此格式，也可使用现有「导入 Markdown」入口导入单个文件。
