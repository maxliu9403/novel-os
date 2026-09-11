# EPUB 导入联调样包

请先阅读 [完整交接](../../h5-import-handoff.md)。此目录包含由实际编译器和打包器生成的有效 EPUB/ZIP/PNG，正文是技术测试短段落。

1. 导入 `01-introduction-80-chapters-4-volumes.zip`：一本书，80 个正文，4 卷，独立 Introduction。
2. 解锁全书第 21 章并记录进度。
3. 导入 `02-same-book-cover-only-update.zip`：身份和 EPUB 完全一致，选中封面从候选 1 改为候选 2；原章节及权益保持。
4. 再导入包 02：幂等处理。
5. 导入 `03-different-book-same-structure.zip`：相同正文与结构、另一个 book_id，建立第二本书。

`verification.json` 记录实际 ZIP SHA-256、共同 EPUB/正文摘要和变化的两个封面组件。

旁置的 `h5-import.json`、`cover-set.json`、`novel-classification.json`、`novel-serialization.json`、`package-manifest.json` 均来自包 01，可直接阅读。包 02 的完整元数据在 ZIP 内。

`novel-classification-catalog.json` 是完整字典，`classification-dictionary.md` 是全部 ID/维度/中英文对照。

在仓库根目录运行以下命令可重新生成并核验全部样包；同一次生成中 A/B 使用同一身份，再次运行会新建一对测试身份：

```bash
venv/bin/python scripts/build_h5_handoff_samples.py
```

联调时使用同一对 A/B。每份约 54 KB，未调用生成模型。
