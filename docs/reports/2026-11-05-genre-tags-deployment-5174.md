# Genre/Tags 新版部署到 5174

2026-11-05。用户明确授权直接部署，并由作者启动新小说测试。分支 `codex/novel-quality-optimization`，部署当前已验收的工作树；未提交或推送。

## 已上线

访问 http://localhost:5174，刷新后从作品库「新建小说」进入：

1. 故事与素材：原创构想、参考创作或原作重写。
2. Genre 与 Tags：一个 Genre，至少一个关联 Tag。目前内置 Romance 和 10 个关联 Tag。
3. 篇幅与偏好：设置语言、章节偏好与每章规划上限，再开始头脑风暴。

Markdown 资料库、来源导入与完整提供范围分析、封面可选参考图、动态分类导出均随新版上线；独立 M3 阅读评议已移除。原作品、讨论、封面和模型配置保留。没有替作者启动真实创作、来源阅读或出图。

## 部署验证

| 检查 | 结果 |
|---|---|
| 部署前任务检查 | 19 作品、9 讨论，21 项只读 HTTP 检查，无活动任务；后端无模型子进程 |
| 数据保护 | 31,814 既有文件 SHA；3,014,717,440 字节备份 tar；一致性 SQLite 备份，完整性检查通过 |
| 构建 | 后端与前端 Docker 镜像构建通过；删除已退役 tiktoken 预热命令；构建上下文排除 source_books |
| 部署脚本回归 | 27 项通过 |
| 实际 Python 3.11 镜像回归 | 188 项通过；断网、无生产数据/凭据，使用临时模板和模拟模型配置 |
| 容器 | `docker compose up --detach --no-build --wait` 完成；前后端均 healthy |
| 运行文件 | 270 个后端、76 个前端文件与工作区发布产物 SHA 一致 |
| API | 来源书与封面参考接口存在，M3 阅读评议接口已移除 |
| 原有数据 | 部署后 31,814 文件及 studio_settings.json 均未改变 |
| 浏览器 | 实际 5174，1600px/390px 三步创建、Genre/Tags 必选、未完成来源门槛、资料库显示通过；无 JS 错误、横向溢出或写请求 |
| 模型配置 | Codex TOML 语法有效、认证文件存在；保持 gpt-6-astra / max / 900s，不进行远程模型调用 |

首轮隔离镜像测试因只读模板目录及无默认模型配置失败；为 QA 补齐 tmpfs 和虚拟 key 后 188 项通过。这没有修改生产配置，也不是通过放宽业务校验解决。

证据：[运行文件与数据核验](evidence/2026-11-05-genre-tags/deployment-5174/verification.json)、[容器测试](evidence/2026-11-05-genre-tags/deployment-5174/container-tests-final.log)、[浏览器记录](evidence/2026-11-05-genre-tags/deployment-5174/browser-check.json)、[组合选择](evidence/2026-11-05-genre-tags/deployment-5174/creation-combination-1600.png)、[开始前页面](evidence/2026-11-05-genre-tags/deployment-5174/creation-ready-1600.png)。完整开发验收见[实现报告](2026-11-05-genre-tags-implementation.md)。

## 回退与边界

私有部署审计和数据备份：`/private/tmp/novelos-genre-deploy-20261105/`，未提交到仓库。旧容器引用的原镜像 ID 已从 Docker 索引丢失，因此从仍运行的容器提取精确应用文件，基于可用的旧版本基础镜像重建回退镜像，并验证后端导入及 Nginx 配置可用；未将环境密钥或数据挂载打入镜像。

- 回退镜像：`novel-os-backend:rollback-genre-tags-20261105`、`novel-os-frontend:rollback-genre-tags-20261105`。
- 回退覆盖文件：审计目录内 `rollback.compose.yaml`。使用前须重新检查活动任务与新版已写数据，不能盲目覆盖数据库。
- 三项宿主小说技能按标准部署同步，内容已一致，无新变更。
- H5 下游尚未升级，动态分类 ZIP 仍需按[交接手册](../design/genre-tag-studio-v1/h5-taxonomy-handoff.md)完成对接；没有开发 H5 预览。
- 工程验收不等于文学与实际图像质量验收。本次没有付费模型请求，后续由作者启动新书验证。
