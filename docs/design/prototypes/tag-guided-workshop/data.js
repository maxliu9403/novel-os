// Isolated design fixtures; no production writes or model calls.
window.TAG_DEMO = {
  "catalog": {
    "age_bands": [
      "adult",
      "midlife",
      "teen",
      "unknown"
    ],
    "audience_channels": [
      "female",
      "general",
      "male"
    ],
    "axes": {
      "primary_genres": [
        {
          "id": "romance",
          "labels": {
            "en-US": "Romance",
            "zh-CN": "浪漫"
          },
          "priority_tier": "core"
        },
        {
          "id": "womens_fiction",
          "labels": {
            "en-US": "Women's Fiction",
            "zh-CN": "女性小说"
          },
          "priority_tier": "core"
        },
        {
          "id": "family_drama",
          "labels": {
            "en-US": "Family Drama",
            "zh-CN": "家庭"
          },
          "priority_tier": "core"
        },
        {
          "id": "mystery",
          "labels": {
            "en-US": "Mystery",
            "zh-CN": "悬疑"
          },
          "priority_tier": "growth"
        },
        {
          "id": "thriller",
          "labels": {
            "en-US": "Thriller",
            "zh-CN": "惊悚"
          },
          "legacy_labels": [
            "惊悚片"
          ],
          "priority_tier": "growth"
        },
        {
          "id": "fantasy",
          "labels": {
            "en-US": "Fantasy",
            "zh-CN": "奇幻"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Make cultivation rules, advancement costs, and rival responses drive earned growth and escalating stakes.",
          "id": "xuanhuan",
          "labels": {
            "en-US": "Xuanhuan",
            "zh-CN": "玄幻"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Establish an escalating source of dread whose rules constrain choices and make survival costly.",
          "id": "horror",
          "labels": {
            "en-US": "Horror",
            "zh-CN": "恐怖"
          },
          "priority_tier": "growth"
        },
        {
          "id": "supernatural",
          "labels": {
            "en-US": "Supernatural",
            "zh-CN": "超自然"
          },
          "priority_tier": "growth"
        },
        {
          "id": "contemporary_realism",
          "labels": {
            "en-US": "Contemporary Realism",
            "zh-CN": "情感现实主义"
          },
          "priority_tier": "core"
        },
        {
          "id": "young_adult",
          "labels": {
            "en-US": "Young Adult",
            "zh-CN": "青春"
          },
          "legacy_labels": [
            "青春激情"
          ],
          "priority_tier": "experimental"
        },
        {
          "id": "general_fiction",
          "labels": {
            "en-US": "General Fiction",
            "zh-CN": "综合小说"
          },
          "priority_tier": "compatibility"
        }
      ],
      "settings": [
        {
          "id": "contemporary_urban",
          "labels": {
            "en-US": "Contemporary Urban",
            "zh-CN": "现代城市"
          },
          "priority_tier": "core"
        },
        {
          "id": "social_life",
          "labels": {
            "en-US": "Social Life",
            "zh-CN": "社交生活"
          },
          "priority_tier": "experimental"
        },
        {
          "id": "family_domestic",
          "labels": {
            "en-US": "Family / Domestic",
            "zh-CN": "家庭生活"
          },
          "priority_tier": "core"
        },
        {
          "id": "royal_court",
          "labels": {
            "en-US": "Royal Court",
            "zh-CN": "宫廷"
          },
          "priority_tier": "experimental"
        },
        {
          "id": "western",
          "labels": {
            "en-US": "Western",
            "zh-CN": "西部"
          },
          "priority_tier": "experimental"
        },
        {
          "id": "post_apocalyptic",
          "labels": {
            "en-US": "Post-Apocalyptic",
            "zh-CN": "后末日"
          },
          "priority_tier": "experimental"
        }
      ],
      "story_types": [
        {
          "design_requirement": "Build earned tenderness, relational safety, and visible reciprocal care.",
          "id": "sweet_romance",
          "labels": {
            "en-US": "Sweet Romance",
            "zh-CN": "甜蜜浪漫"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Make the power imbalance produce consequential choices rather than decorative wealth.",
          "id": "ceo_romance",
          "labels": {
            "en-US": "CEO Romance",
            "zh-CN": "CEO浪漫"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Sustain danger, moral pressure, and costly attraction with a coherent emotional contract.",
          "id": "dark_romance",
          "labels": {
            "en-US": "Dark Romance",
            "zh-CN": "黑暗浪漫"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Establish the injury, an active counter-plan, escalating countermoves, and a visible consequence.",
          "id": "revenge",
          "labels": {
            "en-US": "Revenge",
            "zh-CN": "复仇"
          },
          "priority_tier": "core"
        },
        {
          "design_requirement": "Make renewed trust depend on demonstrated change and a fresh high-cost choice.",
          "id": "second_chance",
          "labels": {
            "en-US": "Second Chance",
            "zh-CN": "第二次机会"
          },
          "priority_tier": "core"
        },
        {
          "design_requirement": "Use the criminal power structure as active pressure on loyalty, intimacy, and survival.",
          "id": "mafia",
          "labels": {
            "en-US": "Mafia",
            "zh-CN": "黑手党"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Turn pack rules, identity, and bond expectations into causal story constraints.",
          "id": "werewolf",
          "labels": {
            "en-US": "Werewolf",
            "zh-CN": "狼人"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Tie every alliance and betrayal to succession, legitimacy, or access to power.",
          "id": "royal_intrigue",
          "labels": {
            "en-US": "Royal Intrigue",
            "zh-CN": "宫廷阴谋"
          },
          "priority_tier": "experimental"
        },
        {
          "design_requirement": "Reveal a concrete intimate betrayal, its family cost, and the protagonist's decisive response.",
          "id": "domestic_betrayal",
          "labels": {
            "en-US": "Domestic Betrayal",
            "zh-CN": "家庭背叛"
          },
          "priority_tier": "core"
        },
        {
          "design_requirement": "Center incompatible marital choices and force a durable change in the relationship structure.",
          "id": "marriage_crisis",
          "labels": {
            "en-US": "Marriage Crisis",
            "zh-CN": "婚姻危机"
          },
          "priority_tier": "core"
        },
        {
          "design_requirement": "Give competing obligations credible emotional weight; trace injury, interpretation, conflicting feelings, and a consequential moral choice.",
          "id": "ethical_dilemma",
          "labels": {
            "en-US": "Ethical Drama",
            "zh-CN": "伦理"
          },
          "priority_tier": "core"
        },
        {
          "design_requirement": "Ground her changing self-belief in specific wounds, mixed emotions, and increasingly independent choices with lasting consequences.",
          "id": "female_growth",
          "labels": {
            "en-US": "Female Growth",
            "zh-CN": "女性成长"
          },
          "priority_tier": "core"
        },
        {
          "design_requirement": "Ground his changing self-belief in specific wounds, mixed emotions, and increasingly responsible choices with lasting consequences.",
          "id": "male_growth",
          "labels": {
            "en-US": "Male Growth",
            "zh-CN": "男性成长"
          },
          "priority_tier": "core"
        },
        {
          "design_requirement": "Make public image, private needs, and career obligations collide in costly personal decisions.",
          "id": "celebrity",
          "labels": {
            "en-US": "Celebrity",
            "zh-CN": "名人明星"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Show the survivor's lived emotional impact, constraints, and choices toward agency; make recovery earned and never confuse abuse with proof of love.",
          "id": "abuse_survival",
          "labels": {
            "en-US": "Abuse Survival",
            "zh-CN": "受到虐待"
          },
          "priority_tier": "core"
        },
        {
          "design_requirement": "Give the queen or empress concrete authority, obligations, and personal loyalties that shape consequential decisions.",
          "id": "queen_empress",
          "labels": {
            "en-US": "Queen / Empress",
            "zh-CN": "皇后女王"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Turn immediate attraction into tested trust through later choices, incompatible needs, and earned emotional intimacy.",
          "id": "love_at_first_sight",
          "labels": {
            "en-US": "Love at First Sight",
            "zh-CN": "一见钟情"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Make workplace responsibilities, professional boundaries, and attraction create credible relational costs and choices.",
          "id": "office_romance",
          "labels": {
            "en-US": "Office Romance",
            "zh-CN": "办公室恋情"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Build escalating comic consequences from clashing workplace goals while preserving believable motives and emotional stakes.",
          "id": "workplace_comedy",
          "labels": {
            "en-US": "Workplace Comedy",
            "zh-CN": "职场闹剧"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Build a specific same-sex relationship with distinct personalities, reciprocal attraction, emotional choices, and earned intimacy.",
          "id": "same_sex_romance",
          "labels": {
            "en-US": "Same-Sex Romance",
            "zh-CN": "同性恋"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Make caregiving, limited resources, and personal desires produce difficult choices for a parent with an independent emotional life.",
          "id": "single_parent",
          "labels": {
            "en-US": "Single Parent",
            "zh-CN": "单身父母亲"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Make wealth change access, dependency, and family or romantic obligations instead of serving only as luxury scenery.",
          "id": "billionaire",
          "labels": {
            "en-US": "Billionaire",
            "zh-CN": "富豪"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Give pregnancy concrete emotional and practical consequences, preserve the pregnant character's agency, and let choices reshape relationships.",
          "id": "pregnancy",
          "labels": {
            "en-US": "Pregnancy",
            "zh-CN": "怀孕"
          },
          "priority_tier": "growth"
        },
        {
          "design_requirement": "Stage the unfolding collapse through causal losses, survival choices, changing alliances, and escalating consequences.",
          "id": "apocalypse",
          "labels": {
            "en-US": "Apocalypse",
            "zh-CN": "末日降临"
          },
          "priority_tier": "growth"
        }
      ],
      "tones": [
        {
          "id": "angst",
          "labels": {
            "en-US": "Angst",
            "zh-CN": "虐心"
          },
          "legacy_labels": [
            "焦虑"
          ],
          "priority_tier": "core"
        },
        {
          "id": "sweet",
          "labels": {
            "en-US": "Sweet",
            "zh-CN": "甜蜜"
          },
          "priority_tier": "growth"
        },
        {
          "id": "dark",
          "labels": {
            "en-US": "Dark",
            "zh-CN": "黑暗"
          },
          "priority_tier": "growth"
        },
        {
          "id": "suspenseful",
          "labels": {
            "en-US": "Suspenseful",
            "zh-CN": "紧张悬疑"
          },
          "priority_tier": "growth"
        },
        {
          "id": "passionate",
          "labels": {
            "en-US": "Passionate",
            "zh-CN": "激情"
          },
          "priority_tier": "experimental"
        },
        {
          "id": "emotional_realism",
          "labels": {
            "en-US": "Emotional Realism",
            "zh-CN": "情感现实主义"
          },
          "priority_tier": "core"
        }
      ]
    },
    "catalog_version": "novel-types.2026-10",
    "chapter_bands": [
      "chapters_0_50",
      "chapters_101_150",
      "chapters_151_200",
      "chapters_201_plus",
      "chapters_51_100",
      "unknown"
    ],
    "combination_rules": {
      "mutually_exclusive_story_types": [
        [
          "sweet_romance",
          "dark_romance"
        ]
      ],
      "primary_genre_count": 1,
      "secondary_genre_max": 2,
      "setting_max": 2,
      "story_type_max": 3,
      "tone_max": 3
    },
    "length_forms": [
      "long",
      "short",
      "unknown"
    ],
    "schema_version": "novel-classification.v1",
    "supported_catalog_versions": [
      "novel-types.2026-09",
      "novel-types.2026-10"
    ]
  },
  "profiles": {
    "primary_genres/romance": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: primary_genres\ntag_id: romance\ncommitments:\n  core_promise: 以主要人物的亲密关系发展组织故事，结局兑现有依据的相爱与共同未来。\n---\n\n# 浪漫\n\n## 读者期待\n\n让读者在吸引、相处和关键选择中相信这两个人值得相爱；不只关心他们是否在一起，也关心为什么只能是彼此。\n\n## 故事作用\n\n让两人的目标既相互吸引又产生真实摩擦。重要关系变化须通过共同经历、独特理解和付出表现；外部事件必须改变关系处境。\n\n## 发散方向\n\n- 吸引的起点：彼此误判后的发现、共同事业中的默契、旧日关系中被忽略的一面。\n- 阻力的来源：不兼容的生活方向、关系中的不平等、过去伤害、相互竞争的责任。\n- 关系的回报：学会共同生活、重建平等、主动选择彼此；不限定婚礼或生育。\n\n## 组合规则\n\n若同时选择第二次机会，以原有主角关系的重建为核心。若选择背叛，让浪漫回报建立在对伤害的承担上。不能自动将另一个更富有的人设置成解决一切的救星。\n\n## 分阶段指导\n\n### 头脑风暴\n\n给出吸引、冲突与最终关系方向不同的方案；保持作者指定的关系对象。\n\n### 骨架\n\n安排靠近、受挫、重新理解与关键选择，标出关系变化的事件。\n\n### 正文\n\n写出具体相处的吸引力、双方不同的表达方式和心动后的行动。\n\n### 评议\n\n检查关系是否通过行动发展，回报是否由前文支持，而非只在结尾宣告相爱。\n\n## 避免套路\n\n不要只用外貌财富解释吸引；不要用误会拖满全书；不要让道歉或危险营救代替持续改变。\n",
      "summary": "以主要人物的亲密关系发展组织故事，结局兑现有依据的相爱与共同未来。",
      "version": 1,
      "status": "enabled"
    },
    "primary_genres/womens_fiction": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: primary_genres\ntag_id: womens_fiction\ncommitments:\n  core_promise: 让女性主人公的具体欲望、关系和自主选择推动全书变化。\n---\n\n# 女性小说\n\n## 读者期待\n\n读者能够认出她生活中的压力与渴望，在复杂处境里看见她逐渐取得行动空间。\n\n## 故事作用\n\n先建立主人公想保留、想改变和不敢承认的东西。她的选择同时影响关系、身份和实际生活，别人不能代替她完成关键决定。\n\n## 发散方向\n\n- 成长可以发生在事业、照护、亲密关系、友谊或独处之中。\n- 起点可为不愿再承担旧角色，也可为主动追求此前未有的生活。\n- 结局可以保留亲密关系，也可以离开；自主不等于只能独身。\n\n## 组合规则\n\n结合第二次机会时，自主与重新选择伴侣应同时成立；与家庭生活结合时写出日常劳动和情感价值，避免把家人全部写成阻碍。\n\n## 分阶段指导\n\n### 头脑风暴\n\n设计她真正想要的生活和代价不同的主要路线。\n\n### 骨架\n\n将她的认识变化绑定到行动、关系反应和持久后果。\n\n### 正文\n\n保留她矛盾、幽默、欲望和缺点，不让旁白替她宣布立场。\n\n### 评议\n\n以实际选择和后果判断成长，不以独白口号或离婚状态判断。\n\n## 避免套路\n\n避免所有女性相同的受辱逆袭；不要用获得新伴侣作为自主的唯一证据。\n",
      "summary": "让女性主人公的具体欲望、关系和自主选择推动全书变化。",
      "version": 1,
      "status": "enabled"
    },
    "primary_genres/horror": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: primary_genres\ntag_id: horror\ncommitments:\n  core_promise: 建立会升级的恐惧来源，让人物对威胁的应对改变处境并产生后果。\n---\n\n# 恐怖\n\n## 读者期待\n\n让熟悉事物变得不可靠，使读者既害怕接近真相又想继续读。\n\n## 故事作用\n\n确定恐惧来自什么、人物为何难以退出、哪些信息尚不确定。危险需要边界和因果，恐惧不能仅靠暗房、天气与突然惊吓。\n\n## 发散方向\n\n- 恐惧来源：熟人行为失常、空间与记忆不一致、群体默契或未知生物。\n- 呈现方式：逐步逼近、认知误差、可观察异常的重复变形。\n- 代价：安全、信任、身份或某种珍贵关系的损失。\n\n## 组合规则\n\n若作者要求现实解释，不能自行加入鬼魂。与家庭背叛结合时，让亲密信任与危险相互影响；与甜蜜组合可以用温情建立将被威胁的关切。\n\n## 分阶段指导\n\n### 头脑风暴\n\n提出不同的恐惧机制与求生/求真路径，明确现实或超自然是否待定。\n\n### 骨架\n\n安排威胁升级、认识变化和有铺垫的回应。\n\n### 正文\n\n通过感知、信息差和行为后果形成恐惧，保留人物可理解的行动。\n\n### 评议\n\n核对威胁规则、升级因果和回报；不能以惊吓次数判断质量。\n\n## 避免套路\n\n不要无限新增规则逃避解释，不要靠人物无故犯蠢推动危险。\n",
      "summary": "建立会升级的恐惧来源，让人物对威胁的应对改变处境并产生后果。",
      "version": 1,
      "status": "enabled"
    },
    "settings/contemporary_urban": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: settings\ntag_id: contemporary_urban\ncommitments:\n  core_promise: 让当代城市的空间、工作和资源条件实际影响人物行动。\n---\n\n# 现代城市\n\n## 读者期待\n\n让读者从具体通勤、职业、人际距离和生活成本中感到这座城市。\n\n## 故事作用\n\n用人物的日常路线、可用资源和制度背景组织相遇与阻力。时代工具和职业条件要符合设定，城市不是几个地标。\n\n## 发散方向\n\n- 生活半径：同楼陌生人、跨城伴侣、紧密社区或高流动职业圈。\n- 资源压力：时间、住房、照护、职业机会或社会形象。\n- 空间反差：公开场合与私人生活，繁忙街道与无人知晓的角落。\n\n## 组合规则\n\n与家庭生活结合时，让外部工作和城市资源改变家庭分工。不能默认每个城市故事都是豪门或职场权斗。\n\n## 分阶段指导\n\n### 头脑风暴\n\n用环境提出情节机会和现实阻力，不替作者固定具体城市。\n\n### 骨架\n\n将关键行动绑定到可信地点、时间和资源条件。\n\n### 正文\n\n把环境放入人物行动和感受，而不是开章介绍城市百科。\n\n### 评议\n\n检查环境是否真正影响选择，以及时间和空间是否自洽。\n\n## 避免套路\n\n避免用品牌和地标堆砌都市感；不要用任何行业都通用的空泛办公室。\n",
      "summary": "让当代城市的空间、工作和资源条件实际影响人物行动。",
      "version": 1,
      "status": "enabled"
    },
    "settings/family_domestic": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: settings\ntag_id: family_domestic\ncommitments:\n  core_promise: 共同生活的空间、劳动、资源和亲密习惯必须影响关键关系与行动。\n---\n\n# 家庭生活\n\n## 读者期待\n\n读者既能感到家的温度，也能体会被日常掩盖的失衡；同一动作和物件可在关系变化后有不同意义。\n\n## 故事作用\n\n把吃饭、照护、共用财物、时间分配等日常条件变成具体选择的背景。不要把每个动作解释成规则或协议。\n\n## 发散方向\n\n- 同一屋檐：共同生活中的距离、分居后必须面对的事情、代际同住。\n- 关系线索：一个被改变的习惯、一直被忽略的劳动、一件记忆不同的物件。\n- 回报：相同场景里不同的行动，真正分担或主动归还选择。\n\n## 组合规则\n\n与背叛结合，让真相进入具体生活；与第二次机会结合，通过不同于过去的日常行动证明变化。宫廷或末日也可有家庭生活，不自动当作冲突。\n\n## 分阶段指导\n\n### 头脑风暴\n\n给出家庭处境怎样制造冲突和温情的方案，不默认新增孩子。\n\n### 骨架\n\n安排日常场景的变化与重要情绪回报。\n\n### 正文\n\n用对白、动作、习惯和空间距离写出亲密，不写家庭事务流水账。\n\n### 评议\n\n检查日常细节是否推动理解和关系变化，而非只是布景。\n\n## 避免套路\n\n不要把家人都写成恶人；不要把照护清单和财产条款当作全部故事。\n",
      "summary": "共同生活的空间、劳动、资源和亲密习惯必须影响关键关系与行动。",
      "version": 1,
      "status": "enabled"
    },
    "story_types/domestic_betrayal": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: story_types\ntag_id: domestic_betrayal\ncommitments:\n  core_promise: 重要亲密承诺确实遭到背弃，发现、伤害与主人公的回应必须改变关系和生活。\n---\n\n# 家庭背叛\n\n## 读者期待\n\n让读者理解她为什么信任、为什么受伤，以及她面对真相后能够做什么。婚姻背叛与出轨是重要分支，不能用无关误会消除真实伤害。\n\n## 故事作用\n\n建立双方共享的承诺、被隐瞒的行动和真相进入生活的路径。让受伤方有缺点和主动行动，让背叛方承担具体后果。\n\n## 发散方向\n\n- 背叛时机可选：新婚磨合、孕期、共同事业成功之后、长期照护期间；不默认全部采用。\n- 发现方式可选：共同记忆不一致、生活安排的矛盾、第三方行动或主动坦白。\n- 背叛可以是情感、性、资源或亲属忠诚的背弃；作者明确指定婚外情时不得改成单纯财产纠纷。\n- 后续可以争取关系、离开或重建生活，需服从所选故事承诺。\n\n## 组合规则\n\n与第二次机会结合，设计真实背叛之后的可信重建，不能靠“其实没出轨”擦除冲突。与女性成长结合，关键决定由她做出。与怀孕结合，孕期处境要改变行动条件，不能只作为刺激标签。\n\n## 分阶段指导\n\n### 头脑风暴\n\n围绕不同背叛时机、发现方式与关键选择提出总体路线；不要只换职业名字。\n\n### 骨架\n\n铺设可信线索、真相冲击、行动后的反应，以及相应结局回报。\n\n### 正文\n\n用具体亲密记忆与当前行动制造情感落差，给人物愤怒之外的复杂感受。\n\n### 评议\n\n核对背叛是否真实影响因果、人物是否主动、回报是否处理过伤害。\n\n## 避免套路\n\n不要机械堆叠公开羞辱、恶毒配角和无成本反击；不要用反复偷听拖动故事。\n",
      "summary": "重要亲密承诺确实遭到背弃，发现、伤害与主人公的回应必须改变关系和生活。",
      "version": 1,
      "status": "enabled"
    },
    "story_types/second_chance": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: story_types\ntag_id: second_chance\ncommitments:\n  core_promise: 已破裂的主角关系最终重新开始；信任恢复须由持续行动和关键选择赢得，不能以换伴侣或独自新生替代。\n---\n\n# 第二次机会\n\n## 读者期待\n\n读者既记得旧关系为何值得留恋，也相信伤害并未被轻轻抹去；重新靠近令人期待，最后的重建有情感回报。\n\n## 故事作用\n\n明确是哪一对主角重新获得机会、破裂原因是什么、过去缺失的东西怎样改变。重建不是受伤一方再次忍让，而是双方重新选择关系。\n\n## 发散方向\n\n- 重逢方式：共同目标、无法回避的日常事务、偶然相遇后的主动靠近。\n- 改变证据：没有奖赏时仍履行责任、放弃原先利益、承认曾经拒绝面对的真相。\n- 重建路径：先恢复合作再靠近，先重新理解后检验，或一方不求复合仍作改变。\n- 回报形式可为重新约会、共同生活或明确的新承诺，不必婚礼。\n\n## 组合规则\n\n与家庭背叛结合，保留伤害与责任，让改变经受新的考验。与女性成长结合，重新选择伴侣必须是自主决定。若构想明确永不复合，先让作者选择改标签或改结局，不能私自解释为别人获得机会。\n\n## 分阶段指导\n\n### 头脑风暴\n\n提出不同重建路径，所有候选都兑现原主角关系重新开始；关系对象不明时作为首轮重大问题。\n\n### 骨架\n\n安排旧伤、具体改变、信任试验和最终互选，变化需要足够篇幅。\n\n### 正文\n\n写出熟悉与陌生并存的相处、小心靠近和行动证明；不跳过复杂余波。\n\n### 评议\n\n检查是否兑现关系对象和结局承诺，重建是否有累积依据。\n\n## 避免套路\n\n不以一次道歉、怀孕、孩子劝和或救命事件直接完成复合；不让性吸引抹去所有问题。\n",
      "summary": "已破裂的主角关系最终重新开始；信任恢复须由持续行动和关键选择赢得，不能以换伴侣或独自新生替代。",
      "version": 1,
      "status": "enabled"
    },
    "story_types/werewolf": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: story_types\ntag_id: werewolf\ncommitments:\n  core_promise: 狼人身份、身体或群体机制必须改变关键人物选择与故事后果，不能只保留称谓和外观。\n---\n\n# 狼人\n\n## 读者期待\n\n提供超出普通人类关系的力量、感知、归属和亲密张力，并让人物保有自主选择。\n\n## 故事作用\n\n从本书需要出发建立少量有效的能力、限制与族群规则。世界规则要能被行动检验，而不只是写在背景说明中。\n\n## 发散方向\n\n- 能力机制可从气味、感知、变形、治疗或群体联系中选择，不全量堆叠。\n- 社会结构可以是紧密家族、分散社区、竞争族群或隐于都市的组织。\n- 亲密联结可以是可拒绝的、需要经营的或代价不对称的，不预设天命覆盖个人意愿。\n\n## 组合规则\n\n与婚姻背叛结合，让狼人机制改变发现、利益与回应；与第二次机会结合，能力或联结不能自动替人物恢复信任。未选择的设定例子不变成本书事实。\n\n## 分阶段指导\n\n### 头脑风暴\n\n提出因机制不同而产生不同故事的路线，避免默认Alpha救赎受辱女主。\n\n### 骨架\n\n规则、限制和代价进入关键转折，保持全书一致。\n\n### 正文\n\n通过感官、身体经验、行动和关系写出非人特征，避免反复解释规则。\n\n### 评议\n\n核对去掉狼人设定后因果是否仍原样成立；如是，需指出实际缺失。\n\n## 避免套路\n\n不要只加Alpha/Luna名号；不要用临时新增能力解围；不要以命定关系取消同意与选择。\n",
      "summary": "狼人身份、身体或群体机制必须改变关键人物选择与故事后果，不能只保留称谓和外观。",
      "version": 1,
      "status": "enabled"
    },
    "story_types/female_growth": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: story_types\ntag_id: female_growth\ncommitments:\n  core_promise: 主人公的自我理解通过越来越自主的行动发生变化，重要选择有持久后果。\n---\n\n# 女性成长\n\n## 读者期待\n\n看到她从习惯退让或被定义，走向认识自己的欲望，并为新生活付出实际努力。\n\n## 故事作用\n\n成长包括错误、犹疑、关系摩擦和能力积累，不是一夜变成完美强者。先建立她目前保护自己的方式，再让其受到考验。\n\n## 发散方向\n\n- 成长起点：不敢提出需要、习惯接管一切、以被需要证明价值、用成功回避亲密。\n- 行动路径：学习新能力、重新分配责任、追求愿望、承认自己的伤害。\n- 回报：新的选择空间、关系中的平等、具体的能力与归属。\n\n## 组合规则\n\n与第二次机会结合可以自主重建关系，成长不必以离开所有人证明。与狼人结合让身体、归属和自主发生具体互动。\n\n## 分阶段指导\n\n### 头脑风暴\n\n提出不同内在矛盾及行动路径，避免单一受辱逆袭。\n\n### 骨架\n\n将认识变化落实到关键选择和后果，不只新增成长总结。\n\n### 正文\n\n让人物通过行动、对白和失败表现变化，保持缺点与独特声音。\n\n### 评议\n\n检查成长是否有过程及代价，是否由主人公完成。\n\n## 避免套路\n\n不要把口号当成长；不要让万能救星替她解决每个问题。\n",
      "summary": "主人公的自我理解通过越来越自主的行动发生变化，重要选择有持久后果。",
      "version": 1,
      "status": "enabled"
    },
    "tones/angst": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: tones\ntag_id: angst\ncommitments:\n  core_promise: 让情感痛感来自读者已在意的人物、关系和具体失去，并提供持续发展的情绪回报。\n---\n\n# 虐心\n\n## 读者期待\n\n使读者感到心痛、不甘或矛盾，同时想知道人物将如何回应，而不只是被痛苦消耗。\n\n## 故事作用\n\n先建立值得在意的亲密和愿望，再写伤害与落差。痛感可以来自错过、背弃、无法两全，不只羞辱或身体受伤。\n\n## 发散方向\n\n- 用旧日温柔与当下距离对照，也可用未被说出的需要和理解来得太晚。\n- 轻重节奏可由压抑转爆发、希望后失落、平静中逐渐明白。\n- 给读者温暖、魅力和小回报，让下一次损失有重量。\n\n## 组合规则\n\n与甜蜜结合时可以先痛后甜或交错，首轮说明过程；与第二次机会结合不以持续虐待代替可信修复。\n\n## 分阶段指导\n\n### 头脑风暴\n\n提出主要情绪落差与回报方式，不要求作者决定每个细小情绪。\n\n### 骨架\n\n安排情感累积、转折、喘息和结局回应。\n\n### 正文\n\n用具体记忆、动作与感知表现痛感，避免重复内心总结。\n\n### 评议\n\n根据实际段落检查是否有情绪积累和变化，而非统计悲惨事件。\n\n## 避免套路\n\n不要无休止误会、反复羞辱或把人物写成单纯受害装置。\n",
      "summary": "让情感痛感来自读者已在意的人物、关系和具体失去，并提供持续发展的情绪回报。",
      "version": 1,
      "status": "enabled"
    },
    "tones/sweet": {
      "markdown": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: tones\ntag_id: sweet\ncommitments:\n  core_promise: 让温暖与亲密通过互相看见、回应和照顾成为可感知的阅读回报。\n---\n\n# 甜蜜\n\n## 读者期待\n\n读者能从人物之间独有的默契和行动中感到安心、心动或快乐。\n\n## 故事作用\n\n甜来自对具体人物的理解和主动回应；礼物和告白要回应此前建立的需要。保留冲突和不同意见，不把一方写成毫无自我的照料者。\n\n## 发散方向\n\n- 亲密方式：共同玩笑、细小默契、为对方保留空间、笨拙但准确的照顾。\n- 节奏可以轻松、克制或热烈，按主类型和作者选择组织。\n- 回报可以是一场普通晚餐或一次认真倾听，不限定昂贵消费。\n\n## 组合规则\n\n与虐心组合可以形成变化，但不可把旧伤当作甜蜜的装饰；与第二次机会组合让甜源于新的相处方式。\n\n## 分阶段指导\n\n### 头脑风暴\n\n提出具有角色个性的亲密看点，并与核心冲突相连。\n\n### 骨架\n\n让亲密小回报逐步累积，在重要转折前后有意义。\n\n### 正文\n\n通过互动、节奏与具体回应呈现，减少旁白宣告幸福。\n\n### 评议\n\n检查甜蜜是否来自人物关系，是否因剧情推进而变化。\n\n## 避免套路\n\n避免所有角色同样会说情话；不要用宠溺控制替代互相尊重。\n",
      "summary": "让温暖与亲密通过互相看见、回应和照顾成为可感知的阅读回报。",
      "version": 1,
      "status": "enabled"
    }
  },
  "template": "---\nschema: novel-creative-element.v1\ncatalog_version: novel-types.2026-10\naxis: {{axis}}\ntag_id: {{tag_id}}\ncommitments:\n  core_promise: {{选中后必须兑现的一项核心承诺}}\n---\n\n# {{标签名称}}\n\n## 读者期待\n\n{{读者期待的感受与回报}}\n\n## 故事作用\n\n{{如何影响冲突、行动、关系、背景或表达}}\n\n## 发散方向\n\n- {{可选方向一；例子不是默认事实}}\n- {{可选方向二；应有因果差异}}\n\n## 组合规则\n\n{{与其他类型如何共同作用；需要作者决定的真正冲突}}\n\n## 分阶段指导\n\n### 头脑风暴\n\n{{如何提出不同总体方案}}\n\n### 骨架\n\n{{如何落实到关键事件与回报}}\n\n### 正文\n\n{{如何通过场景、行动与语言呈现}}\n\n### 评议\n\n{{从实际文本检查什么}}\n\n## 避免套路\n\n{{常见失误与更好的设计方向}}\n",
  "premise": "一对共同创业十年的夫妻，妻子发现丈夫隐瞒了一段婚外感情。她不想再以退让维持家庭，但也想知道，这段关系是否还有真正重新开始的可能。"
};
