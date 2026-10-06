# The Last Golden Spire｜正式创作交接

作者已于2026-10-04回复“同意”，整体批准A—E设计。正式提示词：[The Last Golden Spire.md](../../prompt/The%20Last%20Golden%20Spire.md)。

固定范围：一部长篇，三卷各20章，共60章；英文正文每章约1200词，章正文合计72000词；开篇导语另计120—180词。60章包含承接原稿的第1—4章。面向美国50—65岁女性读者。结局不复婚，父亲责任和女儿自己的生活分别兑现。

## 启动命令

在本机终端执行以下命令；本次交接尚未运行生成模型。

```bash
cd /Users/max/OpenSource/Novel-OS
NOVEL_OS_PROJECT_NAME='the-last-golden-spire' \
NOVEL_OS_TITLE='The Last Golden Spire' \
NOVEL_OS_GENRE="Women's Fiction" \
NOVEL_OS_CHAPTERS='60' NOVEL_OS_WORDS='72000' \
NOVEL_OS_EDIT_MODE='developmental' NOVEL_OS_APPROVAL='auto' \
NOVEL_OS_QUALITY_POLICY='evidence_v1' \
NOVEL_OS_OUTPUT='markdown epub' \
./deploy.sh novel './prompt/The Last Golden Spire.md'
```

`approval auto`启用有次数上限的自动质量修复；`evidence_v1`以审核证据推进章节，并在恢复运行时保留已经提交的章节。启动器的现有帮助已核验：默认最多5次暂时性失败重试、2次质量修复。它复用健康的后端，不要求先重启服务。

预期输出目录：`docker-data/projects/the-last-golden-spire/outputs/`。真正启动后保存运行ID；恢复时使用该次运行实际返回的ID执行`novel-status`、`novel-resume`或`novel-retry`，不要猜测ID。

## 已完成的校验

- 正式Prompt的标题、语言、读者、POV、篇幅和前提均由本地`ingest_prompt(..., generate_author=False)`实际解析；仅写入临时校验项目，模型调用为0。
- 三卷范围、原生卷合同、商业故事合同、schema-v2封面交接均验证通过。封面交接是元数据，没有生成图片。
- 60章和120个场景与批准设计逐项一致；七项原稿修补、资金和时间线、六名主要人物结局、十项兑现承诺均保留。
- 两段简短英文声音校准标注为非正史，不计入正文或完成章数。
- 私人源路径、上一部小说内容和素材审计未注入生产Prompt；当前项目已授权的事实保留，四份原文件哈希未变。

完整记录：[prompt.validation.json](prompt.validation.json)。解析和规划校验不代表正文已经生成，或已通过编辑、连续性、声音及最终结局审核。

相关设计：[已批准A—E与60章章节表](long-form-design.approved.md) · [结构化批准设计](long-form-design.approved.json)。
