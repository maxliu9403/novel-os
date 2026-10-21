# 结局证据漏记修复与恢复

任务：`c97e8ae5aef6`，作品《She Still Loved Them, but the Choice Was Hers》。原始诊断保留在 [诊断报告](2026-10-14-ending-gate-c97e8ae5aef6.md)。

## 修复内容

- 统一 Scribe、Guardian 与调用模板的 `Payoff_Events`、`Arc_State_Updates`、`Ending_Evidence` 协议，修正重复字段前缀及报告闭合标签。
- 明确标记的错位字段可以安全归位；多块结局字段合并去重，不再被后面的空块覆盖。缺证据、非法状态、冲突 ID 等机器声明明确报 `ending_protocol`，不能静默丢弃。普通描述和人物备注不能直接升级为完成记录。
- 提前提供角色 ID、姓名及结局义务，让提前退场的配角在职责发生时记录结果。终局提供相关已提交记录的来源索引，避免把整部小说再次发给模型。未来合同要求本身不是完成证据。
- 增加独立的 `EndingEvidenceStore`，供审核已批准正文后补录历史缺失记录。结局检查只使用有效记录作临时证据投影，不改正文、canon、旧提案或批准凭据。
- 恢复检查点绑定审核记录、成员增删和来源版本；派生的 payoff ledger 不再是自己的检查输入。旧快照恢复后补录仍存在。

独立审查发现并修复了时序问题：较早章节的 `intentional_open` 不能永久否定后来完成的情节。本书 payoff_004 在第 7 章 open、第 8 章完成；payoff_007 在第 10 章 open、第 12 章完成。同章或更晚的明确 open 冲突依然拒绝，超出 `as_of_chapter` 的记录不参与当前判断。

## 补录合同与边界

记录位于 `outputs/state/ending_evidence_reviews/ending-review-<sha256>.json`，通过 `EndingEvidenceStore(project).record(payload)` 追加保存。

每项审核包括：审核者类型与姓名、时间、理由、结局合同 SHA、项目实例、义务 ID，以及来源章节、当前 Final revision/SHA、promotion key/receipt、原文与 Unicode 字符位置。存储层核验批准凭据、账本、正文投影和精确引用；这些核验确认来源真实性，语义判断仍由具名审核者负责。

记录不能覆盖原文件；重复提交同一 payload 幂等。修订须显式 `supersedes`，旧记录保留。合同或正文变更、引用不符、文件损坏、身份不符、重复活跃声明、与现有结果冲突，均会让 gate 明确失败。不能据此跳过未完成的主线、终章或其他质量要求。

## 验证

- 相关解析、证据存储、提示词、canon、promotion 与上下文测试：300 项通过。
- Pipeline orchestrator、runner、integration、CLI：151 项通过。两组模块无重复，共 451 项。
- 新恢复测试实际调用 `_stage`：建立旧快照、触发阻断、追加 review、改动 live state、冷加载 manifest 后走恢复分支，验证快照恢复且 review 生效。
- 使用正式作品的全新隔离副本，原始 gate 精确复现 6 条错误；补入 5 项逐条审核证据后 pass。实际 `PipelineRunner.retry(..., phase="ending.review")` 复用全部 15 章，结局和整书检查均完成，在 publication.copy 前主动停止测试。
- 隔离恢复核验 262 个正文/状态/提案/批准/产物文件保持，正式项目 836 个既有文件保持；模型调用 0 次。整书检查有历史伏笔警告，无 critical。没有把警告伪装成完全无问题。

证据与可重复运行脚本：`/private/tmp/novelos-ending-fix-20261014/`。`verify_recovery.py` 拒绝覆盖已有副本；其 review payload 依据已批准的第 3、7、8、9、12、13、14 章，包含精确引用及批准关联。

## 部署与正式恢复

2026-10-14 05:55（上海时间）后端部署完成。仅替换 7 个修复源码/模板，运行 SHA 一致；前端容器和镜像未变，两个服务 healthy。部署前两次确认 15 部作品无活动任务，并保留原后端回滚镜像、目标作品完整备份及 SQLite 在线备份。切换前后 23,012 个既有业务文件 SHA 保持。

后端镜像：`sha256:ba0226c6a309ced57c79f546e4cb11b852908de92de774dca24d64e2ae4684b9`。回滚标签：`novel-os-backend:pre-ending-evidence-20261014`。

正式目标已通过存储接口追加与隔离验证一致的 5 项审核，记录 `ending-review-84b4777452dfa39360c9d475faafe21e5e4c471cac3fb2a6a32c2daac3a2d9b3`，原 836 个文件保持不变。随后通过正常恢复 API 继续同一 run，后台 job 为 `f6cf163ae1114558810502c43058bde5`。

正式恢复已完成，run 状态 `completed`，`ending.review`、`book.check`、`publication.copy`、`compile`、`delivery.package` 全部 done。15 章全部复用；`outputs/feedback/book_completion_report.json` 为 pass、critical 空。262 个正文/状态/历史证据文件 SHA 保持。出版文案及其审核使用正常模型流程执行通过，没有伪造出版检查结果。

已生成 Markdown、HTML、DOCX、EPUB、PDF 和 `outputs/deliverables/book-package.zip`。最终只读核验：

- ZIP 186,898 字节，CRC 通过；28 个成员中 27 个清单文件逐项 size/SHA 与磁盘一致。
- ZIP SHA-256：`9db8ce5424b687137023be199d39b79a1f8339a578c964fd0dfddc2083a09155`。
- 5174 的任务 API 返回 completed、无失败阶段、无需恢复；下载 API 返回 `application/zip` 和正确的 `book-package.zip` 文件名，响应字节与正式 ZIP 一致。
- 本次未测试外部 H5 导入；本地 ZIP 完整性通过不代表接收端分类字典兼容问题已解决。

证据为 `production-delivery-result.json`，核验脚本 `verify_delivery.py`。未提交或推送 Git。
