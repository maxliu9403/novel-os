# 头脑风暴到封面工作台交接方案

状态：已确定并实现的最小改造方案
范围：小说骨架的 `COVER_HANDOFF` 与封面工作台输入边界  
不包含：正文创作引擎、图片模型调用策略、封面 UI 重构

## 目标

头脑风暴阶段只交付封面工作台需要的故事事实和用户约束，封面工作台继续独占封面时刻选择、人物调度、构图、视觉钩子、方向组合和质量评估。这样可以避免两个模型分别分析同一故事并产生不一致的封面方案。

本次交接不新增完整封面设计合同，不要求小说骨架生成 `candidate_moments`、`cast_priority`、`visual_motifs`、`inference_policy`、镜头参数或图片提示词。

实现状态：分析链会从不可变骨架提取约束并持久化；导演语义审核和既有质量审核复用同一次审核边界；图片提示编译器以 `cover-compiler.v16` 的受保护模块传入故事事实、禁止内容和剧透上限。盲读审核仍不接收故事上下文，因此没有新增重复的封面分析任务。

## 职责边界

| 能力 | 头脑风暴 Skill | 封面工作台 |
| --- | --- | --- |
| 核心冲突、人物身份、关系和故事环境 | 负责 | 使用并校验 |
| 故事节点、章节来源和伏笔 | 负责 | 使用并定位 |
| 用户指定的必须出现内容 | 记录 | 校验并纳入方向 |
| 用户禁止出现内容和剧透上限 | 记录 | 校验并阻断违规方向 |
| 可视化封面时刻 | 不负责选择 | 负责选择 |
| 人物动作、反应和空间关系 | 不负责设计 | 负责设计 |
| `scene` / `campaign_composite` | 不负责选择 | 负责选择 |
| 镜头、光线、色彩、标题布局 | 不负责设计 | 负责设计 |
| 3–5 个封面方向 | 不生成 | 负责生成 |
| 图片生成和质量评估 | 不负责 | 负责 |

## 新增可选字段

主封面合同仍然使用 `schema_version: 2`。在现有 `COVER_HANDOFF` JSON 中增加可选的 `cover_constraints`：

```json
{
  "cover_constraints": {
    "constraint_version": "cover-constraints.v1",
    "must_preserve_story_facts": [
      "confirmed character relationship",
      "confirmed central conflict",
      "confirmed story environment"
    ],
    "must_show_if_supported": [
      "user-selected story node or character"
    ],
    "must_not_show": [
      "final outcome",
      "unsupported identity",
      "nonexistent relationship or event"
    ],
    "spoiler_ceiling": "The cover may not reveal the final legal or relationship outcome.",
    "user_visual_preferences": [],
    "preferred_story_node_ids": [],
    "preferred_character_ids": [],
    "source_refs": [
      "section:6.relationship_map",
      "section:9.node:event_refusal"
    ]
  }
}
```

字段语义：

- `must_preserve_story_facts`：封面不能改变的已确认故事事实。
- `must_show_if_supported`：用户指定、但只有在来源分析确认可视化时才必须纳入的内容。
- `must_not_show`：不能出现在封面中的结局、身份、关系或事件。
- `spoiler_ceiling`：封面最多可以揭示到什么阶段。
- `user_visual_preferences`：用户明确给出的视觉偏好，仅作为设计偏好，不升级为故事事实。
- `preferred_story_node_ids`：用户偏好的故事节点。它是封面工作台的候选池提示，不是已批准的封面场景。
- `preferred_character_ids`：用户希望优先考虑的人物 ID，不强制每个方向都出现。
- `source_refs`：指向骨架中已经确认的章节、关系或节点。支持
  `section:<number>`、`section:<number>.<heading_slug>`、
  `section:<number>.node:<node_id>`，由封面工作台在不可变快照中精确解析。
  最多 32 个且不可重复；节点锚点必须唯一。解析检查完整原文，持久化的单条
  来源摘要最多 12,000 字符，节点摘要保留锚点附近内容；完整骨架仍保存在快照中。

没有用户专门的封面约束时，应省略整个 `cover_constraints`，不要凭空生成视觉偏好。

## 明确禁止的重复职责

头脑风暴骨架不得在 `cover_constraints` 或其他新增区块中生成：

- 封面候选时刻的完整视觉方案；
- 人物站位、动作和视线关系；
- `scene` 或 `campaign_composite` 方向；
- `CoverScenePlan`、`CoreConflictVisualContract` 的副本；
- 镜头、光线、色彩、景别和标题布局；
- 视觉钩子或图片生成提示词；
- 3–5 个封面方向。

这些内容由封面工作台在完整来源分析之后生成。

## 封面工作台处理流程

```text
读取项目或头脑风暴会话的完整骨架
        ↓
创建不可变 source snapshot
        ↓
按 source digest 查找完整分析缓存
        ├─ 命中：复用已验证分析
        └─ 未命中：完成一次全量来源分析并保存
        ↓
读取 COVER_HANDOFF 与可选 cover_constraints
        ↓
使用约束过滤或排序候选故事节点
        ↓
由封面导演生成 CoverCampaignPlan / CoverScenePlan
        ↓
生成 3–5 个方向并执行来源、剧透和视觉质量校验
        ↓
用户批准方向后，才进入图片生成
```

`COVER_HANDOFF` 是压缩事实投影，不能替代完整骨架快照和来源分析。切换封面方向、重新进行质量评估或重新编译图片提示词时，不应重新分析相同的 source digest。骨架、来源政策或分析配置发生变化时，缓存必须失效。

## 解析和兼容要求

`cover_constraints` 是可选字段：

- 没有该字段的历史 schema-v2 骨架必须继续通过解析；
- 有该字段时必须完成 typed parse 和 `to_dict()` 往返保留；
- `constraint_version` 当前只接受 `cover-constraints.v1`；
- 所有数组必须是字符串数组；
- 空字段可以使用空数组或空字符串；
- 不允许把候选节点、人物 ID、自由视觉偏好或来源引用当作第二套图片设计合同发送给图片模型；已批准场景的事实边界、禁止内容和剧透上限由现有提示编译器作为受保护模块传入；
- 新字段改变骨架正文后，现有 source prompt digest 必须改变，旧批准结果不能复用。

注意：当前解析器对未知顶层字段会静默忽略。因此仅修改 Skill 模板不能完成这项功能，`CoverBriefV2` 必须同步支持该可选字段并进行往返序列化。

## 失败和阻断策略

以下情况应在封面方向生成前阻断：

- `cover_constraints` 不是对象；
- 版本不是 `cover-constraints.v1`；
- 数组包含非字符串值；
- 交接标记不完整或重复，或来源引用重复、超过上限；
- 用户指定的节点或人物无法在完整来源中找到；
- 骨架 `source_refs` 不使用受支持的 `section:` 语法，或无法在快照中解析；
- `must_show_if_supported` 中明确写出的已知节点、人物或信号没有进入任何方向；
- 方向的可执行视觉字段包含 `must_not_show` 中的完整禁止短语。

`section:` 来源引用由封面工作台对不可变骨架快照解析，不能靠 handoff JSON 自己声明来证明存在。`must_preserve_story_facts`
和 `spoiler_ceiling` 是来源边界与提示锁，不能仅靠字符串比较证明语义一致；它们必须同时进入导演合同、图片提示词和人工/质量审核。封面工作台不得把自由文本约束当作新的故事事实。

以下情况不应阻断：

- 没有发型、五官、体型或服装颜色；
- 没有光线、天气和色彩方案；
- 没有用户指定的封面节点；
- `user_visual_preferences` 为空。

上述视觉细节由封面工作台作为封面专用推演处理，并保持 `model_inferred`，不能写回故事正典。

## 对接文件

- 头脑风暴规则：`skills/novel-brainstorm-workshop/SKILL.md`
- 骨架模板：`skills/novel-brainstorm-workshop/assets/novel-skeleton-template.md`
- 骨架合同：`skills/novel-brainstorm-workshop/references/prompt-contract.md`
- 封面 Skill：`skills/novel-cover-studio/SKILL.md`
- 封面交接规则：`skills/novel-cover-studio/references/cover-handoff.md`
- V2 数据模型：`core/cover_models_v2.py`
- 封面解析入口：`core/cover_handoff.py`
- 现有方向合同：`core/cover_campaign.py`、`core/cover_validator.py`

## 验收标准

1. 新旧 schema-v2 `COVER_HANDOFF` 都能解析。
2. 新增 `cover_constraints` 后，解析和序列化结果不丢字段。
3. 省略 `cover_constraints` 时，历史模型和封面流程行为不变。
4. 头脑风暴输出不包含封面方向、图片提示词或重复的候选时刻合同。
5. 封面工作台仍然从完整骨架分析生成 `CoverCampaignPlan`。
6. 同一 source digest 下切换方向不会触发重复的完整故事分析。
7. 变更骨架后旧的分析、方向和批准结果会被标记为过期。
8. 缺少外貌、服装、光线等视觉细节不会阻断封面方向生成。
9. 不支持的节点、人物、可解析来源引用、明确的 `must_show_if_supported` 缺失和完整禁止短语会在图片模型调用前被拒绝；自由文本剧透边界进入同一次既有事实质量审核确认，不新增独立分析任务。

