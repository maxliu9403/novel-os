# 方法增强：P2 前验证工具

范围：承接已批准设计第 14–16 节，开发独立 A/B 试验与 H5 验收工具。P1 基线提交 `b7d1180`。不启用生产 enforced 模式，不调用真实模型，不修改 H5 项目。

## 已确认的测试 Interface

1. **独立试验**：冻结计划 → 显式预算执行 → 全部结果/盲评包 → 人工评审汇总。测试使用 fake 模型；覆盖公平输入、预算、中断、并发、完整窗口、证据绑定及失败保留。
2. **H5 交付契约**：对实际 ZIP 作只读预检 → 导入测试计划 → 核验 H5 提供的结果。覆盖身份、EPUB 定位、摘要、Introduction、独立更新及错误包；不以引擎自检冒充外部导入验收。

## 放行边界

- 初版 A/B 是固定设计、同模型的 **Scribe 生成阶段试验**，不是完整流水线效果评估。A 沿用冻结的现有 Scribe system prompt；B 在隔离副本中替换冲突配额并加入精选方法。其他角色和生产默认值不变。
- 3 个场景 × 每组 3 次 × A/B = 18 份窗口/片段；每份包含多个章节，实际模型调用预算按章节计算，不把 18 份当成 18 次请求。
- 全部输入、响应、失败和不确定发送均保留。不自动重试，不按好坏挑选样本。单次 complete 的底层传输重试和 token/费用未知时明确标注未知。
- 历史章节为显式固定上下文；窗口内部仅接续同组同次的前文，不串用另一组内容。盲评隐藏组别与方法，保留完整性/词数偏差供评审；秘密映射留在本地试验目录。
- H5 实测需由当前导入器提供版本、包 SHA、测试库观察结果。回执核验只能验证这些外部声明的一致性，不证明测试真实执行；保持来源标识。
- 合成 H5 样包验证真实编译/打包路径，不冒充完整小说生成和晋升验收。原有三份 80 章样包只读保留。

## 1. 已实现的工具

- `core/method_experiments/`：严格输入、隔离目录、冻结计划、请求日志、预算、恢复、完整配对盲评及编辑意见汇总。
- `scripts/method_experiment.py`：`plan / run / status / export / assess`。
- `resources/method-experiments/english-pilot.example.json`：三份原创英文合成设计，800–900 英文词/章；3 章免费窗、4 章免费窗、12→13 章卷界。
- `core/h5_acceptance.py`：只读 ZIP 预检、局部更新语义检查、实际 H5 回执一致性核验。
- `scripts/h5_acceptance.py`：`inspect / plan / verify`。
- `scripts/build_method_validation_samples.py`：生成新目录内的 16 个交付测试用例；不重建原有基准包。

P1 只读评审、正式写作角色提示、Promotion、编译及打包器不变。`ModelRouter` 仅新增公开配置快照读取函数；P1 runtime 字节保留，避免纯函数提取使旧方法快照失效。

## 2. A/B 使用步骤

在仓库根目录执行，使用项目虚拟环境。首先复制并编辑示例中的 `model`；填写现有连接 ID、明确模型、推理强度、输出 token 上限和超时。API 模型还需填写实际 endpoint；URL 中不放 key、用户名、查询参数。密钥仍由原连接保管，运行前从原连接读取。

```bash
cp resources/method-experiments/english-pilot.example.json /private/tmp/english-pilot.json
# 编辑 /private/tmp/english-pilot.json 中的 model 配置；示例中的 REPLACE_* 不是实际模型。

venv/bin/python scripts/method_experiment.py plan \
  --root /private/tmp/english-pilot-lab \
  --spec /private/tmp/english-pilot.json
```

计划不构建模型客户端，不读取连接凭据，也不验证连接可用性。标准示例显示 **18 份窗口/片段、最多 54 次 complete 调用**。这不是 18 次请求，也不是费用上限。相同目录的计划冻结；更改输入、方法、模型或执行依赖需要新目录。

**确认预算后才执行下一条：**

```bash
venv/bin/python scripts/method_experiment.py run \
  --root /private/tmp/english-pilot-lab \
  --allow-model-calls --max-model-calls 54

venv/bin/python scripts/method_experiment.py status --root /private/tmp/english-pilot-lab
```

- 可先使用较小上限，例如 6；该上限是**实验全生命周期累计**值，再运行时不会清零。增大上限属于一次新的明确确认，仍不超过冻结计划。
- 同一目录的执行串行化；两次命令不会生成同一章节两遍。
- 发送前记录预留。响应落盘后才解析，恢复复用已保存响应。发送未知的请求保持 uncertain，不自动重发；该组后续章被阻止，另一组可以继续。
- 请求失败、输出无效、上下文超限都保留，不重新采样。完整输入超限时不截首尾。超长字符串响应单独完整保存于 `responses/`，附 SHA 和长度，标记解析预算超限。
- 正文去除 HTML 元数据与状态块，不替换标点或自动润色。词数按编译器正文段落的 Unicode 空白分词统计；超欠长只标记，不凑字数。事实/POV/英文自然度仍需真人评审。
- 冻结本工具及正文提取、计词、模型路由等执行依赖摘要；升级这些代码后旧实验明确报错，使用旧代码重现或新建实验，不混用解释逻辑。
- 模型服务实际权重、底层 SDK 重试和硬件随机性不是种子能固定的。种子只控制执行与左右顺序，不宣称输出确定。

### 盲评与汇总

```bash
venv/bin/python scripts/method_experiment.py export --root /private/tmp/english-pilot-lab
# 将输出的 blind-review.zip 分别交给两位独立英文编辑。
# 两位编辑各自填写 review-template.json，使用不同的匿名 reviewer_id。

venv/bin/python scripts/method_experiment.py assess \
  --root /private/tmp/english-pilot-lab \
  --reviews /private/tmp/editor-one.json /private/tmp/editor-two.json
```

所有计划内尝试终止后才导出。失败和不确定样本保留占位与状态，不从分母删除。ZIP 只含正文、共同事实/细纲、格式/词数状态和空评审表；不含模型、方法、组别或秘密映射。`plan.json` 和其他实验目录文件留给协调人，**不要把整个目录交给盲评编辑**。

每份评审必须覆盖全部配对，填写自然度、人物声音、因果、局部价值、阅读意愿 5 项的 1–5 分、偏好与说明。严重问题附精确章号、引用和 Unicode 码点位置。重复编辑 ID、遗漏配对、过期快照、虚构引文和未填分数会被拒绝。

汇总保留两位编辑的分歧；只有两人都选 B 才计为一个增强组一致胜出。标准 9 组配对中至少 6 组一致胜出、所有章节完整且在已批准词数范围内、B 无严重问题时，才产生 `editorial_pilot_signal=true`。A 的缺陷单列，不误算成 B 的回归。信号不是统计显著性或付费增长证明，`production_release_ready` 始终为 false：仍需目标读者、完整流水线、H5 和 P2 主动模式确认。

耗时是 Scribe 单次 complete 的中位数/p95，不冒充全书耗时；当前适配器未提供 token/传输次数/费用时保持 null。编辑身份及评价也是外部声明，代码只核验结构与引用，不证明编辑独立性。

## 3. H5 样包及预检

已生成的样包位于 [docs/examples/method-validation](../examples/method-validation/README.md)。该目录中的四个 `invalid-*` 包是故意损坏的测试输入，请勿作为小说上线。

| 场景 | 目的 |
| --- | --- |
| 原 80 章/4 卷基准 | 基准字节、导读、卷界保持 |
| 22 章短篇，分别指定免费 3/4 章 | 导读不挤占免费正文 |
| 48 章/4 卷，每卷 12 章 | 12→13 连续章号及卷归属 |
| 换封面后重复导入 | 不漏更新、不重复建书 |
| 同结构同正文的不同书 | 身份不合并 |
| A→B→A 历史重放 | 不静默覆盖当前版本 |
| 单章、分类、卷标题、Introduction 独立更新 | 只改变对应域，身份与权益保留 |
| 错 MIME、错摘要、重复路径、错章节映射 | 明确拒绝且不修改现有数据 |

免费截止数是 `cases.json` 中的测试产品输入，不新增 ZIP 字段。样包使用很短的技术测试文本，不是完整质量合格小说。

```bash
# 重新生成：必须指定新的、非项目数据目录。
venv/bin/python scripts/build_method_validation_samples.py \
  --output /private/tmp/h5-method-samples

# 单包预检：只读，不修复包、不解压到磁盘、不调用 H5。
venv/bin/python scripts/h5_acceptance.py inspect \
  /private/tmp/h5-method-samples/long-48.zip

# 移动目录/换电脑后，按相对路径重新冻结当地验收计划。
venv/bin/python scripts/h5_acceptance.py plan \
  --cases /private/tmp/h5-method-samples/cases.json \
  > /private/tmp/h5-method-plan.json
```

预检检查 ZIP 路径、重复项、体积预算、文件摘要/MIME、EPUB 实际映射、分类/卷、封面及组件摘要；明确限制公开文件角色与路径，递归扫描内部方法/知识 schema 和关键私有字段，检查 OPF 元数据。它不是恶意内容语义分析器，也不是 H5 导入器。

## 4. H5 开发者如何返回实测结果

不要求改变 H5 的导入逻辑。在测试库中使用**当前实际导入器**执行计划；用外部测试适配器将 H5 的数据库/导入日志字段映射为下面的回执。各 case 可使用独立测试库快照，书主键不必跨 case 相同。每个更新 case 先导入其 baseline，并为测试用户建立至少一条阅读进度、付费解锁和收藏。

顶层格式：

```json
{
  "schema_version": "novel-h5-acceptance-receipt.v1",
  "plan_sha256": "填写当前验收计划摘要",
  "importer": {
    "name": "实际导入器名称",
    "version": "实际部署版本或提交号",
    "environment": "test",
    "executed_at": "2026-09-12T12:00:00Z",
    "trace_sha256": "实际完整测试日志文件的 SHA-256"
  },
  "results": []
}
```

每个 `results[]` 记录：

| 字段 | 来源与要求 |
| --- | --- |
| `case_id`、`package_sha256` | 对应计划 case 与实际送入导入器的 ZIP 字节 |
| `outcome` | `created / updated / unchanged / rejected / history_replay_pending`，与预期动作匹配 |
| `error_code` | 正常结果为空；错误包需由适配器忠实映射为计划的 `expected_error`，数据库/网络失败不映射为格式错误 |
| `book_count_before/after` | 相同测试库范围内的实际书籍总数；创建只增 1，其余不变；存在 baseline 时 before 至少 1，after 至少 1，初次导入空库允许 0→1 |
| `before`、`after` | 数据库实测状态；初次创建 before=null，其余必须有 baseline 状态 |

每个状态对象：

- `book_pk`：H5 实际书主键，统一序列化为字符串。
- `source_book_id`：与引擎身份的实际关联值。
- `introduction_count`：实际独立导读项数量。
- `versions`：H5 保存或导入结果中实际确认的组件摘要。不得只把计划的期望值复制为观察结果。
- `volume_rows`：按卷号排列，每行含 `volume_pk / volume_id / volume_number / title / chapter_start / chapter_end`。
- `chapter_rows`：按全书章号排列，每行含 `chapter_pk / chapter_id / number / volume_id / volume_pk / chapter_in_volume / is_free`；主键字段均为字符串，`is_free` 为布尔值，Introduction 不进入此数组。
- `progress / unlocks / collections`：各含 `count` 和 `sha256`，基于该测试用户/书籍的真实记录。记录按稳定主键排序，再以 UTF-8、排序对象键、无多余空格的规范 JSON 求 SHA-256；两次使用相同字段，含所属书章 ID、进度/权益业务值，不含采样时间。摘要不暴露用户详情，但也不构成独立真实性证明。

对于 history replay，本版用例选择“等待明确恢复确认”，因此 before/after 必须相同；不在该测试中执行恢复覆盖。对不同书，书、章、卷主键均应与已有书分离；对同书更新，书章卷主键、章节外键与进度/解锁/收藏应保持。

```bash
venv/bin/python scripts/h5_acceptance.py verify \
  --plan /private/tmp/h5-method-plan.json \
  --receipt /private/tmp/h5-importer-result.json
```

`verify` 重新检查当前 ZIP 与完整计划，文件被替换或计划缺项会失败；缺少外部结果时返回 `pending`。全部一致时仅返回 `externally_reported_pass`，表示外部声明与合同一致，`current_importer_verified=false`：本工具未亲自执行或认证导入器。最终验收还要核对真实运行日志及其 SHA。完整机读字段可参考 `core/h5_acceptance.py` 中严格的回执模型。

## 5. 开发验证及交付边界

已经过两轴独立只读审查，并补充针对审查发现的回归。未改 ai-novel-h5、生产提示词、正式稿、编译器或打包器；未调用真实模型、重启服务、推送或部署。P1 已有本地回退提交，当前工具独立于生产 pipeline。

### 审查闭环

- **Standards**：已补齐新目录隔离、超长响应留存、请求归属绑定、预算/并发恢复，以及回执书章卷身份和错误原因一致性；最终已审增量无未关闭阻断项。
- **Spec**：已修复 P1 快照兼容性、卷界样本完整性、A/B 严重问题分别计数、空计划假通过、实际卷行/外键缺失和私有 schema 混入公开包；最终已审增量无未关闭阻断项。
- 最后补充的回执反例先复现再修复：已有基准却报告书库为零、创建不同书却漏计基准、成功结果携带数据库错误。空库首次导入 `0→1` 保留为合法正例。
- 这些是代码和合成数据测试结论，不代表真实模型产文提升或外部导入器已经通过。

最终冻结代码回归：后端 **1,583 passed**（含本轮新增 50 项），前端 **107 passed / 17 files**，前端 build 和 lint 成功。后端保留 4 条现有 multiprocessing/fork 弃用警告。最后定向组合为 85 项通过；13 份 ZIP 摘要核对一致，16 项外部导入结果仍为 pending。

最终测试计数、源码/样包摘要和实际执行边界记录在 [验证回执](2026-09-12-method-validation-verification.json)。下一放行步骤仍是确认真实模型预算、取得编辑/目标读者评价和 H5 实测证据；之后再进入 P2 主动修订。
