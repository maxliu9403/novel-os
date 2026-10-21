# The Hours I Kept｜创作交接

用户已于 2026-10-04 确认 A 方向及 A–E 全部设计。本次交付为可启动 Novel OS 的正式创作提示词，正文尚未生成。

- [正式提示词](../../prompt/The%20Hours%20I%20Kept.md)
- [已确认故事设计](design.proposal.md)
- [机器可读设计](design.proposal.json)
- [最终提示词验证记录](prompt.validation.json)

## 已锁定的创作约定

美国市场，50–65岁女性，英文正文。单本24章，每章约1,200词，章节合计目标28,800词；另有120–180词英文导语。Helen Mercer，58岁，以第一人称过去时讲述餐饮企业退出中的婚姻背叛、尊严反击与中年重建。

八周经营退出、三次家庭餐试做、最多四十小时独立有偿交接、一年后尾声。七条核心兑现和六名主要人物的结局已有稳定编号；前3章完成试读微弧，第21–24章集中兑现结局及生活余波。平台付费边界未锁定。

## 验证范围

本地原生 intake 在独立临时项目中执行，设置 generate_author=False，未调用写作模型。分类、篇幅格式、商业故事与封面 v2 契约通过原生解析；整份提示词原样保存。自定义故事契约、结局引用、章节与预算另外进行 JSON、编号与算术核验，共41项通过。

自定义 audience_profile、workshop_trace、opening_contract、story_lead_contract 与 ending_contract 不由 prompt_intake 自动全部展开；提示词明确要求 Architect 将它们写入 foundation 和相应工作文件。解析通过不等于之后的模型必然遵守，也不等于正文、结局质量已经通过。

独立审查覆盖素材隔离、24章因果、经营现实性、人物情感与结局一致性。第5章“周日两小时”已明确为D6星期二谈话时约定下个周日，不改变既定事件。正文审核与实际结局检查仍为 not_run。

## 启动命令

在终端执行以下命令将开始小说生成。参数明确锁定项目、篇幅、结构编辑、证据审核及导出格式；标准输入关闭以使用这些参数而不进入终端交互设置。

```bash
cd /Users/max/OpenSource/Novel-OS
NOVEL_OS_PROJECT_NAME='the-hours-i-kept' \
NOVEL_OS_TITLE='The Hours I Kept' \
NOVEL_OS_GENRE="Women's Fiction" \
NOVEL_OS_CHAPTERS=24 \
NOVEL_OS_WORDS=28800 \
NOVEL_OS_EDIT_MODE=developmental \
NOVEL_OS_APPROVAL=auto \
NOVEL_OS_QUALITY_POLICY=evidence_v1 \
NOVEL_OS_OUTPUT='markdown epub' \
./deploy.sh novel './prompt/The Hours I Kept.md' < /dev/null
```

`./deploy.sh novel --help` 已实际运行成功。正式生成命令没有执行；未检查写作模型凭据或启动容器。项目名在验证时未占用。运行产物预期位于：

```text
/Users/max/OpenSource/Novel-OS/docker-data/projects/the-hours-i-kept/outputs/
```

默认采用5次暂时性错误重试、2轮质量修复；auto 不代表无限循环或质量问题自动获得通过。遇到超出次数的问题，运行会保留检查点及报告。

启动后可查看状态；有多个任务时使用输出的具体 RUN_ID：

```bash
./deploy.sh novel-status RUN_ID
./deploy.sh novel-resume RUN_ID
./deploy.sh novel-retry RUN_ID
```

封面仅提供了正式交接数据；本次没有生成图片。没有修改 Novel OS 框架代码、现有项目或部署配置。

