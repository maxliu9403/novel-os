# 电影感与商业吸引力封面提示词系统设计

日期：2026-08-30
状态：待用户书面审阅
上游设计：`docs/superpowers/specs/2026-08-29-novel-cover-generation-design.md`

## 1. 摘要

本设计把现有的封面概念模板升级为一个受控的混合系统：

```text
已确认的小说设计
  -> CoverBrief v2 故事事实合同
  -> Cover Art Director 文本模型生成结构化电影场景方案
  -> Canon Validator 拒绝人物、年龄、环境与情节幻觉
  -> Prompt Compiler 确定性编译最终图片提示词
  -> gpt-image-2 独立生成每张候选图
  -> Visual Quality Gate 检查写实、人物、电影感、类型与缩略图质量
  -> Cover Studio 人工审批、单张重试与最终选择
```

Art Director 只做结构化创意决策，不直接输出图片。所有最终封面图片必须由
`gpt-image-2` 生成。系统通过故事张力、类型识别、人物关系和缩略图层级提高封面的
商业吸引力，同时保持封面承诺与小说内容一致。本期不采集、计算或学习广告转化数据。

## 2. 现有问题

当前实现已经具备独立图片设置、3-5 个候选、单候选重试、内容寻址存储、
Cover Studio 选择和交付包，但提示词构建仍有以下缺口：

1. `CoverBrief` 只有一个自由文本 `protagonist`，无法严格表示多主角。
2. 人物年龄、职业、社会身份、日常服装和生活环境没有独立必填字段。
3. 概念由固定模板生成，类型情绪、人物调度和镜头语言不足。
4. `high-conversion` 只是描述性词语，没有被转换成可执行的视觉钩子与故事证据。
5. 写实要求集中在少量形容词，不能系统约束皮肤、人体、镜头、光源和空间。
6. 候选只记录最终提示词，没有记录第一眼应该看到什么以及为何与故事匹配。

## 3. 目标

1. 生成真人写实、电影剧照感、无明显 AI 伪影的商业小说封面。
2. 人物年龄、身份、服装、生活环境和关系严格来自小说合同。
3. 多主角小说必须生成多人物主题封面，并优先使用同一故事场景。
4. 类型情绪通过人物行为、摄影、布光、空间和色彩实现，而非抽象标签。
5. 每个候选对应一个可解释、可测试的商业视觉钩子。
6. 图片渲染模型强制为 `gpt-image-2`，候选仍以独立 `n=1` 调用生成。
7. 保持封面失败不影响小说、章节、Canon 或生产运行状态。
8. 保持已有 CoverSet 的不可变来源、部分成功和单候选恢复能力。

## 4. 非目标

1. 不采集、导入、归因、计算或预测广告 CTR、阅读转化、付费转化和收入数据。
2. 不复制已出版封面、在世艺术家风格、演员或公众人物肖像。
3. 不让商业吸引力覆盖小说事实或制造正文不兑现的情节。
4. 不连接广告平台，不进行 A/B 实验、投放归因、策略学习或预算调整。
5. 不在本次设计中把选中封面嵌入 EPUB、PDF 或 DOCX 正文。
6. 不用确定性文字叠加替换当前由图片模型生成标题的产品决策。

## 5. 核心产品决策

### 5.1 设计目标

封面设计同时满足四个定性目标：

1. **第一眼清晰**：在移动缩略图中快速识别主要人物、关系和类型。
2. **情绪吸引**：用一个未完成动作、关系张力或关键证物产生继续了解的欲望。
3. **审美完成度**：真人写实、电影感、标题清晰，达到商业成品质量。
4. **故事一致**：画面承诺能够由小说事实支撑，不依赖夸张或虚构情节。

这些目标通过设计审阅和视觉质量门判断，不转换成 CTR、转化率或收入预测。

### 5.2 图片模型

- `CoverSettings.model` 必须精确为 `gpt-image-2`。
- Studio 中图片模型显示为只读，不再允许保存其他模型名。
- 服务启动、API 保存和生成前都执行模型检查，避免只在 UI 层约束。
- Art Director 和 Visual Evaluator 可以使用文本或多模态模型，但它们不生成最终图片。
- 所有候选继续使用独立 `n=1` 请求，便于隔离失败、来源和成本。

### 5.3 写实与电影感

默认视觉介质为 `live-action cinematic editorial photograph`。电影感定义为：

- 一个具有前后因果的故事瞬间，而不是站姿人物海报；
- 符合空间关系的演员调度、视线、动作和遮挡；
- 有明确来源的实际光线；
- 合理镜头透视、景深和焦点层次；
- 真人皮肤、年龄痕迹、服装材质和环境使用痕迹；
- 与情绪一致但不过度模板化的电影调色。

黑金、橙青、高反差、浅景深本身不构成电影感，也不能作为所有类型的默认风格。

### 5.4 多主角规则

- 2-3 位核心主角：全部清晰可辨，并在同一故事场景中参与动作或关系。
- 4 位及以上核心主角：2-3 位承担前景或视觉主轴，其余主角仍在同一叙事空间中
  通过中景动作和关系位置出现。
- 禁止头像拼贴、人物卡片、上下分割的无关场景和重复脸。
- 配角只有在承担关键关系、证物或冲突功能时进入画面。

### 5.5 身份来源

人物身份只来自已确认的故事事实。市场配置可以改变构图、摄影、标题排版和商业
审美，但不能根据目标市场推断人物族裔、年龄或阶层。缺失的关键身份信息进入
`visual_assumptions` 并在付费图片生成前确认。

### 5.6 参考封面提炼边界

本轮 8 张参考图只用于提炼视觉机制，不作为复制目标，也不证明广告效果。进入
类型配置的有效机制为：

- 用一个冻结故事瞬间代替静态人物海报；
- 用视线、距离、前后景和遮挡表达关系与权力；
- 用一个戒指、钥匙、孩子、合同、行李或家庭物件承担证据功能；
- 用普通住宅、公司活动、酒店或家庭空间证明人物的生活状态；
- 用亲密未完成、公开排除、指责、发现和反转形成开放问题；
- 在缩略图中保持一个主视觉、一个关系、一个动作和一个证物。

参考图中的塑料皮肤、过度磨皮、物理含混反射、插画化棺木剖面、泛化奢华环境和
人物拼贴不进入正向配置。类似高刺激构图只有在小说存在可解析的故事证据时才允许。

## 6. 模块与所有权

| 模块 | 所有权 | 输入 | 输出 |
|---|---|---|---|
| `CoverBriefNormalizer` | 故事事实 | Prompt、foundation、legacy state | `CoverBriefV2` |
| `CoverArtDirector` | 创意规划 | `CoverBriefV2`、类型配置 | `ArtDirectionSet` |
| `CoverCanonValidator` | 事实边界 | brief、direction set | 验证结果 |
| `CoverPromptCompiler` | 最终提示词 | brief、单个 scene plan、编译版本 | `CompiledCoverPrompt` |
| `ImageGenerationClient` | 图片传输 | compiled prompt、cover settings | `GeneratedImage` |
| `CoverVisualEvaluator` | 视觉评估 | image、brief、scene plan、thumbnail | `CoverQualityReport` |
| `CoverService` | 生命周期 | 上述模块 | CoverSet、retry、selection |
| `CoverStore` | 持久化 | CoverSet revisions | 原子 JSON |
| `CoverStudio` | 人工决策 | concepts、images、reports | approve/reject/retry/select |

模块边界保持如下原则：Art Director 不能调用图片模型；Prompt Compiler 不调用 LLM；
Visual Evaluator 不能改变故事事实或自动产生付费重试；CoverService 是唯一协调者。

## 7. CoverBrief v2

### 7.1 顶层合同

```json
{
  "schema_version": 2,
  "title": "Exact approved title",
  "author": "",
  "language": "English",
  "genre": "contemporary romance / family drama",
  "target_audience": "women 30-45 seeking emotionally grounded relationship drama",
  "market_scope": "United States mobile serialized fiction",
  "core_task": "...",
  "core_conflict": "...",
  "emotional_promise": "...",
  "principal_characters": [],
  "relationship_map": [],
  "lived_environment": {},
  "decisive_story_nodes": [],
  "secondary_signals": [],
  "genre_emotion_profile": {},
  "commercial_visual_goal": {},
  "title_direction": {},
  "forbidden_elements": [],
  "visual_assumptions": []
}
```

### 7.2 主要人物

```json
{
  "character_id": "char_mara",
  "name": "Mara Vale",
  "narrative_role": "co_protagonist",
  "must_appear": true,
  "age": 34,
  "age_band": "early-to-mid thirties",
  "gender_presentation": "woman",
  "physical_identity": "confirmed story-facing appearance",
  "occupation_and_status": "hospital administrator, financially independent",
  "daily_wardrobe": "practical mid-priced office clothing with repeated use",
  "lived_environment": "modest apartment shared with a school-age child",
  "current_emotional_state": "controlled hurt becoming resolve",
  "agency_signal": "removes the shared key and closes the door",
  "relationships": ["char_oren"]
}
```

`must_appear` 人物必须至少提供 `age` 或可信 `age_band`，并提供职业/生活状态、日常
服装或环境锚点。生成提示词同时使用数字年龄和可见年龄阶段，减少模型把 35 岁角色
渲染成 20 岁时装模特的倾向。

### 7.3 关系图

```json
{
  "from_character_id": "char_mara",
  "to_character_id": "char_oren",
  "relationship": "estranged spouses",
  "power_balance": "he controls public status; she controls decisive evidence",
  "visible_tension": "he avoids eye contact while she holds the key",
  "shared_risk": "their child witnesses the rupture"
}
```

关系图用于生成视线、距离、前后景和动作，不直接变成说明性文字。

### 7.4 生活环境

```json
{
  "era": "present day",
  "fictional_place": "invented North American city",
  "primary_spaces": ["lived-in apartment kitchen", "mid-range corporate hotel"],
  "economic_signals": ["repaired dining chair", "practical school backpack"],
  "cultural_signals": ["confirmed story-specific domestic routines"],
  "weather_and_season": "late autumn evening",
  "environment_truths": ["the family is not wealthy", "the home is actively lived in"]
}
```

环境必须通过可见物件、材质、磨损、空间尺度和光源进入画面，禁止只写
`realistic home`、`luxury room` 等空泛词。

### 7.5 类型情绪配置

```json
{
  "primary_genre": "romance",
  "submode": "slow_burn_reconciliation",
  "emotional_temperature": "warm but restrained",
  "desired_viewer_feeling": "intimacy mixed with uncertainty",
  "relationship_motion": "about to move closer but still blocked",
  "prohibited_shortcuts": ["generic smiling couple", "rose collage", "wedding imagery"]
}
```

### 7.6 商业视觉目标

```json
{
  "market": "United States",
  "audience_segment": "women 30-45 interested in relationship and family drama",
  "display_context": "mobile_thumbnail",
  "thumbnail_reference_width": 120,
  "thumbnail_reference_height": 180,
  "first_glance_priority": "relationship rupture",
  "reader_identification": "a partner protecting herself and her child",
  "truthful_story_promise": "the protagonist recognizes the betrayal and acts"
}
```

该合同只指导封面视觉，不包含 campaign、placement、impressions、clicks 或任何转化
字段。`market` 和 `audience_segment` 来自既有受众研究，不用于记录投放结果。

### 7.7 视觉假设

```json
{
  "field": "char_mara.hair",
  "proposed_value": "dark brown shoulder-length hair",
  "reason": "not established in canon; needed for visual continuity",
  "status": "pending_confirmation"
}
```

以下字段缺失会阻止 Art Direction 审批：核心人物年龄阶段、核心人物数量、主要关系、
生活环境、决定性故事节点和类型情绪。非关键发型、精确颜色等可以作为显式假设，
但必须在图片付费调用前获得批准。

## 8. Cover Art Director

### 8.1 模型职责

Art Director 使用一个支持结构化 JSON 输出的强文本模型。它负责：

1. 从已验证 brief 中选择真实可视化的故事瞬间；
2. 为人物设计动作、视线、空间位置和环境证据；
3. 为每个候选分配不同的商业视觉钩子；
4. 选择符合类型的摄影、布光、色彩和标题安全区；
5. 输出 `ArtDirectionSet`，不输出最终图片提示词。

Art Director 不得增加新人物事实、改变年龄、推断族裔、制造后期剧情、复制参考封面
或输出艺术家名字。输入中使用 character id；自由文本中的人物名只是展示信息。

### 8.2 配置

新增独立配置：

```env
NOVEL_OS_COVER_DIRECTOR_PROVIDER=
NOVEL_OS_COVER_DIRECTOR_MODEL=
NOVEL_OS_COVER_DIRECTOR_BASE_URL=
NOVEL_OS_COVER_DIRECTOR_API_KEY=
NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS=180
```

空值回退到已有写作模型设置，但不回退到图片模型。Director 密钥遵循现有写入后
隐藏规则。Art Director 失败时停止在图片调用之前，不静默回退到通用模板并消耗图片
费用。用户可以显式选择经过验证的确定性 fallback 概念。

### 8.3 ArtDirectionSet

```json
{
  "schema_version": 1,
  "director_model": "safe model id",
  "brief_sha256": "...",
  "profile_version": "cover-profiles.v2",
  "plans": [],
  "visual_assumptions": [],
  "status": "awaiting_approval"
}
```

每次方向设计创建新版本，不覆盖已批准方向。批准记录绑定 `brief_sha256` 和完整
`ArtDirectionSet` 哈希。brief 改变后旧方向标记为 stale。

## 9. CoverScenePlan

每个候选必须使用如下合同：

```json
{
  "concept_id": "concept-01-relationship-tension",
  "visual_strategy": "relationship_tension",
  "story_evidence_refs": ["character:char_mara", "node:door_choice"],
  "cast": ["char_mara", "char_oren"],
  "focal_character_id": "char_mara",
  "moment_before": "he asks for the shared key",
  "frozen_action": "she removes the key while he reaches but stops",
  "moment_after": "the apartment door will close between them",
  "gaze_graph": ["char_mara -> key", "char_oren -> char_mara"],
  "blocking": "she foreground right; he one step behind left; doorway between them",
  "environment_anchors": ["lived-in apartment entry", "child's backpack"],
  "primary_prop": "shared brass key",
  "shot_scale": "medium two-shot",
  "camera_height": "eye level",
  "lens": "50mm full-frame equivalent",
  "depth_plan": "faces and key readable; background recognizably lived in",
  "motivated_lighting": "warm hall lamp and cool dusk window",
  "color_script": "warm skin and home light against restrained cool separation",
  "title_safe_zone": "upper third, no faces or hands",
  "visual_hook": {}
}
```

`moment_before` 和 `moment_after` 强制导演选择动作过程中的一帧，减少静态肖像和
人物介绍照。`story_evidence_refs` 必须能在 brief 中解析，否则验证失败。

## 10. 候选组合与商业视觉钩子

默认四个候选使用四种不同的视觉吸引机制：

1. `emotional_identification`：目标读者迅速识别人物的欲望、伤害或边界。
2. `relationship_tension`：人物距离、视线和未完成动作形成开放问题。
3. `evidence_reveal`：一个可信证物改变对关系或处境的理解。
4. `irreversible_moment`：展示即将改变人物命运的一秒。

候选数为 5 时增加 `power_reversal`。候选数为 3 时由 Art Director 根据故事选择
最相关的三种，不按固定顺序截断。

每个 `visual_hook` 包含：

```json
{
  "hook_type": "betrayal_witness",
  "first_glance_subject": "mother holding child",
  "open_question": "Why is the father celebrating with another woman?",
  "identity_anchor": "a partner and mother being publicly excluded",
  "genre_signal": "contemporary family betrayal drama",
  "reader_promise": "the protagonist recognizes the betrayal and acts",
  "target_emotion": "protective anger",
  "misleading_risk": "low",
  "expected_thumbnail_read": "mother and child excluded from family celebration"
}
```

`reader_promise` 必须由前期故事、核心任务或明确的故事节点支撑。只在结局揭示且不
代表全书体验的情节不能作为默认视觉钩子。

## 11. 类型视觉配置

### 11.1 爱情

爱情不是单一暖色滤镜。默认子模式包括：

| 子模式 | 人物动作 | 光线与色彩 | 读者承诺 |
|---|---|---|---|
| `tender_slow_burn` | 将触未触、克制对视、共同完成日常动作 | 暖色实际光、柔和但真实的阴影 | 关系逐渐靠近 |
| `reconciliation` | 保持距离但共享一个旧物或空间 | 家庭暖光和冷色分隔同时存在 | 破裂关系可能修复 |
| `forbidden_tension` | 身体靠近、目光回避、空间有外部阻力 | 暖肤色、局部阴影、环境压力 | 欲望与风险并存 |
| `betrayal_romance` | 一人发现、另一人回避或被第三方牵引 | 暖色记忆与冷色现实对照 | 真相将迫使主角行动 |

所有爱情子模式要求年龄和生活环境可信。禁止默认年轻化、奢华化、婚礼化、玫瑰
拼贴和无故事依据的身体接触。

### 11.2 家庭伦理

默认子模式包括：

| 子模式 | 关系几何 | 典型证物 | 目标情绪 |
|---|---|---|---|
| `accusation_triangle` | 指责者、被指责者、犹豫见证者 | 学校物件、账单、家庭照片 | 不公与保护欲 |
| `public_exclusion` | 主角/孩子前景，被替代关系在远处 | 生日物件、工牌、邀请物 | 被排斥与愤怒 |
| `domestic_betrayal` | 日常空间中的异常距离和回避 | 戒指、行李、钥匙、手机 | 识别背叛 |
| `boundary_and_departure` | 主角控制门、行李或合同 | 钥匙、合同、箱子 | 清醒与行动 |

环境优先使用可信住宅、医院、学校、公司和家庭活动空间。人物必须做出指责、保护、
回避、离开、隐瞒或选择等动作，禁止只用多人悲伤合影表达伦理冲突。

### 11.3 其他类型

新类型配置遵循同一接口：`submode`、`relationship_motion`、`camera_grammar`、
`motivated_light`、`color_logic`、`reader_promise` 和 `prohibited_shortcuts`。
缺少类型配置时使用中性电影写实基线，不自动套用爱情或家庭伦理规则。

## 12. Prompt Compiler

### 12.1 设计原则

Prompt Compiler 是纯函数：相同 `CoverBriefV2 + CoverScenePlan + compiler_version`
必须生成相同提示词。它不访问网络、不调用 LLM、不改变方向计划。

编译后的提示词上限为 12,000 个 Unicode code point。Compiler 先按模块删除重复表达，
再检查长度；超过上限直接失败，不能截断 CAST LOCK、STORY TRUTH、标题或读者承诺。

最终提示词按模型注意力优先级排列：事实和人物先于风格，动作先于装饰，正向摄影
指令先于紧凑排除项。禁止堆叠 `beautiful`、`viral`、`high CTR`、`masterpiece` 等
不可执行形容词。

### 12.2 编译结构

```text
1. ROLE AND OUTPUT
2. STORY TRUTH
3. CAST LOCK
4. SINGLE CINEMATIC MOMENT
5. RELATIONSHIP BLOCKING
6. LIVED ENVIRONMENT AND PRIMARY PROP
7. GENRE EMOTION
8. CAMERA, DEPTH AND MOTIVATED LIGHTING
9. MOBILE COMMERCIAL COVER OBJECTIVE
10. TITLE AND SAFE ZONE
11. PHOTOREALISM REQUIREMENTS
12. COMPACT FAILURE EXCLUSIONS
```

### 12.3 最终提示词模板

```text
Create an original live-action cinematic editorial photograph as a finished
portrait 2:3 commercial novel cover. The final image must look photographed,
not illustrated or synthetically rendered.

STORY TRUTH
Genre: {genre}. Audience: {target_audience}. Core conflict: {core_conflict}.
Use only the following approved fictional story facts: {story_truth}.

CAST LOCK
{one explicit block per required character, including age, identity, clothing,
lived environment, current emotion, and visible action}
All required characters are distinct people and remain visually consistent.

SINGLE CINEMATIC MOMENT
One continuous scene, not a collage. Immediately before: {moment_before}.
Freeze this action: {frozen_action}. Immediately after: {moment_after}.

RELATIONSHIP BLOCKING
{blocking}. Gaze and gesture logic: {gaze_graph}. The power relationship must
be understandable from distance, posture, eye line, and one unfinished action.

LIVED ENVIRONMENT AND PRIMARY PROP
{environment}. Show use, material, scale, and economic reality. The only
dominant story prop is {primary_prop}; it must be physically handled or observed.

GENRE EMOTION
{profile-specific emotional, relationship, light, and color instructions}.

CAMERA, DEPTH AND MOTIVATED LIGHTING
{shot_scale}, {camera_height}, {lens}. {depth_plan}. Light comes from
{motivated_lighting}. Use physically consistent shadows, reflections, perspective,
skin tones, and contact between hands, clothing, props, and furniture.

MOBILE COMMERCIAL COVER OBJECTIVE
At {thumbnail_reference_width}x{thumbnail_reference_height}, the first glance must reveal
{first_glance_subject}. Create one unresolved visual question: {open_question}.
Trigger recognition through {identity_anchor}. Truthfully promise
{reader_promise}. Prioritize one relationship, one action, and one evidence
object; small details are subordinate.

TITLE AND SAFE ZONE
Render the exact title "{title}" exactly once in {language}. No other text.
Keep all faces, hands, and the primary prop outside {title_safe_zone}. The title
must remain readable at mobile thumbnail size.

PHOTOREALISM REQUIREMENTS
Natural skin texture, pores, age-appropriate facial structure and fine lines,
subtle asymmetry, anatomically correct hands, believable hair strands, fabric
weight and wrinkles, lived-in surfaces, realistic lens rendering, and restrained
cinematic color grading. Preserve each character's stated age and social reality.

EXCLUDE
Illustration, painterly rendering, 3D render, plastic or wax skin, beauty-filter
faces, fashion-catalog posing, duplicated people, merged faces, age mismatch,
extra fingers or limbs, impossible reflections, floating props, unrelated luxury,
collage, split-screen, multiple scenes, real landmarks, logos, watermarks,
subtitles, taglines, author text, and any text other than the exact title.
```

模板中的每个占位符由结构化字段产生。空占位符、重复人物、未解析 evidence ref 或
超出长度预算都会在图片调用之前失败。

## 13. 写实与反 AI 质量合同

### 13.1 正向约束

- 年龄：同时描述数字/阶段和可见年龄特征，禁止一律年轻模特化。
- 皮肤：保留毛孔、细纹、自然色差和轻微不对称，不使用磨皮词汇。
- 人体：动作符合关节活动、重量和平衡，手必须与物体真实接触。
- 头发：真实发丝、重力、发际线和环境光，不使用塑料块状高光。
- 服装：匹配职业、收入、时代和日常使用，具有面料重量与褶皱。
- 环境：家具尺度、表面磨损、光源和人物接触关系一致。
- 物理：阴影、反射、视线、透视和景深来自同一个空间。

### 13.2 镜头基线

- 家庭空间和三人关系：35mm 或 50mm，保留环境信息和空间压力。
- 双人亲密关系：50mm 或 85mm，避免过度压缩为美容肖像。
- 证物近景：50mm 或 85mm，但人物仍主导故事，物件不是产品广告。
- 大于三人场景：35mm 或 50mm，使用景深层次而不是缩小所有人脸。
- 极端广角、俯拍、仰拍只有在故事权力关系需要时使用。

## 14. 商业吸引力设计

### 14.1 第一眼视觉钩子

封面必须在缩略图第一眼建立一个真实、单一的问题。有效机制包括：

- 即将发生但尚未完成的亲密动作；
- 人物发现关系异常的瞬间；
- 一个证物改变人物理解；
- 主角被公开排除或误解；
- 主角即将建立边界或夺回控制；
- 与故事一致的危险或不可能处境。

封面不能同时表达三条剧情线。标题、人物关系、动作和证物构成四级层次，环境只支撑
理解。高刺激画面若无法由 `story_evidence_refs` 证明，将被 Canon Validator 拒绝。

### 14.2 缩略图设计预检

每张成图生成只读的 120x180（或 commercial visual goal 指定尺寸）预览。质量检查回答：

1. 第一眼是否能找到主要人物或关系组合？
2. 不读小字是否能识别核心情绪？
3. 是否存在一个未完成动作或开放问题？
4. 唯一证物是否可辨但不抢主角？
5. 标题是否清晰且不遮挡脸、手和证物？
6. 类型是否在一秒内可识别？

预检失败只标记问题，不把未经批准的自动重试转换成图片费用。

### 14.3 数据边界

本期系统不接收 impressions、clicks、reads、paid conversions、revenue、campaign id、
placement 或实验分组，不计算 CTR/CVR，不对候选做数据排名，也不从投放结果自动调整
类型配置。商业吸引力只作为封面设计与人工审阅标准。

## 15. 生成前质量门

图片请求前必须全部通过：

1. `CoverBriefV2` schema 和 SHA 验证；
2. 所有 `must_appear` 人物均进入符合规则的 cast；
3. 每个人物具备年龄阶段和生活身份；
4. 所有 story evidence ref 可解析；
5. scene plan 是一个连续空间和一个冻结动作；
6. 候选的 visual strategy 和 visual hook 互不重复；
7. 类型配置存在或明确选择中性基线；
8. 读者承诺有前期故事依据；
9. 标题和语言与批准 Prompt 完全一致；
10. 没有待确认的关键 `visual_assumptions`；
11. final prompt 通过人物覆盖、禁词、额外文案和长度检查；
12. image model 精确为 `gpt-image-2`。

任何失败都发生在付费图片调用之前。

## 16. 图片生成与成本控制

- 默认请求：`gpt-image-2`、`n=1`、portrait `2:3`、high、JPEG。
- 默认 4 个候选，允许 3-5 个。
- 服务接受 provider-native portrait 2:3 尺寸，保存实际尺寸与请求尺寸。
- Art Direction 先生成并展示；用户批准方向集后才开始图片调用。
- 每完成一个候选立即保存，进程中断最多丢失一个在途请求。
- retry 只处理指定候选，不重新生成已成功候选。
- 自动视觉评分不能自行发起图片重试；每次额外消费必须由用户动作或明确的预批准
  retry budget 触发。预算记录在 direction approval 中，并绑定 direction hash、最大
  额外请求数和过期时间。

## 17. 生成后 Visual Quality Gate

### 17.1 三层检查

第一层为确定性二进制检查：格式、字节限制、portrait 2:3、哈希和可解码性。

第二层为多模态评估，输入原图、缩略图、CoverBrief 和对应 ScenePlan，输出结构化
`CoverQualityReport`。评估模型不生成图片。若没有配置兼容多模态模型，则候选保持
`human_review_required`，不能伪造自动通过结果。

第三层为 Cover Studio 人工审批。人工选择仍是激活封面的唯一方式。

### 17.2 评分维度

每个数值项采用 0-100 分，并附一条可定位到图像区域或 brief 字段的证据。报告状态为
`blocked`、`human_review_required` 或 `recommended_for_human_review`。存在任何 blocker
时为 `blocked`；无 blocker 且事实、人物覆盖、年龄环境、写实和人体物理五项均不低于
80 时可以标记为 `recommended_for_human_review`。该状态仍不能自动选中或激活封面。

```json
{
  "canon_fidelity": 0,
  "required_cast_coverage": 0,
  "age_and_environment_fidelity": 0,
  "photorealism": 0,
  "anatomy_and_physics": 0,
  "cinematic_storytelling": 0,
  "genre_emotion": 0,
  "thumbnail_clarity": 0,
  "hook_promise_alignment": 0,
  "title_legibility_advisory": 0,
  "blockers": [],
  "repair_codes": []
}
```

事实错误、漏掉必要主角、年龄明显错误、明显 AI 人体伪影、多场景拼贴、无依据情节、
额外文字和标题严重错误为 blocker。OCR 只提供提示，不作为标题准确性的唯一证明。

### 17.3 定向修复

重试根据 `repair_codes` 只修改对应提示词模块：

| repair code | 修改模块 |
|---|---|
| `age_mismatch` | CAST LOCK |
| `missing_character` | CAST LOCK + RELATIONSHIP BLOCKING |
| `generic_ai_face` | PHOTOREALISM + CAMERA |
| `weak_story_action` | SINGLE CINEMATIC MOMENT |
| `genre_drift` | GENRE EMOTION |
| `thumbnail_clutter` | BLOCKING + COMMERCIAL COVER OBJECTIVE |
| `reader_promise_mismatch` | VISUAL HOOK + STORY EVIDENCE |
| `title_failure` | TITLE SAFE ZONE |

修复生成新的 prompt revision，并保留旧 prompt、质量报告和失败图片来源。就绪图片仍然
不可原地修改；候选重试创建新的 generation attempt 记录。

## 18. 持久化与来源

`CoverSet` 增加：

- `brief_schema_version`
- `art_direction_set_id` 和 hash
- `profile_version`
- `compiler_version`
- `concept_approval`
- 每个 concept 的 `scene_plan` 与 `visual_hook`
- 每个 candidate 的 `prompt_revision`、attempt history 和 quality report
- `commercial_visual_goal`

图片候选继续保存模型、request id、实际尺寸、请求尺寸、格式、SHA、最终提示词和安全
请求参数。API 密钥、Authorization 头、完整 provider 错误体和未发布原始 Prompt 不进入
CoverSet 或日志。

## 19. Studio 工作流

Cover Studio 分为四个连续状态：

1. **Story facts**：显示主要人物、年龄、生活环境、类型和待确认视觉假设。
2. **Art direction**：显示 3-5 个场景方案、cast、视觉钩子和故事证据；批准后锁定 hash。
3. **Generation**：逐张显示进度、图片费用边界和独立失败。
4. **Review**：并排显示原图、移动缩略图、质量报告、视觉钩子和 retry 原因。

用户可以拒绝一个概念并在图片调用前重新导演，也可以在成图后对单张候选选择具体
repair code。选择封面仍需确认；系统不根据离线分数自动选中。

## 20. API 与命令边界

目标 API 分为：

```text
POST /projects/{id}/covers/directions
POST /projects/{id}/covers/directions/{direction_id}/approve
POST /projects/{id}/covers/generate
POST /projects/{id}/covers/{set_id}/candidates/{candidate_id}/retry
GET  /projects/{id}/covers/{set_id}/quality
```

`generate` 必须引用已批准且未 stale 的 direction id。现有直接生成 API 在兼容期内可以
内部执行 direction planning，但必须返回 `awaiting_approval`，不立即调用图片模型。

`./deploy.sh novel-cover` 保持不重启健康服务。非交互模式必须提供
`NOVEL_OS_COVER_APPROVED_DIRECTION_SHA256=<exact-sha256>`；服务端将其与当前 brief、
direction 内容和持久化审批记录同时核对。布尔环境变量不能表示审批，也不存在永久
全局跳过开关。

## 21. 兼容与迁移

### 21.1 Schema v1

- 已存储 v1 CoverSet 保持可读、可选择和可交付，不原地重写。
- 新生成请求通过 `CoverBriefNormalizer` 把 v1 protagonist 转为一个
  `principal_characters` 条目。
- v1 无法提供的年龄、生活环境和多主角信息进入 `visual_assumptions`。
- 缺少关键字段时只允许生成方向草案，不允许进入付费图片请求。

### 21.2 Legacy project

legacy adapter 继续读取 `foundation.json`、`story_state.json` 和 ending contract，但必须：

1. 收集所有 role 为 protagonist/co-protagonist 或被 story contract 标记为主要视角的人物；
2. 优先使用明确年龄，其次可信年龄阶段；
3. 从 setting、occupation、resource 和 relationship 数据提取生活环境；
4. 给每个推断字段记录来源路径；
5. 对缺失关键事实生成待确认项，不用市场刻板印象填充。

### 21.3 当前概念

现有确定性五个 concept family 保留为显式 fallback 和回归基线。正常生产路径使用
Art Director；fallback 也必须经过 v2 Validator 和 Prompt Compiler，不能继续发送旧的
自由文本提示词。

## 22. 失败处理

| 阶段 | 失败 | 行为 |
|---|---|---|
| brief normalization | 缺少关键事实 | 返回待确认字段，不调用模型 |
| art direction | provider/JSON/schema 失败 | 保留 brief，允许重试 direction |
| canon validation | 幻觉或 evidence 缺失 | 拒绝方向，不调用图片模型 |
| prompt compile | 占位符/长度/人物覆盖失败 | 开发错误或用户可见验证错误 |
| gpt-image-2 | 429/5xx/timeout | 保持候选级失败和可重试状态 |
| binary validation | 格式/比例/解码失败 | 候选失败，不写 deliverable |
| visual evaluation | evaluator 不可用 | human_review_required，不伪造分数 |
| quality blocker | 人物/写实/标题/事实失败 | 候选可见但不可推荐，等待人工 retry/reject |

任何阶段都不能回滚、删除或改变小说生产运行。封面状态和小说运行状态保持独立。

## 23. 测试策略

### 23.1 合同测试

- v2 单主角、双主角、三主角和四主角 round trip；
- `must_appear` 年龄/年龄段与生活身份必填；
- 市场不能覆盖人物身份；
- v1 到 v2 的兼容转换和待确认字段；
- legacy 多主角提取与字段来源记录；
- story evidence refs 和 visual assumptions gate。

### 23.2 Art Director 测试

- 使用固定结构化响应 fixture，不调用真实文本模型；
- 四个策略、场景、镜头和视觉钩子具有实质差异；
- 拒绝新增人物、年龄变化、族裔推断、无依据 spoiler 和多场景拼贴；
- provider 失败发生在图片调用前；
- direction approval 与 brief hash 绑定。

### 23.3 Compiler golden tests

- 同输入生成逐字相同提示词；
- 所有必要人物、年龄、环境和读者承诺进入正确模块；
- 爱情与家庭伦理子模式选择正确；
- 2-3 人和 4+ 人构图规则不同；
- 不出现空占位符、未批准身份、额外文案和不可执行营销形容词；
- retry repair code 只改变目标模块。

### 23.4 Image client 与 service

- 只接受 `gpt-image-2`；
- 每个概念独立 `n=1`；
- provider-native portrait 2:3 JPEG/PNG；
- partial success、single retry、attempt history 和 ready provenance；
- 未批准 direction、stale brief 和 pending assumption 均不调用图片 client；
- 测试禁止访问付费端点。

### 23.5 Studio 与可访问性

- facts -> direction -> generation -> review 状态；
- 多主角和待确认字段可见；
- 原图/120x180 切换不改变布局；
- 质量 blocker、visual hook 和 repair code 可读；
- 生成期间按钮禁用、进度稳定、错误可恢复；
- 桌面和移动端无文字或控件重叠。

### 23.6 商业视觉边界

- commercial visual goal 只包含受众、缩略图和读者承诺；
- API 和 CoverSet 不出现 campaign、impression、click、conversion 或 revenue 字段；
- visual hook 可以追踪到 story evidence；
- prompt/cover/profile version 可追踪；
- 人工审阅不会修改 Canon 或人物合同。

## 24. 分阶段实施

### Phase 1: 事实合同与确定性编译

- CoverBrief v2、迁移、类型配置；
- Prompt Compiler、golden tests；
- `gpt-image-2` 硬约束；
- 当前模板通过新 compiler 运行。

### Phase 2: 结构化 Art Director

- Director 设置和客户端；
- ArtDirectionSet、ScenePlan、Canon Validator；
- 方向审批、stale 和来源持久化；
- CLI/API/Studio 方向预览。

### Phase 3: 视觉质量与定向重试

- thumbnail projection；
- CoverQualityReport 和 evaluator interface；
- repair codes、attempt history；
- Studio 并排审阅。

每个 Phase 独立提交和验证。Phase 1-3 构成本次图片质量升级的完整生产能力。

## 25. 验收标准

1. 新生成的 CoverBrief 使用 schema v2，并结构化记录所有核心主角。
2. 必须入画人物具备年龄/年龄段、身份、生活环境和动作依据。
3. 2-3 主角全部清晰入画；4+ 主角遵循前景主轴和同场景中景规则。
4. 正常路径由 Art Director 产生结构化 ScenePlan，不直接产生自由文本图片提示词。
5. Canon Validator 可以阻止新增人物事实、年龄漂移、市场身份推断和无依据情节。
6. Prompt Compiler 对相同输入产生确定性结果，并通过类型/人物/视觉钩子 golden tests。
7. 所有最终图片请求的 model 精确为 `gpt-image-2`，每张为独立 `n=1`。
8. 每张候选保留 scene plan、visual hook、compiler version、prompt、request id、
   model、尺寸、格式、SHA 和 attempt history。
9. 爱情候选通过行为、距离、实际暖光和环境产生暧昧，不依赖通用玫瑰或婚礼符号。
10. 家庭伦理候选通过可信空间、关系动作和唯一生活证物表达冲突。
11. 成图使用真人电影摄影基线，不以插画、3D、塑料皮肤或时装站姿作为成功输出。
12. 缩略图可以识别一个关系、一个动作、一个开放问题和清晰标题。
13. 商业吸引力只通过定性视觉合同和人工审阅判断，不采集或预测广告转化数据。
14. API、CoverSet 和 Studio 不包含 campaign、impression、click、conversion 或 revenue 数据。
15. 图片、Director 或 Evaluator 失败不改变小说、章节、Canon 或生产运行状态。
16. 所有自动测试不访问付费端点；live `gpt-image-2` 验证继续需要明确费用批准。
17. 后端、前端、Skill、CLI、Docker 和浏览器验证全部通过后才标记实现完成。

## 26. 实施前冻结的决策

以下决策在本规格批准后视为实现合同：

- 图片渲染器固定为 `gpt-image-2`。
- 默认四候选、独立 `n=1`、high、JPEG、portrait 2:3。
- 使用 CoverBrief v2 + Art Director + Canon Validator + Prompt Compiler。
- 电影写实是默认介质；不把插画作为成功 fallback。
- 多主角必须同场景表达，禁止头像拼贴。
- 本期只优化封面商业吸引力，不采集、计算或学习广告转化数据。
- 市场配置不能推断或覆盖人物身份。
- 图片调用前必须审批方向和关键视觉假设。
- 图片调用后仍由用户在 Cover Studio 明确选择，不自动激活候选。
