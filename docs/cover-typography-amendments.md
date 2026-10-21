# 本地封面排版修订

当已有封面的故事画面可保留，但原标题位置遮住人物或阅读效果不佳时，可以独立提出、预览、批准并应用新排版。该流程保留原方向、图片提示词、编译配方和历史图片；修订只绑定一个明确的候选，不能替换已选中的封面。

流程为 **propose → 查看实际 PNG → approve exact hash → apply**。提出和批准不调用模型；应用会以新布局运行真实 Stage A/B 审核，但不调用图片模型、不增加或消耗图片重绘额度。排版批准不等于质量通过，也不等于选中或发布。

## 使用前提与范围

- 候选使用 `composed` 标题模式，有完整的批准配方、来源绑定和保留原画。`native` 标题不能通过此流程擦除或替换。
- 候选可以是已有原画的 `ready`、`failed` 或 `rejected` 状态；不能是当前选中的候选，同一批次不能仍有 `pending` 图片。
- 来源、分析和原批准必须仍有效；审核前及最终提交时均复验。被质疑的分析、过期的来源或撤销的批准不能通过本地修订绕过。
- v1 不迁移跨所有者审批。包含已应用排版修订的 Workshop 封面历史会在导入项目前明确拒绝；已经从 Workshop 导入项目的候选也不能再次提出本地修订。普通未修订历史仍可按原转移流程导入。
- 当前提供 CLI 和项目/Workshop HTTP 入口，未新增专用 Web 排版编辑界面。

两种模式：

| `mode` | 可改变内容 | 限制 |
| --- | --- | --- |
| `overlay` | 标题行、字体、字号范围、颜色、间距、区域和保护区 | 在同一原画上重新排字；不能移动、裁剪、补绘或调色原画，也不能新增 `artwork_region` / `background_color` |
| `contain_panel` | 以上标题布局，以及完整原画的放置区域和实色背景 | 等比缩放完整原画，零裁剪、零补绘，维持原画布尺寸；标题区域必须在实际放置原画之外。人物在缩略图中会变小，须重新评估辨识度 |

## 准备提案

提案是 UTF-8 JSON。以原候选 `_compiled_recipe.title_layout` 为起点，保留书名原文，按实际图片重新确定区域。下面只是结构示例，`Home` 必须替换为当前已批准书名，不能原样用于另一作品：

```json
{
  "schema_version": "cover-typography-amendment.v1",
  "mode": "overlay",
  "title_layout": {
    "title": "Home",
    "lines": ["Home"],
    "font_id": "sans-bold",
    "font_size_min": 0.025,
    "font_size_max": 0.07,
    "letter_spacing": 0,
    "align": "left",
    "color": "#FFF3D6",
    "region": [0.06, 0.025, 0.88, 0.16],
    "safe_zones": [[0, 0.25, 1, 0.75]]
  }
}
```

区域为画布比例 `[x, y, width, height]`，字号与字距以画布宽度为基准。`safe_zones` 是人物、动作等不能被标题遮挡的故事区域，不是标题框。必须根据实际原画核对，几何检查不能自动识别人脸。标题允许重新换行，但不得改变文字或顺序；中文需要覆盖所有字形的字体。字体字节与排版摘要在提案时固定，环境中字体改变会要求新提案。

若从原配方复制布局后更换 `font_id`，请仅在新提案的草稿 JSON 中移除继承的 `font_sha256` 和 `font_weight`，由 `propose` 校验新字体、匹配字重并固定其字节摘要；保留旧值会因字体不匹配而拒绝。不要修改已保存的提案、预览或批准记录；已提出的布局需要换字体时，应重新提出并批准新提案。

`contain_panel` 的提案把 `mode` 改为 `contain_panel`，并在根对象补充例如：

```json
{
  "artwork_region": [0, 0.25, 1, 0.75],
  "background_color": "#123456"
}
```

这是对完整提案的补充，不是独立请求。实际原画会在该区域内等比居中，不会拉伸铺满。标题与保护区均使用最终画布坐标。

## CLI：项目封面

在与项目服务一致的环境、数据目录、工作区和字体环境运行。`NOVEL_OS_DB`、`NOVEL_OS_MEDIA_DIR` 需指向该服务使用的数据库和媒体目录；`--workspace-id` 使用原来源所有者的工作区 ID。不要为读取本地预览切换数据库或重启服务。CLI 当前仅提供项目入口；Workshop 使用下述 HTTP 入口。

以下变量为操作时读取的真实值，`COVER_REVISION` 是整个 cover set 的当前修订号：

```bash
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" propose-typography \
  --project "$PROJECT_PATH" --cover-set "$COVER_SET_ID" --candidate "$CANDIDATE_ID" \
  --expected-revision "$COVER_REVISION" --proposal-json "$PROPOSAL_JSON"
```

返回 `amendment_id`、`amendment_sha256`、`preview_sha256`、`preview_path`、`preview_relative_path`、字体/布局摘要和 `approved: false`。打开 `preview_path` 检查全尺寸、缩略图、书名读序与人物遮挡，再使用本次提案的精确摘要确认：

```bash
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" approve-typography \
  --project "$PROJECT_PATH" --cover-set "$COVER_SET_ID" --candidate "$CANDIDATE_ID" \
  --amendment-id "$AMENDMENT_ID" --expected-amendment-sha256 "$AMENDMENT_SHA256" \
  --confirm-layout
```

批准绑定来源、原配方、原候选与原图片、原画、字体、布局和精确预览。该步骤不替换候选。最后应用：

```bash
PYTHONPATH=core ./venv/bin/python core/orchestrator.py cover --workspace-id "$WORKSPACE_ID" apply-typography \
  --project "$PROJECT_PATH" --cover-set "$COVER_SET_ID" --candidate "$CANDIDATE_ID" \
  --amendment-id "$AMENDMENT_ID" --expected-amendment-sha256 "$AMENDMENT_SHA256" \
  --expected-revision "$COVER_REVISION"
```

应用运行独立审图后，以 CAS 提交精确预览并返回更新后的 cover set。批量处理多个候选时，每次应用后重新读取整个 set 的 `revision`；不要把第一张的修订号用于后续张。其他候选更新不自动取消提案，但本候选任何变化都要求重新提出和批准，不能强制覆盖。

## HTTP：项目和 Workshop

沿用现有工作区认证与权限。两种所有者有相同 JSON 合同，不允许由请求体指定另一个根目录。

```text
项目 BASE = /api/projects/{project_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments
讨论 BASE = /api/workshop/sessions/{session_id}/covers/{cover_set_id}/candidates/{candidate_id}/typography-amendments
```

| 操作 | 方法与路径 | 请求体 / 返回 |
| --- | --- | --- |
| 提出 | `POST BASE` | `{"expected_revision": 7, "proposal": {...}}`；201 返回提案、摘要及作用域内 `preview_url`，不暴露宿主绝对路径 |
| 查看 | `GET BASE/{amendment_id}/preview` | 返回经过完整性验证的 `image/png`，`Cache-Control: private, no-store` |
| 批准 | `POST BASE/{amendment_id}/approve` | `{"expected_amendment_sha256": "<64位摘要>", "confirm_layout": true}`；200 返回批准凭据 |
| 应用 | `POST BASE/{amendment_id}/apply` | `{"expected_revision": 7, "expected_amendment_sha256": "<64位摘要>"}`；202 返回 `cover.typography` job |

`expected_revision` 必须是正整数；`confirm_layout` 必须是显式布尔值 `true`，不是字符串。批准/应用不能夹带内联排版重写；修改布局必须新建提案。通过 `GET /api/jobs/{job_id}` 查看任务状态，完成后重新读取对应 cover set，不能把 202 当作应用成功。

## 审核、失败恢复与选中

应用时 Stage A 只看实际原画及缩略图；`contain_panel` 使用实际缩放放置后的无标题画布。A 的绑定结果先持久化，Stage B 才接收冻结 A、实际最终图片及新排版/图层几何。原编译配方保持不变，新的质量报告和本地排版 attempt 记录独立批准凭据；旧 `human_acceptance` 不沿用。

| 观察到的状态 | 恢复方式 |
| --- | --- |
| 预览失败或看起来不合适 | 调整提案并重新 propose；失败不改变当前候选，也不调用图片模型 |
| CAS 冲突 | 重读 set 与目标候选。只有目标候选仍与提案绑定完全相同，才使用最新 set 修订号重试；否则新提案、重新查看与批准 |
| 审图期间来源、分析或批准失效 | 最终来源复验拒绝提交；先处理实际来源/批准问题，不强行复用旧授权 |
| 进程中断，候选仍是原图片/原修订 | 原始评估与应用记录保留；校验来源与候选仍相同后，可以对同一批准再次 apply。会产生新的评估/应用记录，不能声称恢复了未完成的模型响应 |
| 新图片已经提交，但评估 unavailable、需人工复核，或任务在交付包阶段中断 | 先确认当前图片 SHA 与 amendment 的 preview SHA 相同，勿重复 apply 原父候选。使用现有 `POST .../candidates/{candidate_id}/reevaluate`，体为 `{"expected_revision": <当前修订号>}`，复评同一图片并重建项目交付投影，不调用图片模型 |
| 新排版仍遮挡或审核发现画面语义错误 | 单纯布局问题可提出另一份排版修订；故事画面错误须回设计与批准流程，不能把排版批准当作额外图片预算 |

复评已修订候选会保留当前排版与变换，不能退回旧配方的标题布局。审图失败/未知不等于质量通过；新图不满足推荐门槛时，仍遵守现有人工接受理由与显式选择规则。应用不会自动选中，也不替换已有选中的封面。最终选择及项目 manifest/ZIP 核验仍按既有交付流程执行。

不可变证据保存在所属 cover root 的 `typography-amendments/{amendment_id}/`：提案、精确预览、无标题审核画布、批准凭据及应用记录。原候选图片和历史 attempts 保留。不要手改这些文件、原配方或候选摘要来恢复任务。
