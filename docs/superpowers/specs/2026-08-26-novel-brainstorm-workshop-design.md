# Novel Brainstorm Workshop Skill Design

## Goal

把客户给出的粗略小说方案转成经过头脑风暴、结构取舍和用户确认的完整创作 Prompt，并提供 Novel OS 一次性运行命令。Skill 默认完成设计与交付，不自动调用模型。

## Approved interaction model

1. 检查当前 workspace、既有 Prompt、项目和 canon。
2. 从粗略方案提取题材、受众、情绪承诺、人物、冲突、目标、代价、结局和篇幅。
3. 最多询问 5 个高影响问题，每次只问一个；低影响细节由 Skill 补全并标记为假设。
4. 提供 2–3 个有实质差异的结构方案，说明取舍并推荐一个。
5. 分段展示故事契约、人物关系、规则/秘密/时间线、章节结构和质量门槛，每段等待确认。
6. 设计获确认后生成完整 Prompt 文件，运行 `prompt_intake` 校验，并输出实际启动命令。

## Cross-genre model

固定使用四层故事承诺、五股故事力量、角色能动性、章节因果链、悬念兑现台账和质量门禁。根据主类型切换冲突载体与特殊台账：

- 现实情感：关系、财务、照料、法律和生活资源；
- 悬疑推理：证据、在场证明、知识边界和线索回收；
- 奇幻/玄幻：规则、限制、代价、例外和阵营；
- 科幻：技术边界、权限、能源、延迟和系统后果；
- 历史/架空：年代、制度、阶层、地理和可用知识；
- 职场/校园：决策权、预算、期限、评价和利益相关者。

混合题材必须指定一个主引擎和一个辅助镜头，并明确规则冲突时的优先级。

## Artifacts

- Skill source: `skills/novel-brainstorm-workshop/SKILL.md`;
- Progressive references: `references/genre-adapters.md`, `references/prompt-contract.md`, `references/quality-gates.md`;
- Generated novel prompt: `prompt/<title>.md` when Novel OS is present;
- Generated run command: uses `./venv/bin/python`, `approval auto`, `quality_policy evidence_v1`, five transient retries, and two bounded quality repairs;
- Validation: `prompt_intake` result must expose the approved title, genre, language, chapters, words, audience, tone, and premise.

## Safety and continuity boundaries

- Existing projects and prompts remain untouched unless the user explicitly requests replacement.
- Existing canon is read before a continuation or revision; promoted chapters and evidence receipts are preserved.
- The final prompt separates user decisions from Skill assumptions.
- The model run is a user-controlled handoff. Durable evidence/canon integrity issues remain visible instead of being silently compiled.

## Validation decision

The user chose not to establish a cross-genre eval set in this iteration. Static validation is still required: Skill Creator `quick_validate.py`, package validation, source/installed directory comparison, and Git whitespace checks.

