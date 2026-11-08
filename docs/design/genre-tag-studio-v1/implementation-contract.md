# 实施边界与接口

2026-11-05 · 已实现并完成工程验收；未部署到5174、未进行真实模型文学验收。用户明确无需历史流程兼容和H5预览，因此不实现旧知识迁移、旧M3归档页面及H5展示界面。原始历史书籍/资料文件没有删除。

## 单一职责

| 模块 | 唯一职责 |
|---|---|
| `core/creative_types.py` | Markdown解析、草稿/启用版本、关联与冲突、不可变选择、阶段编译 |
| `core/publication_taxonomy.py` | 私有知识到公开分类的确定性投影和公开revision |
| `core/knowledge_intake.py` | 网页/CLI骨架与选择一致性预检 |
| `core/creative_execution.py` | 沿用现有冻结执行器，把阶段指导交给各写作Agent |
| `core/source_library.py` | 来源目录/ZIP验证、私有原件及清单哈希 |
| `core/source_study.py` | 所提供全文的分组读取、检查点、综合与显式恢复 |
| `core/reference_assets.py` | 工作区/项目所属不可变PNG/JPEG参考资产 |
| `core/cover_poster_planner.py` | 完整骨架、选中知识、可选图像的一次联合规划 |
| `api/poster_image_service.py` | 原有生成/编辑/精修执行器；参考图必须实际传入 |
| `core/delivery_package.py` | 固定目录、EPUB、classification及SHA一致性，不由模型拼包 |

没有新增第二套小说执行引擎、知识转规则模型或完稿逐章评议。章节的Editor/Continuity/Style、reader_investment、篇幅、结局检查仍在原链路。新建时一个Genre＋至少一个关联Tag是服务端约束，不只依赖按钮禁用。

## 实际HTTP接口

- `GET /api/creative-types`：全部条目摘要；前端按kind/genre_ids筛选。
- `GET /api/creative-types/template`、`POST /preview`：模板和阶段投影；零模型调用。
- `GET /api/creative-types/{id}`、`GET /{id}/markdown`：MD、历史启用版本与下载。
- `POST /api/creative-types`、`POST /{id}/draft`、`POST /{id}/activate`、`POST /{id}/disable`、`POST /{id}/delete`：显式revision与operation_id，幂等、锁内比较、原子持久化。删除为不可复活的tombstone；冻结书籍不受影响。
- `GET/POST /api/source-books`：列表/上传ZIP或目录；导入不调用模型。相同内容哈希复用。
- `GET /api/source-books/{id}`、`GET /{id}/cover`：清单、进度与原图。
- `POST /api/source-books/{id}/analyze`、`POST /{id}/cancel`：显式启动/恢复/暂停。`restart:true` 将旧分析移入历史后按当前模型重读；不能把两种模型检查点混用。
- `POST /api/projects/{id}/poster/reference`、`POST /reference/selection`、`GET /reference/{asset_id}`：上传、替换模式/移除、私有图片。其余封面任务仍走原poster API。
- Workshop沿用既有API，新增 `creative_types` TypeRefs和可选 `source_book`；服务端冻结组合与引用，不信任前端拼出的prompt。

资料库与来源库按现有workspace隔离。参考图按project隔离。持久文件使用原子写入，知识和来源执行锁覆盖进程/线程。来源worker复用当前单API进程JobRunner部署模型，不支持多worker分布式调度；服务重启后陈旧运行记录变为可显式恢复的interrupted，不假装远端仍在思考。

## 创作与来源的绑定

写作快照放入本书 `outputs/input/knowledge.json`，来源分析用途放入 `outputs/private/source-context.json`；书籍输出只以作者采用的骨架为准。导入的故事、未读后续、创作建议和本书canon分开。选择、模式或构想修改沿用已有基础设定的明确重启过程，不隐式覆盖历史决定。

SourceStudy按UTF-8容量分组覆盖每份Synopsis与章节全文，最多2N−1次正常调用（N读取组＋N−1合成），不是每章一次。实际执行前显示组数和最大调用数；暂停保留完整成功响应。请求绑定来源SHA、模型配置、系统/请求SHA；恢复重用匹配结果。已完成分析嵌入创建快照，后来重新分析不会改写旧书。

来源阅读用量保存在来源库，按brainstorming类别记录；它是可供多部作品复用的资料准备成本，不重复摊进每本书已有费用合计。失败没有用量回执时保留未知，不伪报零费用。

## 可核验的保证

代码保证：选择关联与身份、每阶段输入完整性、快照不漂移、未读不可使用、真实图像传输、技术失败可恢复、公开包不夹带私有资料。文学判断仍通过正常讨论/编辑和作者选择，不以JSON字段或测试数量证明精彩。

同Genre不同Tags、同组合不同构想应该产生不同的压力、选择、代价和回报；这是下一阶段真实小样本验收目标。建议3组组合×2个独立构想，包含温暖/喜剧而非全是复仇；先比较方案和开篇，再决定全书用量。此轮未启动该真实测试。

## 旧路径退出

M3 UI、API、worker、提示词、恢复分支及相关独立依赖已删除；旧API返回404，不能启动或恢复。正文阅读及历史费用识别保留。复杂profile JSON的编辑/运行资源由唯一Markdown合同替换，不自动猜测转换旧知识。来源没有授权成为新书事实，书名或来源ID不作为生成逻辑开关。

H5旧文件解析仅用于既有协议读取；新Genre/Tags固定导出动态分类，未升级H5明确不兼容，不能把新标签硬塞旧目录。H5预览没有开发。实际交接见 [H5手册](h5-taxonomy-handoff.md)，验收和部署状态见 [开发报告](../../reports/2026-11-05-genre-tags-implementation.md)。
