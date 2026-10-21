# 创作方法增强 P0 / P1：实现与使用

日期：2026-09-12。范围：已批准设计中的 **P0＋P1**；不包含 P2 主动改稿、P3 知识建模、P4 声音索引。

设计：[完整方案](../superpowers/specs/2026-09-12-narrative-method-integration-design.md)。

## 1. 本次交付

- 两份固定版本英文方法资料：功能性表达；选择、回报归属与阅读期待。
- `NarrativeMethods.compile/validate`：只读评审输入编译与精确引文核验。
- `MethodReviews`：运行快照、私有输入/响应/报告、持久化请求预留、显式重评、保留表达记录。
- 全书 Pipeline 在商业修订后、正式晋升前评审最新不可变候选。既有角色提示、正文生成及晋升合同保持原样。
- Studio 章节页折叠的“写作评审 · 只读”：运行/稿件版本选择、后台任务、报告、调用统计、历史原稿高亮。
- 新建作品页明确显示只读评审、范围与额外模型调用，可取消勾选。
- 新产物仍经现有 EPUB/H5 exporter；无新增 H5 必填字段，不修改 ai-novel-h5。

## 2. 如何启用

新建作品默认 `advisory`，创建页可选择关闭。仅适用于明确为英文、具有已批准商业合同/免费窗口的全书运行。

单阶段写作不会静默变成全书运行。没有明确免费窗口时显示 `approved_free_trial_required`，不推测第 3 或第 4 章。

现有 CLI 新增：

```bash
# 查看参数，无模型请求
python core/orchestrator.py run --help

# 在原 run 命令的其他参数上添加以下一个选项
--method-mode advisory
--method-mode off
```

省略该参数时，新运行继承项目策略，缺省为 advisory。启动日志显示模式及额外调用上限。`--dry-run` 仍只处理原有 intake，不调用评审模型。

运行启动后策略冻结。Studio 修改的是**未来运行**默认值，不改变当前任务。改造前旧书和旧 run 没有迁移/补审任务，文件不删除。

P1 不接受 `enforced`、自动改稿权限或其他作用范围。原有人工编辑能力继续可用。

## 3. 当前策略合同

`GET /api/projects/{project_id}/method-policy` 返回带 `sha256` 的记录。首次未保存时修订值为空字符串。

`PUT` 必须提交 `expected_revision`，并发过期返回 409。实际 P1 数据如下（后续主动增强使用显式内部 schema 演进，而不是透传字段）：

```json
{
  "expected_revision": "",
  "policy": {
    "schema": "novel-method-policy.v1",
    "mode": "advisory",
    "method_ids": ["en-functional-prose.v1", "en-agency-payoff.v1"],
    "scope": "free_trial_window",
    "repair_policy": "none",
    "review_max_chars": 16000,
    "context_max_chars": 16000,
    "schema_repair_attempts": 1,
    "review_timeout_seconds": 300
  }
}
```

预算按 Unicode code points；与正文章节字数目标无关。正文或合同超预算返回 `incomplete`，不首尾截取。普通评审单次调用超时默认 300 秒，策略可设 30–1800 秒；底层 SDK 自有重试可能增加总等待。

模型使用已有 **Judge 路由**。需显式模型名称才能冻结；不猜测 CLI 的默认模型。快照保存方法内容/哈希、实现哈希、配置、语言、免费窗口及合同。

模型/推理强度/有效 endpoint 按快照恢复；原连接 ID 用于读取当前凭据，凭据本身不进入快照。原连接被删除、endpoint 改变或凭据缺失时明确失败，不切换到其他连接/环境账号。Azure 单独冻结 endpoint 和 API version。密钥轮换后可使用原连接的新凭据。

## 4. 评审报告如何解释

- `valid`：结构、规则覆盖和引文校验合格；模型意见仍可能错误，不等于小说质量通过。
- `invalid`：报告结构或引文不合格，最多自动修正一次。
- `unavailable`：请求/配置失败，或中断导致请求结果不确定。
- `incomplete`：完整输入超预算等覆盖问题。
- `not_applicable`：语言或已批准范围不适用。

所有报告 `blocking=false`；模型自称 major 的建议也不授予晋升阻断权。报告不进入既有 promotion receipt。

引文使用完整不可变 UTF-8 稿件解码后的 Unicode code point 下标 `[start,end)`。唯一匹配可以纠正定位；重复引用的错误位置不会默认选第一段。前端按码点切分而非直接使用 UTF-16 String.slice；只高亮 SHA 已验证的历史原稿，不把旧偏移放到新稿上。

免费窗口相关方法只检查本章承担的功能；完整窗口的既有商业门禁继续独立运行。P1 不伪称单章评审证明全窗口已兑现。

## 5. 运行恢复与人工重评

- 自动评审一般一次 `complete()`，报告结构修正最多再一次；认证、容量、超时不进入文学修复循环。
- 在发送请求前持久化预留，响应落盘后再验证。响应已保存时恢复复用，不重复请求。
- 发送后结果未知的请求显示 `interrupted_request_uncertain`，不自动重发。
- 同一份评审只由一个请求执行；只锁定该 review_id，不让正文保存或未来策略设置等待模型。
- 主流水线保存 `method_snapshot_started/method_lock_sha256`。恢复丢失快照时记录方法不可用，不按新设置重建；原有正文流程继续。
- 项目默认策略损坏时，本次运行关闭方法评审并记录 `methods.policy_unavailable`，不覆盖损坏文件，不报告评审通过。
- 失败报告可点“重新评审此版本”，**这是一次明确的新模型调用**。`retry_of` 引用旧报告；同一次重评重复提交仍幂等，旧报告不覆盖。完整输入绑定变化应创建对应新版本的评审。

`usage.model_calls` 统计已确认调用完成接口的次数；发送状态未定的预留单独计入 `uncertain_model_calls`，不宣称已发送或免费。`transport_attempts`、token 和费用在当前适配器未返回时为 null/未知。尤其 Codex CLI/SDK 内部重连不是一次方法调用，不把未知底层请求数宣称为 1，不把未知费用记为 0。人工重评按报告 lineage 分开计数。

Web 任务仍使用现有内存 JobRunner，没有建设持久化 worker。重启可能丢失 Web job 展示，但已持久化输入、请求和报告保留。

## 6. Studio 内部接口

- `GET/PUT /api/projects/{id}/method-policy`
- `GET /api/projects/{id}/runs/{run_id}/method-lock`
- `GET /api/projects/{id}/chapters/{n}/method-reviews?revision_id=...`
- `POST /api/projects/{id}/chapters/{n}/method-review`
  - 请求：`run_id`、`revision_id`；显式重评可加 `retry_of`。
  - 返回 202 和既有 JobRunner 任务；按 `/api/jobs/{job_id}` 查询。
- `GET /api/projects/{id}/method-reviews/{report_id}/source`
- `POST /api/projects/{id}/method-reviews/{report_id}/keep`
  - 请求：`revision_id`、`finding_index`、可选 `note`。
  - 仅记录保留表达，不改正文或事实。

全部复用既有项目访问控制、删除围栏和任务注册。正文版本需属于当前项目/章节。P1 没有 method-repair 接口。

## 7. 私有产物与 H5

```text
outputs/quality/methods/
  policy.json
  runs/<run_id>/lock.json
  critiques/<report_id>/
    assembly.json
    requests/<attempt>.json
    report.json
  decisions/<decision_sha>.json
  locks/<review_id>.lock
```

这些文件含私有原稿/评审材料，仍按项目数据管理；显式 H5 打包 allowlist 不包含它们。

保持既有 manifest v4、EPUB 正文、独立 Introduction、分类/分卷映射、书章身份、封面独立更新和摘要算法。现有封面规则无变化。

新增回归用真实打包器对比：仅新增方法快照和报告时，ZIP 成员集合、各成员字节和 manifest 完全相同。ZIP 容器本身的时间戳不作为公开内容恒定的替代断言。

**引擎合同回归不等于 H5 联调。** 当前 H5 导入器/校验器未在本轮执行，实际导入验收仍是正式发布条件；不以修改 H5 来消化变化。

## 8. 回退和部署边界

- 首选把未来运行模式设为 off，保留快照/报告供查看。
- 在途策略不直接改写。P1 暂未实现设计中 P2 的 continuation-run 切换入口。
- 本轮没有改变正式稿和 canon schema；仍不建议用旧程序接管不认识新 manifest 字段的在途任务。程序回退应在任务停止的明确窗口配合完整快照执行。
- Dockerfile 已复制 `resources/`；更新技能目录本身不等于启用该引擎能力。
- 本轮开发不自动部署、重启已有服务、创建小说或执行真实英文 A/B 生成，不包含提交和推送。

## 9. 已审查事项及待验收

已作两轴只读代码审查，并修复快照丢失重建、跨模型持项目锁、Azure endpoint 漂移、缺原连接凭据时回退等问题。

自动测试覆盖：纯编译/引用核验、无效结构预算、Unicode、快照/报告篡改、中断、显式重评幂等、并发策略更新、慢后台评审期间 HTTP 写入和删除围栏、原稿来源、完整 pipeline off/advisory 失败对照、真实 ZIP 私有侧车隔离。

仍需后续验证：真实英文编辑/目标读者盲评、真实模型成本与耗时、当前 H5 导入器联调。P2 前不宣称读感或付费转化已提升。

## 10. 本地验证记录

最终代码验证结果：

| 检查 | 结果 |
| --- | --- |
| 后端全量 pytest | 1,533 通过；4 条现有 multiprocessing fork 弃用警告 |
| 新增方法与接口测试 | 33 通过（包含在后端全量中） |
| Web 全量 Vitest | 17 个测试文件、107 项通过 |
| Web TypeScript / Vite 构建 | 通过 |
| Web ESLint | 通过 |
| `git diff --check` | 通过 |

前端一次与后端全量并行的验证中，既有 ChapterView 测试等待 `Final manuscript` 超时；随后该文件单独复测及前端全量复测均通过。未修改该测试、断言或等待阈值。并行负载是可能因素，尚未证明为唯一原因。

测试使用模拟模型；以上结果不代表真实模型质量评测、生产部署或 H5 联调通过。机器可读计数、命令、资源与实现文件 SHA 见 [验证记录](2026-09-12-narrative-methods-p1-verification.json)。
