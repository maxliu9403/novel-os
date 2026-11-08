# 动态分类联调夹具

这些是合成的两章技术样例，不是小说质量样本，不包含用户书籍或第三方全文。

- `valid-dynamic-taxonomy.zip`：Manifest V4、ready、EPUB正文、动态分类；引擎合同检查通过。
- `invalid-classification-hash.zip`：在合法包内修改公开定义但保留旧SHA，预期 `file_digest`。
- `classification.example.json`：完整v2分类对象。
- `acceptance-plan.json`：初次导入、同包重复、非法包拒绝三例。external_import_status仍为pending。

从仓库根目录重新生成（不调用模型）：

```bash
venv/bin/python scripts/build_dynamic_taxonomy_samples.py
```

验收计划按本机绝对路径生成。移动仓库或复制到H5环境后，重新生成或调整计划中的本地文件路径；不要修改ZIP内部内容。

协议见 [H5交接手册](../../../design/genre-tag-studio-v1/h5-taxonomy-handoff.md)。现有静态H5验证器预期不支持动态v2；不能将该拒绝解释为ZIP损坏或跨域错误。
