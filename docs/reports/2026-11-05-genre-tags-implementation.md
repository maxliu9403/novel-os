# Genre/Tags、参考创作与封面参考图开发交付

2026-11-05，分支 `codex/novel-quality-optimization`。用户已批准整体实现，明确不需要H5预览和历史创建流程兼容。**本次完成代码和工程验收；未部署5174，未启动真实模型创作/出图。** 原工作区存在其他未提交功能，本次未提交、推送或覆盖它们。

> 后续部署：已按用户授权部署到5174，见 [部署与验收记录](2026-11-05-genre-tags-deployment-5174.md)。下文“未部署”描述的是本报告最初的代码交付时点。

## 已实现

1. **唯一Markdown知识库**：复杂profile编辑器及JSON资源替换为Genre/Tag文档。六必要分节＋可选封面表达；版本、SHA、并发revision、幂等、停用/删除、导入/下载、投影预览。生产种子为Romance＋10个经过语料研究的Tag；设计样例不再维护第二份运行内容。
2. **统一创建与传递**：构想/导入→一个Genre及关联Tags→语言篇幅。服务端强制一个Genre、1–20个关联Tag；切换Genre先确认不兼容项，共用Tag保留。冻结资料进入讨论、骨架、章卡、正文、既有编辑、Synopsis、封面；改资料不改变已启动书籍。网页和CLI共用骨架/快照一致性检查，动态分类缺少知识快照会在调用前明确失败。
3. **来源库与完整提供范围分析**：目录/ZIP结构、安全边界、原件SHA、Synopsis和全部章节按容量读取、检查点、取消/恢复/重新分析。模型配置改变不能混用旧检查点；新分析归档旧结果。参考创作与原作重写有明确模式，不以overview代替原文，未读后续始终未知。
4. **封面参考图**：可选单图上传或来源封面带入、用法选择、移除。一次真实视觉规划同时返回拆解与四方向，随后真实edit出图；原作重写有图默认二创。仍允许宣传式艺术构图。候选精修以该候选原画为准；四候选、直接选用、失败重试和排版交付保留。
5. **M3退出**：独立全书阅读评议UI、API、worker、提示词和恢复代码删除，旧路由404；正文阅读、正常写作质量环节和历史费用记录保留。没有把M3改名后塞回生成流程。
6. **H5动态分类导出**：同一代码对象生成分类sidecar、EPUB内嵌、publication引用和散列。仍保持Manifest V4、novel-h5-import.v1、ready及全部原尺寸/章节/作者规则。公开目录不含私有知识和来源；H5展示预览未开发。

顺带修复验收发现的实际边界：Markdown尾部空白不可被Pydantic自动剥离（否则原文SHA与编辑内容不一致）；骨架交付检查点统一按deliver阶段计算，避免从讨论进入交付后错误判定缓存不匹配；未完成来源分析在创建时返回明确409而非500；没有参考图观察时拒绝伪造完成的视觉分析。

## 工程结果

| 验证 | 结果与证据 |
|---|---|
| 后端跨模块回归 | **997 passed, 1 skipped**；Workshop、冻结执行、source、poster、publication、H5、pipeline与CLI。跳过项要求显式固定的外部Go导入器checkout，未视为通过。[日志](evidence/2026-11-05-genre-tags/backend-tests.txt) |
| 前端全量 | **602 passed / 55文件**。[日志](evidence/2026-11-05-genre-tags/frontend-tests.txt) |
| 静态检查与生产构建 | ESLint、TypeScript/Vite构建通过；新增Python模块的F/E9/I检查通过；git diff --check通过。[构建](evidence/2026-11-05-genre-tags/frontend-build.txt) |
| 独立CLI进程 | 在清除PYTHONPATH的真实进程执行run --knowledge --dry-run；intake成功并保存相同知识快照，零模型调用。[记录](evidence/2026-11-05-genre-tags/cli-dry-run.txt) |
| 真实来源目录兼容 | 本地200/200 Romance目录通过inspect_bundle，覆盖1743列章文件；未改来源，未追加语义模型分析。[逐书记录](evidence/2026-11-05-genre-tags/source-import-compatibility.json) |
| 图像端到端夹具 | 实际PNG字节进入一次规划、四次edit，实际referenceSHA核对，完整图可选用，坏图/篡改拒绝；使用假provider，不假装真实出图验收 |
| H5产物 | 合法ZIP通过；篡改分类而未更新SHA的包返回file_digest；EPUB/sidecar同源；私有标记不进入包。初次/重复/非法包3例计划已提供 |
| 浏览器 | 隔离5202、真实生产构建、1600px与390px：知识预览/保存/刷新；新建必选/返回/来源门槛；封面上传/切换模式/刷新/移除；无页面JS错误与横向溢出 |

浏览器截图：[知识库](evidence/2026-11-05-genre-tags/knowledge-desktop.png)、[组合选择](evidence/2026-11-05-genre-tags/creation-step2.png)、[来源导入](evidence/2026-11-05-genre-tags/source-import.png)、[封面参考图](evidence/2026-11-05-genre-tags/cover-reference-desktop.png)。5202仅使用临时库，真实付费入口封锁；验收后停止临时服务。

## 运行与后续验收边界

- 5174当前实例没有重启或替换。部署前需核对活动任务，避免中断正文/封面任务；本次没有恢复任何旧评议。
- H5尚未升级：新动态v2包不能声称旧导入器已接受。提供[最终交接手册](../design/genre-tag-studio-v1/h5-taxonomy-handoff.md)、[真实夹具](../examples/h5-import/dynamic-taxonomy/README.md)；H5代码和线上数据库未修改。
- 正式发行前设置稳定的 `NOVEL_OS_PUBLISHER_NAMESPACE`，H5绑定认证来源。默认novel-os.local是开发值。
- 来源worker复用当前单进程JobRunner，磁盘检查点可恢复；多worker分布式调度不属于现有部署能力。来源阅读用量保存在来源库，作为共享资料准备成本，不重复计入每本书合计。
- 当前没有自动旧profile迁移，没有新M3归档页面；旧文件/书籍原数据保留，不能宣称旧未完成讨论已无损升级。
- 工程合同保证输入、执行与产物的一致性，不能保证每本小说文学质量。建议作者批准预算后，用3组组合×2个独立构想做方案/开篇小样本；包含喜剧或甜蜜组合，比较类型辨识、行动代价、情绪回报和故事差异，再决定全书测试。

实现文档：[总体说明](../design/genre-tag-studio-v1/README.md)、[Markdown合同](../design/genre-tag-studio-v1/markdown-contract.md)、[模块/API](../design/genre-tag-studio-v1/implementation-contract.md)、[来源合同](../design/genre-tag-studio-v1/source-import.md)、[封面合同](../design/genre-tag-studio-v1/cover-prompt-contract.md)。
