# c97e8ae5aef6 结局检查诊断

2026-10-14，任务《She Still Loved Them, but the Choice Was Hers》。本轮仅诊断和隔离实验，没有修改正式小说、状态、检查点或提示词，没有重启、部署、重新生成或调用模型。

## 结论

本次六条错误对应五项结局义务，正式正文已有对应情节。失败发生在“模型审核文字 → 结构化提案 → 正式状态 → 结局台账”之间的证据记录，而不是小说缺少这五项收束。

15 章的 `chapter.promote` 均已完成。`ending.review` 在 2026-10-14 05:03:15（上海时间）约 0.16 秒内被本地确定性规则阻断；任务为 paused，尚未进入后续整书检查及编译。结局检查不调用 LLM，不重新通读正文，只读已提交状态和结局合同。因此重复同一个检查不会自行补齐缺失证据。

项目目录：`docker-data/projects/she-still-loved-them-but-the-choice-was-hers-4cc8bbb8`。下列正文路径均相对于该项目。

| 报错义务 | 正式正文中的完成证据 | 记录缺口 |
| --- | --- | --- |
| L01 / bounded_counsel_delivered | `outputs/manuscript/chapter_003_final.md:47` 有限委托；第 7 章第 21、43 行区分责任边界；第 9 章第 11–27 行交付已生效保护、通知和停止交易；家庭选择由 Eleanor 在第 12–14 章完成 | L01 明确是 Marian Reed，但 arc_stage 仍 beginning，outcome 空。第 15 章 Scribe 把完成信息写入 Character_References.note，仅保存为 absence_note；Guardian 因提供的上下文缺身份映射及早期专业场景，未确认 L01 |
| payoff_004 | `outputs/manuscript/chapter_008_final.md:1` 至第 15 行，取消可选装修、保留过渡预算、限定维修范围、完工并付款 | 第 8 章 Guardian 报告第 9 行明确 paid，但仅为自由文字，parsed proposal 没有 payoff_events |
| payoff_007 | `outputs/manuscript/chapter_012_final.md:5` 至第 67 行，保护文件、拒售撤赠、资金通知、先分配房款后要求同意 | paid 条目写在 New_Facts_Established 下，没有进入 payoff_events |
| payoff_008 | `outputs/manuscript/chapter_013_final.md:5` 至第 43 行，展示女儿邮件、反驳被动参与、承认真实亲情但不免除责任 | `- Payoff_Events: ...` 带字段名前缀的条目被归入 New_Facts_Established |
| payoff_009 | `outputs/manuscript/chapter_014_final.md:5` 至第 87 行，收回伸出的手、拒绝资助、请丈夫离开、不追赶或再付款 | 同上，paid 条目未进入 payoff_events |

第 8、12、13、14 章正式 payoff_events 均为空。第 15 章对四项只记录 recalled，按规则不能代替当章兑现的 paid 证据。该区别本身合理，不应为了通过结局检查把所有 recalled 改成 paid。

已核对第 3、7、8、9、12、13、14、15 章 final 正文 SHA-256 与 committed promotion receipt 一致；并非只存在于未批准草稿或后续手改正文。样式阶段没有丢掉这些事件：Guardian 原始解析及其不可变提案里就已缺少对应字段。

## 框架原因

1. **输出协议不一致。** `agents/continuity_guardian/prompt.md:246` 要求严格遵循既有字段且不增加 state fields，后面的必填列表和最终示例没有 Payoff_Events / Arc_State_Updates / Ending_Evidence；`agents/scribe/prompt.md:233` 同样限制旧字段；而 `core/orchestrator.py:3029` 的任务模板要求新字段。保存的第 12、15 章 validation_prompt 仍含这一新模板。实际 Codex 适配器把 ROLE 和 TASK 拼入同一消息，不能把此冲突说成 OpenAI 协议层 system 角色压过 user 的已证因果。
2. **任务模板本身有错误。** `core/orchestrator.py:3032` 在 Arc_State_Updates 的列表项前再次写 Arc_State_Updates，解析器会把前缀连同角色 ID 视为实体名；第 3036 行又用打开标签结束 CONTINUITY_REPORT。这是可复现的通用缺陷，但本次四项 payoff 的直接丢失位置是自由文字或 New_Facts_Established，不能把所有漏记都归于重复前缀。
3. **解析及批准链缺少漏记检测。** `core/state_parser.py:119` 只在正常字段标题下收集对应列表；放错字段的事件被当普通事实保存。`parse_continuity:249` 以字典覆盖合并两个块，不能保障较早块的事件被保留。正式批准能通过，却没有及时暴露“报告宣称 paid，提案没有该事件”。本次数据证实解析时已漏记，模型报告 WARNING 不会自动补齐这些字段。
4. **配角完成证据与最终核验脱节。** `core/orchestrator.py:1904` 在终局窗口前仅注入 payoff directory，未提供完整角色 outcome 要求。律师主要工作在第 3、7、9 章，终局从第 11 章开始。修复时复核：`context_pack` 已为入选人物输出 ID，问题是 L01 不在当章选出的角色包内；不是渲染器统一漏掉 ID。第 15 章保存的验证提示词中只在合同出现 L01，在位置/情绪清单出现 Marian Reed，没有把两者及早期批准场景串成可核验的证据包。Guardian 正确地拒绝凭合同或备注证明已发生事件。

`core/ending_quality.py:337` 的最终检查只是暴露这些缺口；没有 ending 专用证据协调/修复分支。直接编辑正式 story_state 也不可靠：`core/pipeline_runner.py:1468` 的失败阶段恢复可能恢复上一个状态快照。恢复必须兼容提案、批准凭据和检查点，不能改旧提案或跳过 gate。

## 离线复现

证据目录：`/private/tmp/novelos-ending-c97e8ae5aef6-20261014/`。

命令：

```sh
/private/tmp/novelos-workshop-staged-env/bin/python /private/tmp/novelos-ending-c97e8ae5aef6-20261014/reproduce.py
```

复制正式项目后，使用现有生产 evaluate_ending：

- 原始副本精确复现 6 条错误。
- 仅在隔离副本按上述已批准正文人工补 4 个 paid 记录，剩下 L01 的 2 条错误。
- 再补已核实的律师身份和专业完成证据，原检查返回 pass。
- 副本内所有匹配 chapter_*_final.md 的文件（含同章副本共 30 个）SHA 保持；正式项目 836 个既有文件 SHA 全部不变；真实模型调用为零。

这是定位缺口的人工证据对照实验，不是已实现的自动修复，不代表已修改正式任务或已成功交付整书。仍须完成后续整书检查、出版文案和编译阶段。

当前运行容器与工作区的 ending_quality、state_parser、state_manager、orchestrator、pipeline_runner、Guardian/Scribe 模板 7 文件 SHA 一致。已有验证提示词与审核输出支持上述诊断，但没有据此声称保留了当时发往远端的完整 ROLE/TASK 字节快照。

## 修复优先级

1. 统一角色模板和调用模板的结局字段协议，修复重复字段与标签，增加真实格式回归。
2. 在章节批准前核对“应跟踪的义务、报告声明、可解析事件”是否一致。无证据时保持未确认；字段错位要明确报出，不能静默丢失或凭任意备注直接升级为已完成。
3. 给跨章/提前退场角色按需提供身份和相关已批准章节证据；已完成的早期职责可在发生时记录，无需最终章重新演一次。
4. 为此类既有任务增加带原文引用、正文哈希、原提案关联及审批记录的证据协调过程，补齐后从结局检查继续。保留原报告与旧提案，不重写 15 章、不降低 gate、不将所有回忆无条件视为兑现。

这是可影响其他使用相同提示词和解析链的小说的框架问题；不是每部小说都会触发，也不是本书的结局要求应一律放宽。
