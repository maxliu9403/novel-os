# 封面分析的独立来源复核 CLI

当独立审查发现已完成分析仍误读人物、关系或故事事实时，提交绑定原分析与冻结来源的复核 JSON，再创建修订分析。原分析和既有尝试记录保留；不要直接修改分析文件或替换来源来绕过校验。

```sh
python core/orchestrator.py cover review-analysis \
  --project /absolute/path/to/project \
  --analysis-id analysis-<parent-id> \
  --expected-analysis-sha256 <parent-analysis-sha256> \
  --review-json /absolute/path/to/independent-review.json
```

如项目位于非默认工作区，在 `review-analysis` 前传 `cover --workspace-id WORKSPACE_ID`。该命令使用当前配置的分析模型与分析参数；它会执行文本分析修订与复核，不生成图片或选择封面。成功时标准输出为子分析 JSON，退出码为 `0`；文件、来源或摘要校验失败时输出错误并返回 `2`。

复核文件是 UTF-8 JSON 对象，字段如下；所有 ID、摘要、原值和证据必须来自实际父分析及其冻结来源：

```json
{
  "schema_version": "cover-external-source-review.v1",
  "review_kind": "agent",
  "reviewer_id": "independent-source-reviewer",
  "analysis_id": "<parent analysis ID>",
  "analysis_sha256": "<parent analysis hash>",
  "snapshot_id": "<snapshot ID>",
  "snapshot_sha256": "<snapshot hash>",
  "source_content_sha256": "<source content hash>",
  "source_bundle_sha256": "<source bundle hash>",
  "configuration_sha256": "<canonical hash of parent.configuration>",
  "synthesis_sha256": "<parent.global_checkpoint.synthesis_sha256>",
  "issues": [{
    "issue_id": "relationship-001",
    "problem": "<specific source contradiction>",
    "target_path": "/brief_payload/principal_characters/0/narrative_role",
    "observed_value": "<exact current value at target_path>",
    "evidence_refs": ["evidence:<evidence_id from the parent analysis>"],
    "exact_source_text": "<verbatim substring of one referenced source quote>",
    "suggested_correction": "<source-supported correction>"
  }]
}
```

`target_path` 使用 RFC 6901 JSON Pointer，相对于父分析的 `global_checkpoint.synthesis`。`observed_value` 的值和 JSON 类型必须与原字段一致。后续方向规划使用复核完成后返回的新 `analysis_id`，并继续执行既有方向批准和生成流程。
