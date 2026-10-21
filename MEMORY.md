# Novel OS 项目记忆

## 最新修复：2026-10-18 14:36 结局弧线回归与任务 3822f6ff360a

《When the Lift Doors Open》任务 `3822f6ff360a` 在 ending.review 被 `Character ruth_mercer arc stage is middle, expected resolution` 阻断。根因确认：第22章 Scribe/Guardian 已写 `resolution/100`，第24章 Scribe 的局部场景估计又写 `middle/85`，旧解析器直接覆盖角色索引；结局门只读当前索引，故看不到已发生的 resolution。不是第22章剧情缺失。

修复分两层：`state_parser` 新增 arc 单调保护，阶段/进度只能前进，回退尝试不覆盖索引并记录 `reported_stage`/`reported_progress`/`regression_ignored`；`ending_quality` 读取章节历史中的最高阶段，兼容已经回归的旧书而不改写章节记录。dry-run 于项目副本 pass。

首次恢复通过 ending.review/book.check，但在 publication.copy 暴露第二个问题：DeepSeek `deepseek-flash` + effort max 的 Guardian 调用把 8192 全部输出预算耗在 reasoning_tokens，content 为空；三次尝试均空。新增 DeepSeek 输出预算下限（low 8192 / high 16384 / max 32768），仍只按实际生成计费。部署后再次恢复成功：ending.review、book.check、publication.copy、compile、delivery.package 全 done，run completed；报告 pass/critical 0，30章交付 MD/HTML/DOCX/EPUB/PDF + H5 包。

交付 ZIP `outputs/deliverables/book-package.zip`，355581 bytes、43 members、CRC 通过，SHA256 `c6fe7f517a1819df09d515549fd8597f8922457fcfb8603c6b5f706ff29a1a5f`；报告 `book_completion_report.json` pass。全量后端回归通过（排除 3 个受限沙箱 `ps` 用例）；恢复前备份在 `/private/tmp/novelos-lift-arc-recovery-20261018T140641`。相关测试：弧线回归、历史 resolution 过门、LLM 输出下限。修复报告 `docs/reports/2026-10-18-ending-arc-regression-lift.md`；状态索引已从 `middle/85` 修正为 `resolution/100`（备份 `/private/tmp/when-the-lift-story_state-pre-arc-repair-20261018T143744.json`）。本次修复已提交并推送。
## 最新设计：2026-10-18 模型与服务商全页工作台（已实现并部署）

Settings 从“上方标题 + 一条连接列表 + 大量空白”改为全页 Model Atelier：整页工作台铺满视口，左侧标题 + 右侧深色 System Status，四段编号标签页横贯全宽；主体为“主工作区 + Live Topology 侧栏”，连接页使用双列连接卡与可点击的服务商模板目录，图像/文本/计费页复用同一工作台框架。Inspector 实时展示 TEXT / VISION / IMAGE 三条链路与连接、路由、模型、模板数量。桌面 1440、宽屏 2560、390px 均已截图检查；页面宽度填满可用区域，390px 无横向溢出。设计截图见 `docs/design/settings-workbench-2026-10-18.png`。已提交 `8199aea` 并部署到 5174；前后端 healthy，桌面 1440 实机截图与设计一致。

保留全部既有功能与文案：服务连接增删改测、模板点选会预选服务商、文本路由、视觉审阅、封面图像、费用估算、ProviderEditor。测试增至 43 文件 511 项通过；`tsc -b` / `vite build` / 改动文件 ESLint 通过。
## 最新开发：2026-10-18 DeepSeek 文本接入 + 独立视觉审阅（未部署）

用户批准 Settings 设计稿后实施，Codex 登录与图像 profile 保持不变。Studio 新增 `deepseek` provider 模板，只声明 `text_generation`；密钥仍写入 `studio_settings.secrets.json`。文本路由可直接设 DeepSeek；模型以 `/models` 实际返回为准（当前 `deepseek-flash`、`deepseek-v4-pro`），不再写死旧别名。DeepSeek 不进入 image profile，也不能被选为视觉审阅连接。`/models` 元数据含 `effort.supported_levels`（low/high/max，默认 high）、上下文/输出上限、输入模态与显示名；`model_details` 随连接保存并通过 `/model-catalog` 暴露，UI 按模型显示可用推理等级，DeepSeek 请求发送 `reasoning_effort`。切换连接会清空上一连接的模型/推理值，避免把 Codex 模型带进 DeepSeek 列表。

新增 `image_input` 连接能力与 `vision_routes.cover_review`：封面语义评审、独立盲读和海报建议走独立视觉连接（Codex/OpenAI/OpenRouter，或显式开启能力的 OpenAI Compatible）；旧配置没有 vision_routes 时，仅在 `cover_director` 连接支持图像输入的情况下安全回退。`LLMClient.complete_with_images` 对 DeepSeek 及未声明视觉能力的 provider 明确报错，不会把图片发给纯文本连接。视觉审阅未配置或调用失败时，封面评审降级为人工审核，海报建议标记不可用；图像生成继续使用 Codex/OpenAI 图像连接，未配置图像连接时入口禁用并提示，正文、编译与交付不受影响。海报策划在 DeepSeek 下将 max_tokens 限制为 8192。

费用估算新增 DeepSeek 两档参考价（`deepseek-flash` / `deepseek-v4-pro`，按峰值价、缓存命中/未命中与输出分别记录），默认价格表包含参考行，也可在旧安装中用「载入 2 个模型参考价」合并，不覆盖其它服务商；参考价仅作估算，实施时仍以官方价格页为准。

验证：前端 `vitest run` 43 文件 510 通过，`tsc -b`、`vite build`、改动文件 eslint 通过；后端全量除 3 个依赖 `ps` 的沙箱环境用例外通过（`tests/test_llm_cancellation.py` 的 codex/claude_cli 取消用例与 stubborn helper 清理用例在受限沙箱中无法执行 `ps`），provider/vision/pricing/LLM/cover/poster 专项测试通过。已部署到本机容器并完成线上验证：DeepSeek `/models` 实际返回 `deepseek-flash`、`deepseek-v4-pro`；`/model-catalog` 返回 effort `low/high/max`（默认 high）、上下文/输出上限和输入模态；文本路由切换连接会清空旧模型，思考等级可选。默认文本路由已切到 `deepseek-v4-pro`（`high`），workshop 继承默认（保留 900 秒超时），`cover_review` 视觉路由显式固定 Codex `gpt-6-astra`，封面图像 profile 仍为 Codex `gpt-image-2`。线上 `POST /studio/model-routes/default/test` 成功返回「我可以正常回复。」；直连 DeepSeek `reasoning_effort=low/max` 均返回 200。

## 最新修复：2026-10-16 23:44 头脑风暴恢复多轮讨论，已部署

用户只读排查 `102abfa791e6493ca363051d3e711f8a` 后授权按原设计修改。原任务仅选择主线/幸福结局/地点自定，22:58:51 即由模型 requests_delivery 自动切换交付；这是通用流程越界。现 `/turns` 新增 intent=discuss|deliver、默认 discuss，逐轮保存 turn_intent。普通发送输出阶段性提案/取舍/问题，不论已有骨架、完整brief或问题答完均不能切换交付或覆盖骨架。模型越界最多1次范围更正，仍越界则明确未完成、保留旧骨架/对话/问题；不保存擅自生成候选，不回填它续推，诊断 discussion_scope。

UI增加生成/更新完整骨架直接动作，包含尚未发送补充，无二次审批、最低轮数或可选字段填满要求。普通输入里写定稿要求也需用此动作提交，页面已说明区别；不以关键词/额外意图模型调用判断授权。新骨架明确交付直接story_design同次选择有效组织形式并在保存前冻结，省掉自动handoff调用；已有骨架/旧失败完整候选的显式修订保留原路径。暂停/重试持久意图；验证过的旧检查点可显式恢复，无意图/无检查点旧暂停默认讨论，候选不能授权。新讨论不复用旧交付run。原Skill文件未改，旧检查点skillhash兼容。

后端workshop全336通过；最终诊断类别边界/诊断/进度36通过；前端全505/43文件通过，build/tsc/lint/diff-check通过，独立审查的问题“降级清空未答问题”已修复回归。全部测试使用替身，未新增真实小说/模型调用。

旧 `102` 轮次23:43:01自然到45分钟上限error，未被中断；保存story_design+chapter_plan和publication/relationships_world/secrets_payoffs三个assembly部分，正式prompt仍空，3条对话/revision3，可显式恢复。23:44双次确认15作品+讨论空闲/无Codex子进程后部署，全部讨论+SQLite/config备份，23275既有业务文件不变，3后端文件/69前端资源SHA一致；healthy。浏览器正式页已验证两个操作及提示，未提交/恢复。

backend `sha256:569975b6031ab34bb0e1303b89f35a370cf622e308b8ece3e171af90263338c2`，frontend `sha256:39a64495b5de7730bc293608471f44396fb16a5bbf3d2f658c4aa61c3288444a`。审计 `/private/tmp/novelos-workshop-discussion-20261016/`，回滚 `novel-os-{backend,frontend}:pre-workshop-discussion-20261016`。报告 `docs/reports/2026-10-16-workshop-discussion-boundary.md`。未Git提交/推送，保留之前全部工作树改动。

## 最新处理：2026-10-16 21:15 旧 H5 高负载进程已清理

用户在下节诊断后明确授权“请帮我处理掉吧”。重新核对PID/启动时间/cwd/端口/完整进程组，仅对已确认9组（每组pnpm+next dev+next-server）发送SIGTERM；21:15:45共27进程全部正常退出，无SIGKILL，其余9个Next实例身份保留，目标9端口全部释放。清理脚本 `cleanup.py` 有精确白名单、身份复验、无活动TCP检查和防重放标记，不可盲重用旧PID。

8.155秒清理后测量：Next合计CPU618.47%→1.10%，压缩器13.97→5.72GiB、swap27.61→22.61GiB、压缩页速率1842→0MiB/s。未清零swap/删目录/改配置；其他浏览器与系统进程仍有独立负载。独立核验Novel OS前后端容器ID及StartedAt不变、running/healthy、5174/api/health=200，Docker/Novel OS无重启。21:16:53容器总CPU3.33%，Novel OS后端0.23%、前端0%。即时清理完成，统一开发服务生命周期/持久日志仍是预防性建议，未跨项目实施。报告 `docs/reports/2026-10-16-host-high-load.md` 已追加结果；审计 `/private/tmp/novelos-load-diagnosis-20261016/cleanup-*.json`。下节“未停止”仅为之前诊断阶段。

## 最新诊断：2026-10-16 21:02–21:12 宿主高负载（未停止服务）

用户要求排查旧H5 Next实例负载。只读实测18个next-server中9个高占用；8.202秒累计CPU50.73秒，约618%/6.18核心，本机10核心。三组：ai-novel-h5-meta-minimal-ui 70763/70764/80859/80860，meta-event-reliability-20261008 58625/58626/74404/74405，fix-zip-plan-version-validation 80060。九条pnpm根进程PPID1、存活5–9天，同临时项目有重复admin/storefront实例；两个临时项目package.json缺失、部分Next软链断开。三个代表PID的2秒样本100%主线程在TriggerUncaughtException→console，非普通编译热点。

相同Node26.7.0/真实Next15.5.21 logger隔离实验复现断日志socket+直接stderr.write→EPIPE→uncaughtException日志→再次EPIPE，30/67ms达硬上限100次；正常socket或独立流error监听不循环。真实九个输出fd为无对端unix socket，但低CPU兄弟也如此；**未取得实际异常code/message，EPIPE是有复现支撑的强候选机制，不能宣称线上第一触发器已证明**。明确运维问题是开发服务没有随任务/目录清理回收。

Docker可读：两次12容器合计3.94%/28.56%；Novel OS后端0.19%/25.03%、前端0.02%/0.01%，后端无Codex/Node子进程，非此轮数百%主因。32GiB机器swap已用27.61GiB，5秒窗有swap-in5.65MiB/s和高压缩/解压；不能归因全给Next。无停止/重启/业务代码配置修改/模型调用，合成实验均有界结束，现场负载未消除。后续若授权清理，先核对PID启动时间/cwd/任务，精确停进程组，勿盲杀node或重启Novel OS。报告 `docs/reports/2026-10-16-host-high-load.md`；审计 `/private/tmp/novelos-load-diagnosis-20261016/`。

## 最新交付：2026-10-16 封面工作室 v2 已实现并部署

用户批准 `docs/design/prototypes/cover-studio-v2/index.html` 方案并要求执行。现已实现作品入口默认 `skeleton_poster`；下节“尚未实现”是之前设计阶段记录，已由本节取代。完整报告 `docs/reports/2026-10-16-cover-studio-v2.md`，产品规格同步标记已实施。

新流程：完整骨架及明确附件冻结 hash，一次联合规划故事核心、主配角关系、四方向、标题配方；最多一次格式更正，无旧逐段分析/证据语义审核前置。`poster_staging` 允许宣传同框、姿态与象征合成，保持核心身份年龄关系/世界/作者禁项，人数按题材需要；不强制静止画面证明整章因果。四图串行逐张保存，完整图直接选择，无理由或审核门槛；最多一次组合后台建议，不自动返工。原图+用户提示精修一个 child；单图/失败批次显式重试，已存画稿只续本地排版；不自动重放未知远端结果。

已接通持久任务和幂等 alias、精确批准/版本保护、真实模型身份与未知用量、冻结单价汇率/费用活动分类、标题逐行字体大小颜色/描边阴影渐变/字体 SHA/预览导出同字节、选择与交付分别记录、新选图同步 EPUB 和 ZIP/H5 哈希。本地排版不调用模型。旧原生带字图不能假装无损去字。新前端两 Tab 蓝紫全宽，来源/历史/费用抽屉；方向可改人物/构图，新结构字段优先旧执行文本。旧 V11 图、分析、批准、记录不迁移；头脑风暴内部旧封面入口仍保留旧协议，不宣称已改版。

关键代码：`api/poster_{studio,tasks,image_service,advisory}.py`、`core/cover_poster_{planner,store,typography}.py`、`core/cover_{epub,delivery}.py`、`web/src/routes/{CoverStudioRoute,PosterStudio}.tsx`，费用与编译/交付挂钩一并更新。仓库封面 Skill 已区分新默认与 legacy；用户已批准新方案，旧 Skill 的逐段来源绑定要求不应重新套回新流程。

真实验收均使用隔离虚构骨架，未发送正式小说数据：8 题材规划各 1 次成功，95.86–122.39 秒；家庭样本 4/4 图片成功，首张 95.34 秒、整批 369.06 秒，1 次原图精修约101.88秒成功且父/兄弟SHA保持。最新标题合同另 1 次规划147.06秒，真实组合建议1次56.41秒；后者两调用45492报告tokens，费用null，未知推理用量不当作零。4个本地排版版本预览与导出SHA相同，0额外图。限制：短虚构骨架非长篇SLA；8题材只验规划，图只验家庭一本；第四案插画视觉仍偏摄影，原首轮排版暴露对比弱随后本地改进，不承诺所有小说质量保证。

验证：较早后端相关1649通过、前端全量494/42文件通过；最终后端专项234、前端竞态/人物专项26通过，范围重叠勿相加；lint/tsc/build通过。浏览器正式双Tab/来源/费用/历史四图和直接选择入口通过，独立审查已修复两项竞态与描述覆盖问题。

20:16上海完成前后端部署healthy。两次15作品无运行任务/无Codex子进程、作品+SQLite+配置备份；23214既有业务文件不变，19后端/69前端资源SHA一致。backend `sha256:628ed8973a271191d132338a02b4ecd22f64bd99ef888118969c170d3e4348e2`，frontend `sha256:02fc6574fc52ef4c93ea71c2b8c2dcee95f10680a7099daeadcfe648f100795a`。审计 `/private/tmp/novelos-poster-v2-20261016/`，rollback `novel-os-{backend,frontend}:pre-poster-v2-20261016`。未Git提交/推送，保留大量之前未提交改动。

正式 She Still Loved… 的原批次 `cover-54d9ef6eb59d4584bf192f571920ed01`、候选02 `candidate-42bd40c94bca4de09ca142317ebce113` 仍选中；新任务/方案0。ZIP 11045019字节完整有效，selected-cover SHA一致；**旧EPUB未回填精确选图字节，本轮未改旧交付包**，后续新选图或本地交付重建才应用新同步。不能声称历史全部EPUB已迁移。刷新封面页点“设计4个封面方向”即可测试新流程，无需再跑旧骨架分析。旧方案可在历史工作室看，“未设计”指新协议尚无方案。

## 最新设计交付：2026-10-16 封面工作室详细规格与交互稿（未接生产）

用户要求开始提供详细设计方案与设计稿。本轮新增 `docs/design/2026-10-16-cover-studio-product-spec.md`，以及隔离 HTML/CSS/JS 原型 `docs/design/prototypes/cover-studio-v2/`（README、8 张设计截图、4 张原创无字样图及完整提示词）。本机静态预览端口 5186；只绑定127.0.0.1，不连接正式API。沿用现有蓝紫/深蓝/白卡/全宽、仓库logo与Lucide图标，两个主Tab设计方案/候选封面，精修和标题排版独立工作面，费用/历史辅助。

新流程仍是待实施提案：完整骨架一次联合设计，正常1规划+4图+最多1后台组合建议；艺术化人物关系海报，无逐幕证明/人数配额，完整图直接可选。详细规格补齐版本保护、幂等、服务重启未知结果、图片已存后只续排版、费用/模型缺失、旧V11轻量适配、发布门槛和跨题材验收。

四次内置 imagegen 只用于虚构设计示例《After Thirty Years, I Choose Me》（人物/作者均为演示），没有把正式小说资料发送出去，也没有修改正式骨架或封面。第四张请求绘画、结果更像精修摄影，文档与UI诚实标注实际视觉，未宣称美术控制已验收。原图保存在原型assets/cover-1..4.png；书名由HTML/CSS独立叠加，提示词见IMAGE-PROMPTS.md。

原型已浏览器验证选图、改排版不覆盖已选版本、精修失败保留提示与重试、部分成功可选、全部失败可逐张恢复、刷新恢复、桌面/窄屏无横向溢出。生成/精修是明确标注的本地状态演示；精修成功不假装生成新图。费用虚构示例，下载仅演示JSON清单，不是假ZIP。真实异步后端、图片编辑、字体导出、历史存储和真实验收未实现。正式前后端未重启/部署、未Git提交推送。下一步若用户要求实施，应按详细规格全链路开发，不把本次原型当成生产已完成。

## 最新方案补充：2026-10-16 GoodNovel商业封面参考（尚未实施）

用户指定 goodnovel.com 作为设计参考，希望达到同类效果。本轮真实浏览首页、四本详情封面（Barren Luna's Secret、Jilted Alpha、Too Late To Love Me Mr Billionaire、Your Uncle's My Husband Now Back Off Ex）和Emotional Realism分类；只读视觉分析及代码复核，无模型/业务代码/部署变更。报告 `docs/design/2026-10-16-goodnovel-cover-reference.md`，原主方案同步修正。

关键调整：不以“至少三张多人”作为通用质量配额；双人/三角静态宣传姿态同样可由角色关系＋题材符号＋标题传递故事承诺，不能又强制动作/逐幕证明。关系型小说仍应充分探索主配角表达，避免泛化单人和无关联分格头像。美术按题材/受众选择：电影摄影、商业精修、半写实或插画可选，不将狼人年轻情侣风格套到成熟家庭故事；保留年龄身份但允许造型与商业布光。

当前真正技术差距：render_policy摄影硬锁/no beautification/禁插画；编译/验证阻断非摄影；默认保守造型；标题仅3字体ID、每行同字号/字体/色、固定相对行距。需贯通美术配方/规划合同/编译/评价和标题引擎（保序文字段、字号/字体/颜色层级、可发布字体、局部底衬等），不是prompt加“GoodNovel style”。未固定顶部标题或强制分格，不能误报。基础排版先行，复杂蒙版可后续；全流程仍一次联合规划，不新增美术审核链。仅抽样视觉判断，未证明站点设计工具或点击率；真实成图质量需跨题材验收。

## 最新审阅提案：2026-10-16 完整骨架直接设计故事海报（尚未实施）

用户要求完整审阅封面工作室并给优化方案：步骤多/慢、单人过多，希望完整骨架直接设计电影海报式主配角故事场景，允许艺术化而不逐场景复刻。本轮只读代码/Skill/真实任务用量并看四张图；前后端healthy，无业务代码变更、重启或真实模型调用。方案见 `docs/design/2026-10-16-skeleton-poster-studio.md`，是提案而非已部署实现。

最新正式状态已变化：用户10/16 12:40–12:54生成 `generation-task-9db782de885948d58baa17a230a20885` / `cover-54d9ef6eb59d4584bf192f571920ed01`，4/4成功，16:05选中候选02。旧4失败批次仍保留，勿再称最新批失败。批准方向1e176…人数1/3/1/1，成图分别读文件单人、三人分格肖像、被子+单人、木屋+单人；问题从规划层已产生，非图片漏画。

实测该批845.40秒，4图片调用411.83秒、8次A/B文本审核397.83秒(164095报告tokens)，代码逐图生图→A→B串行。方向329e9…14分26秒/4调用/450880报告tokens，前两轮因缺focal_character_id与同一事件epoch字符串不完全相等修订。首次8段骨架无失败参考路径20分析+2方向+4图+8审核=34应用调用，已完成分析可复用，不能说每批34。当前V11放松表达但仍继承复杂V9字段/完整分析门槛，物理互动严格事件绑定使艺术自由偏向肖像/物件。

建议新增版本化skeleton_poster流程：完整骨架一次联合设计紧凑故事核心/关键角色关系/四方向；关系型小说充分探索有意义的主配角构图，人物数随故事需要，不设多人配额；poster_staging允许非字面场景，不逐动作证据审查，守核心身份关系/世界/明确作者约束。默认1规划+4图+最多1独立后台组合建议，协议问题最多1定点更正，无默认前置分段/语义审核；评价不阻断可见/选择。精修原图+用户提示直接单张编辑，纯标题本地调整。完整原文不裁切，预算预检，不承诺固定速度/模型同脸/任意长度均适配。

保留蓝紫全宽，2主tab设计方案/候选，来源/费用/历史辅助；一键按方案生成整合批准与幂等调度。旧analysis/批准/图/费用不改，新增协议验证分派，不伪造complete分析。候选级版本保护允许生成中选已有图；评价按批次终态成功子集和图片hash幂等，旧V11图轻量精修适配。新全链路打通再feature-flag开放，不能半交付。后续需用户指示实施；本轮仅方案文档和此记忆变更，未Git提交/推送。

## 最新交付：2026-10-16 整批出图失败后可显式重新生成

用户报告四图失败后再次点击被“本方向的整批出图额度已使用”阻断。真实方向 `direction-1e1760ee0e4f499ab96bee5a1bfd7e91` 已批准；任务 `generation-task-e45e3d36d03648c28dbfc6c0ed9558da` / 批次 `cover-99868a4e44f545d78c4aa1191812a9e5` 于10/15 19:00–19:04上海4/4失败、0成品。四次错误含rmcp Transport channel closed / HTTP429，stderr截断不足以归属具体远端；宿主文本调用完成、图片实际模型未观测。骨架/分析/批准hash有效，前一轮方向已完成；不用重分析或重规划。

根因是approval.batch_reserved对方向永久锁定。修复core/cover_workflow增加显式generation_batch_id的逐批reservation，不清零旧预算，每批仅一次；新候选验证receipt。API/前端每次主动点击request_id，持久绑定方向/hash/count，同key终态/重启均只返回原task；活动任务合并保存alias，避免延迟重复POST再次收费。未带key旧调用仍oneshot，CLI未扩张，单张修复批准不能整批生成。前端sessionStorage按owner/direction/hash/count保留未确认intent，刷新只读，canonical或alias确认后清理；不同显式方向各自保留key，避免无限锁住。失败/中断显示“重新生成4张封面”，系统样式保持。HTTP局域网无randomUUID时使用getRandomValues安全fallback。

先红测精确旧错误，再后端239回归、前端全量462通过；最终RNG变更专项19通过，lint/tsc/build通过。首次前端默认并发一个旧workshop测试超时，2workers全量通过、未放宽断言。独立审查发现alias恢复与HTTP RNG兼容已修复。真实作品副本及禁网只读新镜像两次离线验证连续2新批次、8本地图片替身，重复POST无额外任务，旧批次及正式1005文件SHA保持；真实模型0。

12:35上海前后端部署healthy；双次15作品空闲检查/无Codex子进程、target+SQLite/config备份；23174既有业务文件SHA保持，4后端模块+65前端资源SHA一致。backend `sha256:c6dbe97bcbc0133249915b6d4f626ffba8f4cb34ee6f4ee916b1589a4932c178`，frontend `sha256:5b4b5642ef0161f1ce12adaa49cfd97867f502ebcff8cabee9a4af89d5865c53`。源镜像使用部署前当前运行实例（10/15 20:15启动），非盲按上一节旧image。正常GET最新仍原失败task/failed4，未自动重试/规划/出图。用户刷新后可基于已批准方向显式再生成；远端429本身未宣称已修复。

报告 `docs/reports/2026-10-16-cover-batch-retry.md`；审计 `/private/tmp/novelos-cover-batch-retry-20261016/`；rollback `novel-os-{backend,frontend}:pre-cover-batch-retry-20261016`。未Git提交/推送。

## 最新交付：2026-10-15 真实语义修订请求超限二次修复

用户反馈“没有修复”，实际 `direction-task-8921dc256ff74d0fa354aa5361856a36` 于08:23–08:28上海失败。上一轮377906仅余6094字节；真实第一轮返回含空 `interaction_edges[].affected_character_ids`，加入完整回复和修订合同后精确422108，第二次模型调用前失败，completed_model_calls=1。不是审核太严格或刷新问题；上次修复没有覆盖真实后续体积。诊断目录 `3fbe0917aec548078b9588a4fb83a00c`。

新增 `core/cover_campaign_source_index.py`：v2无损请求仍大于320000时尝试明确v3模型投影，只取更小表示；完整167912字节冻结原文、431证据ID/原文范围/所有其他元数据、全局故事和方案保持。重复证据statement注释不逐轮传输，原分析记录保持；现有ledger需要的摘要单独保留，自定义条目/patch哈希保持。合同诚实区分模型投影与无损压缩；本地补回声明省略字段后逐字段比对。源片段不裁切，字节/token上限和模型次数保持。

真实已保存首轮回复离线重放：创建300869、语义修订345071（原422108）、独立审核324013、格式修复353357，均低于384000。审核阶段使用只在内存补齐人物数组与事件时间/地点的协议夹具，未批准设计/事实、未保存更正。335既有回归+21 v3专项=356通过，独立审查无阻塞。真正更大的未来唯一内容仍受限，未承诺任意长度不超限。

14:43上海后端部署healthy，image `sha256:6bde01378d3610d235405650c06071b712454ebcf962c1d0ed54ffb786d72083`，3源码SHA一致。两次15作品无活动任务/无Codex子进程后备份target项目+SQLite；23135既有业务文件SHA保持。前端未更新。部署后容器实际代码四阶段重放，字节与本地一致、完整来源/431证据及历史方案回解验证通过。真实模型调用0，没有自动规划/核查/出图；旧失败历史保留。用户可刷新后“重新创建方向”，无需重做已完成骨架分析。

报告 `docs/reports/2026-10-15-cover-semantic-repair-budget.md`；审计 `/private/tmp/novelos-semantic-budget-20261015/`；回滚镜像 `novel-os-backend:pre-campaign-source-index-20261015`。未Git提交/推送。下节旧377906及仅6KB余量已由本节取代。

## 最新交付：2026-10-14 美术方向请求超限无损修复

用户报告 `create_campaign actual_bytes=573516 limit_bytes=384000`。实际任务 `direction-task-9d3131593b814507a8a73da6a5e7c00b` 于15:54上海在调用前失败，completed_model_calls=0。只读检查确认上轮正式child `analysis-10d91a69e14270ba76ed564870bab73d` 此时已complete；上节“待核查”是旧状态，不应回退。使用相同167912字节来源、431证据、brief/ledger/v11/4候选参数，精确离线复现573516。

通病为请求表示重复：完整原文外仍复制完整quotes和重复元数据。新增 `core/cover_campaign_source_transport.py`，完整可读原文片段共享、原证据ID不变、列字典与精确ledger视图复用，发送前完整回解逐字段比对；未知/不同ledger记录保留字面值。大于256000字节的v1请求才尝试，新表示仅更小时采用，最终384000字节/token上限保持。没有删故事/限定/引用，没有放宽审核或增加自动模型次数。创建实测 **573516→377906**（−34.1%），完整source/evidence/brief/ledger一致。

335相关测试通过，真实schema/semantic repair和commercial review构造器均覆盖；独立复核无阻塞。源断言与历史预算断言支持v2回解，保留旧失败证明。当前体积余量约6KB，不能保证任意更长的新回复或后续独有内容都装入上限；真正超限仍调用前停，不自动截断。

16:54上海仅后端部署，image `sha256:8a52e065c104446b3aa4d255444f340c82c806d331eced70dade03216ae93ac3`，2源码SHA匹配，两次15作品无运行任务/无Codex子进程，项目+SQLite备份，23127既有业务文件SHA保持。前端未更新。作品库汇总GET /api/projects检查较慢而超时，未据此重启；改为本地项目清单+每个作品只读deletion-preview取得正常任务状态后部署。汇总性能尚未在本次修改。

部署后容器实际代码离线重放377906/384000，完整167912字节来源和431证据回解一致，analysis SHA `f11f55fcfbd7f4f965e84aa9b9aa0985ee3648b10fefb6b09f6d20ebbde659b2`。真实模型调用0、未重做分析/规划/出图，旧失败任务历史保留。用户可刷新后明确“重新创建方向”，无需重新分析骨架。仅验证预算与信息完整，未宣称真实模型规划已通过。

审计 `/private/tmp/novelos-campaign-budget-20261014/`，报告 `docs/reports/2026-10-14-cover-campaign-source-budget.md`。rollback `novel-os-backend:pre-campaign-source-transport-20261014`。未Git提交/推送。

## 最新交付：2026-10-14 封面修订恢复入口已接通

用户反馈“仍报一样错误”并要求继续。确认上一轮仅部署底层局部修订能力，未登记正式child，也未接通页面。正常“继续未完成分析”仍POST耗尽父ID，core仅更新updated_at后重抛旧错误；真实副本重放0模型、31次历史attempts不变。正式父01:05→04:45 UTC只改updated_at，其余完全相同。

本轮API/core在入队/写前拦截同策略耗尽父，list/detail增加只读recovery。UI隐藏无效继续、父→child“查看修订结果”只读；child显示“局部修订已保存，待核查”，用户明确点击“继续核查修订结果”才调用。网络失败可恢复、语义失败不重开额度；旧policy升级保留，前端明确null与字段缺失分开，避免旧backend兼容误拦升级。没有增加通用修订编辑表单，运维登记接口仍需具名审定依据。

后端233相关回归、前端全量451通过；最后null修正后专项103通过，tsc/eslint/build及独立审核通过。真实副本8段/431证据/3更正，922原文件保持，模型0。审计 `/private/tmp/novelos-cover-recovery-ui-20261014/`，报告 `docs/reports/2026-10-14-cover-recovery-entry.md`。

13:46上海前后端部署healthy，双次15作品空闲检查，target项目/SQLite/config备份。backend image `sha256:d08ac842935983220719c18fc9959fb2cba282112f894349778b5ebb105b771b`，frontend `sha256:cbfdd132c3f485b02003546d0f3359c390066eafaf62f8b94423942a16423463`，3源码/65前端资源SHA核验一致，23096既有业务文件保持。rollback `novel-os-{backend,frontend}:pre-cover-recovery-ui-20261014`。

**已仅本地登记正式child `analysis-10d91a69e14270ba76ed564870bab73d`，仍未启动真实核查。** 保持用户“暂不启动真实核查”的决定。父 `analysis-b983bffa76874269a8fbd5255c3172f2` 保持failed，最新完整SHA `e1b19f8bdee674010d1cf7d8865c7678b6c1732e8f21a07c6c0938b7b32cee28` 经独立具名复核重新绑定；原922文件SHA保持、child partial/verify、0attempts。API显示parent amendment_available/child amendment_pending，默认最新child。浏览器实测刷新、选择旧父、查看修订及再刷新均无写请求，旧盲重试不可见、待核查文案正确。截图 `.playwright-mcp/cover-recovery-ready-20261014.png`。

后续不要再执行上轮 `resume_production.py` 或按上轮旧父SHA登记重复修订；已有正式child。只有用户决定启动真实核查时才通过正常API恢复该child，仍需完整原文/全局核查通过才能用于美术方向。未生成图片、未修改正文、未Git提交/推送。下节“尚未登记child”是旧交付时状态，已被本节取代。

## 最新修复：2026-10-14 封面来源限定与审定局部恢复

同书分析 `analysis-b983bffa76874269a8fbd5255c3172f2` 已在09:05上海失败，结束下节所述08:56恢复。首次语义审核只要求修正虚构town；修正后新增发现资金/住房的来源假设身份在全局经济信号和EV15遗漏。冻结骨架978行明确资金与居住可行性仍为设计假设；分段提取/global_facts保留。核查有依据，但不等于实际资金不足或必须先做现实财务核验才能做封面。

新增 `core/cover_analysis_corrections.py::prepare_source_amendment`，具名agent/human审核、父完整SHA/来源/配置/提取绑定，已有叙述字段和追加证据的局部修订生成独立child。父历史及耗尽额度不改；复用8段/431证据，仅重新verify，不重新global或segment。无效/超限修订不得回落生成，配置升级或来源换绑写前拒绝；失败停止、不额外自动修正。同内容缓存继承旧修正历史。每parent仅一个修订、同参数幂等；暂无前端修订编辑表单。框架并非保证所有模型将来不再遗漏限定。

226项相关回归通过（含新增专项26项），独立审查通过。真实数据副本只更正经济信号、EV15后果及其证据3项，原922文件保持，模型0；正式922文件保持。报告 `docs/reports/2026-10-14-cover-source-qualification.md`，审计 `/private/tmp/novelos-cover-qualification-20261014/`，审定参数 `proposed-amendment.json`。

09:41上海完成后端3模块部署，image `sha256:f9851cec71fe74ef0f46f91306b75b2c565ed9a63c318d8d1ea0fb255b2f4fc6`，双次15项目空闲检查、target/SQLite/config备份；23096既有文件保持，前端容器未换，两服务healthy。rollback `novel-os-backend:pre-cover-qualification-20261014`。

**用户已明确选择“保留已部署修复，暂不启动真实核查”。** 不再等待本次授权，不启动模型核查、后台重试或自动恢复。此前自动审批拒绝 `resume_production.py`：仅发现另一部小说的明确授权，未覆盖本书。只读确认provider=codex、base_url空、effort=high。未登记正式child、未发请求，旧分析保持失败；922原文件SHA一致。恢复材料保留，只有用户后续明确授权本书数据和核查后才能重新检查运行状态并使用审计目录 `resume_production.py`（本地登记+正常API一次dispatch），随后 `check_production.py` 核查真实完成结果。预期child `analysis-262b8c90450592260a4a3607fb23d5bd`；勿复制临时副本，勿重置父修正额度。未Git提交/推送。

## 最新修复：2026-10-14 封面分析空地点与原任务恢复

用户报告同书 CoverAnalysisError: Event requires location。目标 she-still-loved-them-but-the-choice-was-hers-4cc8bbb8 的分析 analysis-b983bffa76874269a8fbd5255c3172f2，原8/8段与431条证据已完成，全局格式修复回复15events中EV04/EV10 location为空。共享提示要求可选未知留空，校验却非空，属框架冲突。原文含『家中独自核账』『木屋餐桌与必要的付款联络』，勿声称原文完全无地点；两个复合/远程事件未断言共同地点。不要把participants当同场，不补默认地点。

core/cover_analysis.py允许显式string空地点，missing/null/非string仍拒绝，其余必需字段/证据约束保持；未改global/verify请求或配置身份。严格旧请求+修复链重验证复用原raw，不改原attempt。core/cover_campaign.py明确literal引用空地点错误并指导选择有地点事件或editorial portrait/object；空值不证明same_moment。363相关测试通过，独立review通过。真实副本模型0，复用全量结果后在verify真实模型边界停止，正式888文件保持。

08:55上海部署，仅2模块；backend sha256:aa93e10d40a7c5ca4983a51a73192917887fdc8bef2de7f88f1b702dfb2b9768。两次确认15作品无任务后备份，23062既有文件SHA保持、frontend未换、健康通过。08:56正常API恢复同analysis，job a140eb983a6545bf895f91c0da76568c，配置gpt-6-astra/high未改。已确认phase=verify、last_error=null、8segments/431evidence/15events，新增3个verify请求，global/segment调用0；335既有正文/交付/源快照/历史回复文件SHA保持，book run c97e8ae5aef6仍completed。

**上述恢复随后于09:05上海失败，详见最新“封面来源限定”记录。** 审计 /private/tmp/novelos-cover-location-20261014/，报告 docs/reports/2026-10-14-cover-event-location.md；未生成图、未修改正文/正式选择、未Git提交/推送。顺便修正旧结局部署记忆的时区笔误：实际05:55上海，非06:55。

## 最新修复：2026-10-14 结局证据协议与正式恢复

用户“修复吧”授权。已统一Scribe/Guardian/TASK结局字段，修复重复Arc前缀/错误闭合标签；显式错位标记可归位，多块合并不覆盖，非法机器声明报ending_protocol，普通事实/备注不冒充完成。提前提供完整义务身份目录；终局按相关已批准Final+canon/proposal提供跨章索引。纠正旧诊断：context_pack已有ID输出，L01是未入选当章角色包。

新增EndingEvidenceStore：具名agent/human审核、项目/合同hash、Final版本、receipt/ledger、原文精确offset绑定，追加式contentaddressed记录和显式supersedes。只补gate投影，不写canon/旧proposal；过期/损坏明确阻断。检查点追踪review成员/来源，移除自生成ledger输入。早期intentional_open不阻止后来完成补证，同/更晚矛盾仍阻断。无自动全文扫描/模型补判/额外重试。

451相关测试通过。真实全书副本原6条→pass，真实PipelineRunner.retry复用15章、ending/book.check完成，在publication.copy前停下；262不可变文件/正式836文件保持，模型0。审计 `/private/tmp/novelos-ending-fix-20261014/`；[修复报告](docs/reports/2026-10-14-ending-evidence-recovery.md)。

05:55上海时间后端修复部署healthy，7源码SHA一致、23012既有文件保持；双重确认15作品无任务后备份target项目/SQLite与rollback镜像。backend `sha256:ba0226c6a309ced57c79f546e4cb11b852908de92de774dca24d64e2ae4684b9`，frontend容器与镜像保持`d3981757…`。正式目标追加审核`ending-review-84b4777452dfa39360c9d475faafe21e5e4c471cac3fb2a6a32c2daac3a2d9b3`，5claims。正常API恢复同run `c97e8ae5aef6`，job `f6cf163ae1114558810502c43058bde5`，现已completed。15章全复用，正式ending.review/book.check/publication.copy/compile/delivery.package全done，报告pass/critical0，262正文及历史证据文件SHA保持。

真实出版文案和审核已执行通过，MD/HTML/DOCX/EPUB/PDF及ZIP已生成。ZIP 186898字节、28成员/27清单文件逐项size/SHA与磁盘一致、CRC通过，SHA `9db8ce5424b687137023be199d39b79a1f8339a578c964fd0dfddc2083a09155`。5174任务API completed/无失败阶段/不可再恢复；下载application/zip+正确filename且bytes一致。核验`production-delivery-result.json`。没有测试外部H5导入，不代表解决此前H5分类字典兼容问题。未Git提交/推送。

## 最新诊断：2026-10-14 c97e8ae5aef6 结局证据漏记

用户要求排查结局检查6条报错。项目 `she-still-loved-them-but-the-choice-was-hers-4cc8bbb8` 已批准15章，05:03上海时间于ending.review本地约0.16s阻断，paused。L01律师职责（3/7/9章）及payoff004/007/008/009（8/12/13/14章）在已批准正文完整出现，相关正文SHA与committed receipt吻合。失败是结构证据漏记，不是这5项剧情未写。第8章paid仅自由文字，12/13/14 paid在New_Facts_Established，Guardian原解析/提案就没有payoff_events；末章recalled不等于paid。L01=Marian Reed已在canon，但Scribe把完成状态放Character_References.note，Guardian因缺身份映射和早期场景未确认，实际arc/outcome空。

框架原因：Guardian ROLE强制旧字段/禁止增字段与TASK新增ending字段冲突；TASK Arc条目重复字段前缀、结束标签错误；解析缺字段静默保存并获批；终局前不提供角色outcome合同，终局又缺退场配角的早期证据。Codex实际拼ROLE/TASK同一消息，勿声称协议层system优先级已证。7个运行源码/模板SHA匹配。只在tmp副本补已读正式正文的证据，原gate 6→2→pass；正式836文件SHA不变、模型0、无重启/部署/正式状态修改。非已完成生产修复，直接重试不补证据；不可裸改state（重试可恢复快照）、修改旧proposal或全体recalled升级paid。需统一协议+漏记校验+跨章证据+审计协调恢复。报告 `docs/reports/2026-10-14-ending-gate-c97e8ae5aef6.md`，证据 `/private/tmp/novelos-ending-c97e8ae5aef6-20261014/`。

## 最新修复与部署：2026-10-13 20:12 第三阶段分块交付

用户“开始修复”授权修复第三阶段重复900s超时。已保留原生Codex/model/effort，将assembly改6个独立补全部分（规格/关系世界/秘密伏笔/阅读文风/封面交接/复核），程序组装17节，既有故事和全部章节无需再次模型转写。每部分全上下文、局部合同校验后保存；最终仍旧完整验证。局部final错误定点修订、append journal保留旧版本，global2格式修复不扩张；timeout无自动调用。

v1 checkpoint/Skill fingerprint原样兼容，optional parts哈希链接旧两阶段与最终artifact，identity/revision/job/cancel/native退出锁保持。新部分失败显式resume仅缺失部分；final修订失败可再次本地定位并继续。遗留额外facts完整JSON保留，嵌套章节标题只降Markdown层级、POV等正文metadata加引用前缀、多行章标题保留原值，原记录不改。普通正文原样保留。

普通讨论/已有骨架编辑原整轮timeout不变；分阶段交付单次请求沿配置900s，整轮最多3倍即2700s，从原轮起点包含等待/IO，活动不续期。UI显示单次/总期限和当前部分/已保存x/6，失败暂停继续入口明确，系统视觉保持。

最终后端workshop+metric327、前端439、独立review58、桌面手机14项通过。真实4cc8bbb89ae74a509b9d4341f50df7a9旧run676b6f62ee7e490ca86452ecf2c12637隔离验收仅6mockparts、15章与5story字段原文保留，17节真实验证通过，原32文件SHA不变；无真实LLM。模拟输出量最大4031B/合计8105B vs同文整文response62036B，非实际速度/成本/语义质量承诺。

20:12同步5174前后端healthy，部署前两次空闲检查、数据tar+SQLite备份及rollback镜像。194源码与65前端资源SHA一致；14作品/4讨论保持，14封面媒体可读，22303业务文件SHA与16表记录数保持。目标线上仍error/rev12、前两阶段saved/resumable，无自动续跑。用户现在可刷新点“从已保存阶段继续”。CLI0.153.0，模型配置未改，Git未提交推送。backend0bca886c…、frontend8f1aa8c9…，rollback `pre-workshop-parts-20261013`；证据 `/private/tmp/novelos-workshop-parts-deploy-20261013/`。详见[修复交付](docs/reports/2026-10-13-workshop-partial-assembly.md)，原始诊断保留在下节。

## 最新诊断：2026-10-13 第三阶段仍超时

用户要求查最新头脑风暴LLM日志。本轮仅只读排查，无重启/模型重放/配置或业务代码修改。最新4cc8bbb89ae74a509b9d4341f50df7a9 rev12为error；检查时无Codex子进程。rev11(10/12 17:49–18:04)交接33.21s、story414.12s、chapters398.36s，assembly仅剩54.06s。rev12(10/13 09:59:14–10:14:14)正确复用前两步，只执行assembly且有899.92s；10:03:00–10:14:10共135个非重叠DELTA批次、20760正文事件，每批约5s直到截止前，之后本地900s强制timeout。此为真实正文活动证据，不能把事件数当token；旧部署前900s静默仍不能倒推。

CLI只返回101B启动事件，完整消息0/final文件不存在，未进入本轮validation、更无格式重试。第三步仍为一次性17节骨架+全合同/逐章转写，checkpoint只保存完整阶段，导致活动中的未完成整合被截断。实际请求构建离线复现127866B系统+58201B用户，完整作者上下文和两阶段成果相同，15章，fingerprint/Skill/hashchain正确；运行关键源码SHA一致。前两阶段安全保存，并非恢复失效。8192在CLI中不生效，model/effort保持gpt-6-astra/xhigh。

建议后续改第三步为有界补充生成+确定性本地组装及更细完整检查点，区分请求/静默/总任务期限；不能只重试或靠增加超时保证成功。尚未实施。真实方法最小离线deadline复现0.55s预算在0.553s终止，指标投影5项通过、独立审查一致。证据 `/private/tmp/novelos-workshop-timeout-20261013/`；[诊断与优化方向](docs/reports/2026-10-13-workshop-assembly-timeout.md)。

## 最新部署：2026-10-12 15:57 原生观测与阶段保存上线

用户明确“当前没有任务请重新部署”，解除此前暂不重启限制。本轮确认14作品无活动任务、4条讨论均非running、容器无Codex子进程后，保留原生Codex CLI 0.153.0与模型配置，重建并切换5174前后端。两服务healthy；193运行源码/Skill文件和65前端资源与已验证工作区构建逐项SHA一致。

部署后14作品与4条讨论列表逐字段保持、历史详情及所有作品封面可读、14个封面媒体可访问；22293既有业务/配置文件SHA保持，SQLite16表记录数量保持。无模型调用/任务写请求/历史任务重放。新阶段保存仅对新流程产生的成果有效，历史900秒失败没有补造检查点；实际长任务速度仍待正常使用验证。

backend `sha256:11a823384541f7406f89ec6b76180387eff265b3c622151281492642393279ac`，frontend `sha256:d926809d4c730d73d75ab1582aa3ce46b14b8f6b237eda08774081689e12d691`。回滚镜像 `novel-os-{backend,frontend}:pre-workshop-staged-20261012`；数据归档及SQLite在线备份、部署/验收证据在 `/private/tmp/novelos-workshop-deploy-20261012/`。宿主Skill未覆盖，未提交/推送；[交付报告](docs/reports/2026-10-12-workshop-staged-delivery.md)。

## 最新实现：2026-10-12 原生活动观测与骨架阶段保存

用户“同意”保留原生CLI并改善长时间无输出。已实现原生OTel metrics观测与新骨架story_design→chapter_plan→assembly三阶段检查点；当前**只开发验证，未部署/重启/重跑真实任务/提交推送**，遵守先不重启。原模型/推理档位/auth/read-only保持，非App Server迁移。

摘要/正文delta与transport分开，DELTA/CUMULATIVE+epoch+重放/重叠检查，批次接收时间诚实展示；仅实际诊断callback且collector成功启用新metrics和子进程5秒interval，usage-only保留原logs与env。时间不重置截止，旧900秒仍无日志可补证。新同镜像禁网probe5.900秒指标可见、8.890秒CLI整项输出；无凭证/业务挂载/真实模型，fallbackmetadata限制保持。

新骨架内部handoff自动三阶段，每步全作者上下文，早期延后最终schema；17节+章节数/mode/原合同最终校验。已有完整骨架小改/前次失败候选合同修复仍直接整合，不重新做全书设计。每轮总900秒（沿配置）与全局2次格式修复上限保持，IO/等旧进程退出计入期限，超时无自动重试。

delivery/<runid>.json追加式存储、完整输入指纹/Skill/source revision/会话身份、阶段hashchain、4MiB失败不截断、原子fsync。保存受当前revision/job/cancel锁内fencing；真实_complete会话级执行锁等待旧CLI清理，其他会话不阻塞。error有成果可显式resume，GET不重放；全部保存但session发布中断可本地重验发布，UI“完成骨架保存”。新作者turn/restart换run，inputedit清当前关联，损坏/Skill变更拒绝复用，旧数据保留。

合并后端367项通过（受限socket/ps12项放开本机离线能力后全绿）；全前端431项+tsc/lint/build、独立agent审查通过。共享LLM107、图片/provider95与合并套件有重叠不累计。检查点存储/生命周期、旧调用退出再恢复/暂停等待、预算与失败不重放已有新测试。实际故事质量/速度尚待正常用户任务验收，不保证总时长或成本下降。[交付报告](docs/reports/2026-10-12-workshop-staged-delivery.md)，[设计](docs/design/2026-10-12-native-codex-long-running-workshop.md)。

隔离浏览器1512/390px共14场景、6次内存模拟resume通过；零真实写入/模型/浏览器错误，刷新无重放。已修小屏新增进度挤压输入区：手机整窗滚动、桌面分区滚动，阶段卡短标签保持一行，系统视觉不变。改后56聚焦+最终9组件/build/lint通过；最后后端提示一致性修正50项再次全绿。截图与audit在`/private/tmp/novelos-workshop-ui-20261012/`，临时Chrome及5189 preview已关闭。生产服务仍未部署/重启。

## 最新续查：2026-10-12 原生 CLI 长推理进展

用户明确保留原生Codex，Token次要，要证明900秒是否工作并设计稳定输出。本轮无重启/重试/真实模型/业务代码修改。原容器logs_2.sqlite总0行、state线程0行，无rollout或临时结果；官方exec log_db=None，旧帧无法追回。

新同镜像断网mock实测：上游0.652秒发公开摘要delta，1.155秒正文delta；metrics1.283秒已报告两类各1，exec --json直到5.170秒item完成才输出完整reasoning/message。退出0最终文本正确。官方exec内部已用AppServer核心，JSONL过滤增量是明确盲区；补metrics能保留exec观察实际活动，不保证原900秒有此活动或已定位远端根因。指标为异步增量聚合、缺responseID，不得二次差分或冒充精确逐帧时刻。

建议先补原生metrics及真实活动分类，再按粗阶段保存成果；需要逐段正文时使用同CLI的app-server接口。阶段保存/恢复是业务检查点，thread/resume不能复活已终止远端计算。模型/档位保持，超时不能只延长。方案尚未实施：[长任务设计](docs/design/2026-10-12-native-codex-long-running-workshop.md)。证据`/private/tmp/novelos-workshop-protocol-20261012/delta_results.json`及同目录delta_probe.py；[完整诊断](docs/reports/2026-10-12-workshop-delivery-timeout.md)。

## 最新续查：2026-10-12 Codex 协议与 Cockpit 用量

延续只读诊断，无重启/重试/真实模型调用/生产配置改动。用户查看Cockpit Tools（本机1.3.63）。安全内存比较确认容器/宿主/Cockpit当前选中账号一致；不能归因看错账号。Cockpit本机session统计扫描rollout，Novel OS用--ephemeral且不挂共享sessions，因此不能统计此容器调用；网关disabled，当前请求为ChatGPT Codex直连；账号quota配置每10分钟刷新。用户未明确具体面板，需分口径解释，不能把数字不变当未发送证明。

确认遥测缺口：官方Codex0.153普通websocket_event只写metrics，框架只订阅OTel logs，故中间帧不可见；完成另有日志。900秒无正文不等于持续推理，也不等于网络完全静默。

同镜像network-none/no credentials隔离mock：140357B synthetic prompt在正式response.create内全量SHA匹配，generate=false预热后generate省略+previous_response_id衔接，返回模拟结果成功。2秒idle下静默/Ping分别2.32/2.23秒超时，metadata帧则持续到7秒本地截止无结果，7断言通过。此为可能机制非原故障根因复现；mock缺目标模型metadata，不能称线上等价或服务端已接纳。官方CLI序列化stream字段（源码值true）与公开WS指南不使用该字段有差异，但无证据归为框架错误。实任务正式上游接纳/计算仍未知。

详见[诊断报告](docs/reports/2026-10-12-workshop-delivery-timeout.md)，实验`/private/tmp/novelos-workshop-protocol-20261012/`。后续应区分预热/正式发送/服务端接纳/有效内容/终态；不要仅延长超时或重放任务。本轮只更新诊断文档与记忆，业务代码未改。

## 最新诊断：2026-10-12 14:25 头脑风暴交付调用静默超时

用户要求不重启/不干预；本轮只读检查运行任务，无重启/暂停/重试/配置变化/额外模型调用。会话4cc8bbb89ae74a509b9d4341f50df7a9 rev10 job45a255c7d54c4242a998f5224ad0c752。14:10:22起900秒；首调用约32秒返回422字符内部交接并有32043输入/622输出Token，14:10:59自动切delivery第二次（不是格式重试）。第二次126400B系统+13640B用户，gpt-6-astra/xhigh，14:25:22.736按原期限自动error，第二次usage缺失、0可见内容、无final文件。

14:24:44运行中只读核对真实Codex pid3477 fd0：140357B输入完整UTF8/JSON，含3条对话和末条“同意推荐，请按照推荐继续推理”，system/user长度与日志吻合，原fd位置等于EOF。已排除框架漏拼/stdin部分传输；不能据此声称远端LLM收到完整字节。先11709输入/0输出completed后WebSocket发送success，与CLI预热吻合但未记录generate字段。发送成功不证明服务端在计算；其后只有心跳与models查询。Token账本按CLI完成回执结算，缺失不等于0。

进度遗漏delivery交接及上游endpoint类别，是可观测性待优化；当前证据无法区分上游排队/推理/响应链路/CLI内部停滞。未改业务代码或部署。[完整诊断](docs/reports/2026-10-12-workshop-delivery-timeout.md)。

## 最新核对：2026-10-12 H5 ZIP 分类字典兼容待处理

用户提供H5诊断交接`/private/tmp/novelnest-zip400-diagnosis-20261012/NOVEL-OS-ADAPTATION.md`及2026-09接收端字典，要求说明当前完整分类。本轮只读核对原`/Users/max/Downloads/book-package (3).zip`，SHA为`8368c1d225b90b3654497725d30178af3a32da97be67a30135c16df3e84f9a92`，与交接一致。包用2026-10；story_types为domestic_betrayal/marriage_crisis/female_growth，3项未超数量上限，但female_growth不在提供的H5旧字典。交接的实际解析器先因版本不支持失败；本轮没有重新运行接收端或查线上状态。

框架catalog_payload与仓库完整JSON逐字段一致：当前48项（主类型12/故事24/基调6/场景6），H5提供字典32项（10/10/6/6），新增16项无删除，共有32项未变。新增主类型xuanhuan/horror，新增故事ethical_dilemma/female_growth/male_growth/celebrity/abuse_survival/queen_empress/love_at_first_sight/office_romance/workplace_comedy/same_sex_romance/single_parent/billionaire/pregnancy/apocalypse。受众/篇幅枚举和组合规则未变。框架向后兼容旧字典并不表示H5向前兼容新ID。

未实施导出降版或H5升级，未改原ZIP/正式项目数据/线上。后续若保留新分类须H5同步支持完整2026-10并兼容09；若适配旧接收端须正式目标字典投影并同步EPUB/sidecar/publication package及所有关联SHA，不能只换版本字符串。完整字典在`docs/examples/h5-import/novel-classification-catalog.json`；旧`docs/h5-import-handoff.md`仍标09，需随后续接入方案同步澄清。

## 最新续接：2026-10-12 12:42 完整封面直接选择

用户明确取消人工接受理由门槛：完整图片可直接选择，不要求当前或所有候选审核通过。已移除CoverStudio理由表单/state与必填判断、服务端质量理由拒绝；ready/rejected均可选，已拒绝完整图可重新选用。项目/头脑风暴共用规则，导入同步支持rejected。保持pending/failed限制、来源/批准/配方/继承与媒体完整性核验，保留原“使用此封面”确认。

选择与质量判断分离，blocked报告保持原样，不制造human_acceptance。历史理由保留，旧API/CLI仍兼容可选note（最长2000），新UI省略字段。CONTEXT、仓库Skill和商业封面设计决策已同步，宿主Skill未覆盖。

原16组合红灯14失败/2通过，修复后全绿；后端167+93=260、全前端417通过，TypeScript/lint/build/diff-check与独立审查通过。已同步5174前后端healthy；14作品空闲后部署，22282既有数据/配置SHA不变，176运行源码/Skill一致。backend `sha256:48ba4170f284eb2efc7a04e2f8ff2bf22be5681e872490b5789a7c3bc15e1995`，frontend `sha256:89ee8ad4762bd8767232b42d59585dfcf3f983d033527e532a10e97c52283e80`；回滚pre-direct-cover-selection-20261012。

独立Chrome桌面1512/手机390四项通过：blocked选择、无理由提交/原报告保持、刷新不重放、rejected重新选择，无溢出或浏览器错误。两次POST仅内存mock，正式选择/模型调用0，context已关闭；未提交或推送。[交付报告](docs/reports/2026-10-12-direct-cover-selection.md)，审计`/private/tmp/novelos-direct-cover-selection-20261012/`。

## 最新续接：2026-10-12 12:22 封面商业表达与审核提速

用户明确：封面传达大致故事、核心关系与情绪，不需所有细节和剧情逐项一致。新Live/CLI经get_cover_art_director使用cover-profiles.v11，campaign.expression_mode=literal_scene/editorial；宣传式人物组合、物象、环境与余波允许，无主演/无具体event也必须非空真实来源证明。旧v10字段不补写/v17配方不变，新显式mode用v18。

cover-commercial-review.v1把material_story_contradiction/author_constraint/source_integrity阻断与marketing_appeal/visual_ambiguity/composition_craft/thumbnail_readability建议分开。仅硬错误修复；本地warnings也不会买下一轮。图后cover-readability.v2按冻结scene选协议（不能模型自报切换），低动作/因果分数不自动变事实阻断；A盲读冻结、B可用标题补充总体承诺，真实标题/技术错误仍挡。方向和新单图proposal显示折叠可选建议，proposal摘要包含建议，刷新无调用。

单图只review目标并严格复用各兄弟实际已批准来源，核对source/analysis/shared/plan/receipt；无可信回执做完整事实fallback，不重挑未改美术。范围外事实错误停止单图而非无效反复。实际失败ff934284c8e940a48da9d421e1d3132d总571.657秒，六模型区间570.659秒；首轮只超prompt101字符，缩短后全组重审误否决不能修改的候选1。通病为尺度与范围错配。

真实六响应离线精确复现；新协议模拟2调用（非实模型保证），审核296413→247450字节（约-16.5%），完整67306字节原文/200证据保持；四editorial全事实337932<384000。用户现有模型/推理档位保持。未真实调用模型、出图、重新审核或重放失败任务。旧失败保留；需重新规划采用新策略，原有效proposal重试仍旧方案。

后端封面+Skill1310、前端412通过，tsc/lint/build/diff-check通过；独立审查67项并闭合warning循环和协议自报问题。已同步5174前后端healthy；14作品空闲后更新，22253既有文件SHA保持，176运行源码/Skill一致。backend `sha256:7b773fbb78c359955cec04812bf7e5731321034cef9482c99e5ee245e292bbc8`，frontend `sha256:64588c005e5a2187bfa758f8c1944619456469712b2754c6d9606acaf00eff93`；rollback pre-commercial-cover-20261012。未提交/推送、宿主Skill未覆盖。

独立Chrome桌面1512/手机390五项检查通过，建议默认折叠/刷新保留/单图批准/审批刷新重置/无溢出；错误与写请求0，内存mock且context关闭。运行入口只读核对v11+gpt-6-astra。

[设计](docs/design/2026-10-12-commercial-cover-expression.md)，[交付与限制](docs/reports/2026-10-12-commercial-cover-policy.md)，审计`/private/tmp/novelos-commercial-cover-20261012/`。

## 最新续接：2026-10-12 11:26 失败精修重试恢复

用户候选2因上节参考图读取失败后无图，原卡片只对ready图片显示精修入口，旧额度也已消耗，形成恢复死路。现新增失败/中断卡片及任务面板“重试精修”：恢复原parent图和保存要求，校验完整来源/方案/配方后无模型复制为新awaiting_approval任务；用户另行批准1次编辑才执行。旧任务、batch_reserved、费用、父图和正式选择保持；稳定后继身份保证幂等及写盘中断恢复，连续失败针对后继重试。

“修改要求并重新规划”预填原要求与原图，不自动调用。方案过期409给replan_context；已有精修成品或原画409给result_context，导向查看/重评/排版而非再次出图。GET恢复不写盘；不同slot后续批次保留的旧失败候选沿验证repair_lineage查找生产task，lookup双字段绑定当前卡片，不按序号猜。重试后定位待确认面板，尊重用户请求期间切页；刷新重置审批勾选且不重放POST。

后端163、前端397、Skill8项通过，tsc/lint/build/diff-check通过；独立review关闭跨批次恢复缺陷后通过。实际失败任务在隔离副本已验证无模型恢复与旧数据字节保持。已更新5174：backend `sha256:f36aecc2f6daf3a17918478473e0b8ae353f8c047a514c5b820fa7a0e80da02a`，frontend `sha256:18d2f17a4a1a1d5707b7dc8ce3cc3a11a93166d389f56e13e3e56df72ac71ad3`；11:25上海时间启动，均healthy。14作品空闲后部署，22208既有文件SHA不变、175后端源码/Skill匹配。回滚镜像 `novel-os-{backend,frontend}:pre-refinement-retry-20261012`。未提交/推送。

独立Chrome桌面1512px/手机390px验收通过：原缺陷红转绿、原图/要求恢复、待批准/运行刷新、审批重置、连续失败入口可用、无横向溢出或浏览器错误；2个POST仅浏览器内存mock，正式写入和模型调用0，context已关闭。本轮不真实调用模型、不重试正式任务。原任务仍failed，用户刷新页面后从失败卡片直接恢复，不再要求手动找旧版本。审计 `/private/tmp/novelos-refinement-retry-20261012/`；[交付报告](docs/reports/2026-10-12-cover-refinement-retry.md)。

## 最新续接：2026-10-12 11:09 原图精修文件沙箱兼容

用户候选2精修任务 `repair-task-717fea70cd2f431cb9fbccb035c5ea4d` 在 Codex 0.153 Docker 文件沙箱 bwrap 命名空间创建时报错；原图SHA正确，通用 Codex 路径参考兼容问题。8分16秒包含7分11秒规划，图片阶段约65秒。已改 `core/codex_image_edit.py` app-server临时会话直接传冻结原图dataURL/detail original，再以 num_last_images_to_include=1 编辑，避免CLI --image缩放／暴露路径和图片工具二次读文件。此前附件＋referenced_image_paths的实现已被替代。workspace-write不变；明确关闭 omit_app_server_notification_media 以保留图片回执。

成功要求唯一completed imageGeneration回执并直接保存其base64原字节；缺失／失败／额外调用／返回原图／超时不得伪成功，不按目录猜图，不降级，不自动重试。诊断立即识别同类bwrap失败；最终宿主文本token累计结算与进程清理保持。原失败任务／父图／其他候选／正式选择不变。需用户从保留原候选新发起“精修这张”，不后台重放或退款。

专项75、集成103（含31重合）、生图服务81、Skill8项通过；独立review通过。真实容器mock首Responses证明2048×3072图像SHA一致且无路径；官方0.153源码证明num_last→会话URL→edit及imageGeneration→item/completed映射。dummy provider未开放imagegen，未实际调用真实图片模型，不得称实图效果验收。

已更新5174后端：`sha256:cb422402dc44ce36cf0830888a8b12c12ca968fbc2f46fba7f2902ae26b4d3b0`，2026-10-12T03:08:46Z启动，前后端healthy；前端CID/镜像/启动时间保持。14作品无运行任务后更新；22208既有数据/配置文件SHA不变，175运行源码/Skill SHA一致。修复前rollback `novel-os-backend:pre-reference-attachment-20261012`；最终补丁前额外镜像 `pre-reference-attachment-final-20261012`。未提交／推送。

审计 `/private/tmp/novelos-reference-attachment-20261012/`（最终部署 `final-deploy/`）；[交付报告](docs/reports/2026-10-12-cover-reference-transport.md)。

## 最新续接：2026-10-12 00:01 单图修复预算与原图精修

已解决上节候选2/3单图修复请求超限：验证base.evidence_ledger与权威账本一致后去除重复副本，再计算patch/hash；保存时恢复完整ledger。真实候选2 441576→345132字节、候选3 440414→343970；67306字节原文、200证据、四方向及整组独立审核不变。通用修复，无截断／扩预算／自动重试。

新增“精修这张”：用户填写1–2000字要求，先修订审核所选方向，再明确批准1次参考图编辑。API edits传multipart原图，Codex附件＋referenced_image_paths；请求与参考SHA绑定、前后校验、无编辑降级。composed编辑无标题原画后按批准布局合成，再对新图A/B审核；native编辑原成品。另存新批并保留父图、兄弟与正式选择，GET恢复不重放。liked/selected/rejected来源绑定候选均支持；旧import/amendment仍沿原核验流程。

后端3144项覆盖经全量与受限测试补验通过（20项本地端口/ps沙箱限制；既有0.6秒模拟遥测启动窗口改3秒后10项遥测全绿）；前端376项、tsc/lint/build、独立review、Chrome桌面/390px 18项通过。浏览器2个POST仅内存模拟，真实模型调用0；没有实际精修用户图片。已部署5174并核对174源码/Skill SHA、22187既有作品/配置文件不变；更新前14作品/讨论无运行任务。前后端healthy。新backend d53b0dbdb278cc77e4a7d76928acd7a671e9b01ee81e486fdc32d86fe43a8ad5，frontend 01ce1cfb88928e56dc85cae3715d677eca540d528eaf988fc47db97e16fd512c；两者rollback pre-cover-refinement-20261011。

旧失败任务保留，用户需新建修复或精修方案；不要后台重放或替换正式图。审计 `/private/tmp/novelos-cover-refinement-20261011/`；[报告](docs/reports/2026-10-12-cover-refinement-and-budget.md)。仓库Skill/CONTEXT已同步，宿主Skill未覆盖。未提交/推送。

## 最新续接：2026-10-11 23:41 全宽与封面子页交互

用户六项UI要求已完成并部署5174：作品库／头脑风暴全宽，封面移除1680px上限；作品整卡用原生链接进入详情，封面／管理／任务信息／恢复独立点击；头脑风暴状态与继续入口同行。封面采用候选封面／美术方向／故事分析／费用四个子页，`?tab=`保存选择，隐藏面板保持挂载及轮询，后台状态持续可见；已完成方向仅在对应direction_id批准后隐藏提示，已批准方案保留查阅。切换有键盘焦点、长页回到目标内容，迟到批准响应不覆盖用户新子页。继续使用系统浅蓝紫玻璃和系统字体。

前端360项、tsc/build/lint/diff-check通过；独立Chrome桌面／宽屏／390px共83项通过，真实写入和模型调用0，模拟批准仅在内存。前端镜像`sha256:7bd14418c28f8cfab8b698f631123de80d89753fea226508767273d0100b20f5`，回滚`novel-os-frontend:pre-cover-navigation-20261011`。后端ID／镜像／启动时间完全不变；真实Secret Daughter封面和方向记录前后相同。审计`/private/tmp/novelos-cover-navigation-20261011/`；[交付报告](docs/reports/2026-10-11-workspace-navigation.md)。未提交／推送。

独立待办：浏览器验收读取到用户已有单图修复`repair-task-abeb8ac15328458395184c9affb5b3a9`因`semantic_repair`请求440414字节>384000预算失败，本次UI工作未改后端、未重试。方向任务`direction-task-6b668a9edf334e709a6c6952984e47c9`是已完成且已批准，应隐藏完成提示；不要误把修复失败也隐藏。

此独立问题只读精确复现：单图修复将direction.evidence_ledger整份嵌入previous_response，重复96444字节，无损transport未处理该嵌套。内存只去重复副本后候选3降至343970字节，候选2由441576降至345132；原文、200完整证据、四计划及顶层visual ledger保留。属于大证据集单图修复通用构建缺陷，首次模型调用前本地阻断。后续应针对可重新绑定证据副本做去重并验证patch/hash合同，不截断事实、不扩预算、不直接重试旧请求。

## 最新续接：2026-10-11 23:28 视觉统一要求与修正

用户明确否定上轮作品库／头脑风暴的纸色绿灰衬线风格，要求**保持和当前系统一样的视觉**。后续界面设计必须优先复用 `web/src/index.css` 的浅蓝紫、深蓝文字、系统无衬线字体、玻璃面板、圆角及现有按钮；frontend-design Skill 不得作为另起独立主题的理由。

已将作品库、头脑风暴与新增修复面板统一回该体系；保留所有上轮功能，改全宽并优化手机搜索布局。350前端测试、末次专项7项、tsc/lint/build通过；独立浏览器1512／2048／390px共26项通过，实际写请求0、错误0。仅更新5174前端，镜像 `sha256:e0764826c7827a777f1feb05a2cf16ab4259c06d7f076cd8f9d55d765fe599b2`；后端ID／镜像／启动时间完全不变，未打断后台任务或主动调用模型。回滚 `novel-os-frontend:pre-visual-consistency-20261011`。审计 `/private/tmp/novelos-visual-consistency-20261011/`；[视觉修正报告](docs/reports/2026-10-11-visual-consistency.md)。上节纸色出版方案已被此次用户偏好取代；设计文档同步修订。未提交／推送。


## 最新续接：2026-10-11 23:20 作品库、讨论历史与单图修复

用户本轮五项要求已实现并同步正式5174：作品库按frontend-design改为纸色出版目录和2:3书衣，正文／封面状态分开；封面精简为待设计、处理中、待确认、需处理、已选定，投影真实任务与保存结果，旧正式图保留。全局导航新增 `/workshop` 历史中心，全部有效讨论可搜索、按状态筛选、分页、恢复及进入关联作品。打开历史不自动调用模型。

基础设定修复为“编辑→仅保存→明确确认从头推导”；重新推导确认持久化，刷新不自动触发。确认后旧过程归档可查但不再进入新上下文，旧候选回退也由context_start_revision隔离；费用／Token保留；重复确认同任务，运行／已启动作品不允许重启。

单张封面修复新增持久cover.repair任务和前端方案确认面板。故事／构图／绘制问题只修改该slot，完整来源及独立语义审核后批准1次图片调用，新批保存目标结果及其余原图／配方／审核来源；原父批与正式选择保留。排版重合成／重评为0图片调用，仍有审核费用。排版入口仅重合成已批准布局；新布局沿用原排版提案／精确预览流程；有旧amendment/import候选沿用原流程。单图是整张候选重绘，不是局部像素编辑。完成不代表质量通过，不自动选中、不自动重试。最新修复方向不允许整批生成；A/B提案取代后旧提案会失效并允许重新规划。

全后端3,065项、前端350项、tsc/lint/build通过；独立审查已修复dispatch job落盘失败仍可能付费、损坏记录漏隔离、旧提案死路及过期方向状态误标。独立浏览器桌面／390px 24项通过，编辑／修复3个POST均仅内存模拟，零真实模型调用。14本作品、4个讨论接口正常，171源码／Skill SHA一致，前后端healthy。部署前后25,110文件／配置SHA不变；原Secret Daughter批次本轮基线实际为revision11，不可用上节旧10覆盖，四图ready，A/D human_review_required，B/C blocked；未实际修复或改变选择。

新镜像backend `sha256:63fdd663c431383a1f6c0348c6033a174625eb82f25e384d7b1207e7f841196d`，frontend `sha256:39102d382c90b8dd91ebd50001799661489ba6816b4b43ec9e4bc5e6489495dd`。回滚标签前后端`pre-library-workshop-repair-20261011`。方案：[作品库与交互设计](docs/design/2026-10-11-library-workshop-and-cover-repair.md)；交付：[五项改进报告](docs/reports/2026-10-11-library-workshop-and-single-cover-repair.md)。审计`/private/tmp/novelos-library-workshop-repair-20261011/`；独立浏览器关闭、下载default；未提交／推送，保留所有已有工作区改动。


## 最新续接：2026-10-11 22:53 封面生成任务刷新恢复

用户报告封面异步生成在刷新后看不到任务。已先复现页面重新挂载后进度消失／按钮恢复可点，再实现所属作品／讨论持久生成任务，统一项目、讨论及项目旧版生成入口。原POST仍返回202 Job并增加task_id；新增latest／指定任务GET。CoverService在准备、出图、合成、审核、整组比较、保存阶段记录精确候选组与真实计数；任务写入失败阻止后续付费调用。去重先于来源绑定额度预检；刷新／切页／响应丢失只查询原任务，不重放、不自动重绘或退款；服务重启显示中断。

前端页头恢复阶段、候选序号、耗时、已处理／可查看／失败数量，区分生成完成与质量通过。断线和候选未同步保持禁用生成；精确绑定任务批次，跨作品迟到响应和初始旧列表不能覆盖新结果。此次只覆盖初始批次；单张重试、重新评估和排版应用仍为原流程。

后端 **3,003 项**、前端 **315 项**通过，lint／构建／独立审查及桌面／390px浏览器15项验收通过。已更新正式5174，前后端healthy、168个运行源码／Skill SHA一致、14本作品与关联讨论GET正常。部署等原用户任务自然结束后执行，25,106个既有文件／配置SHA保持，零真实模型调用。新后端`e803c9c69df2a2418fd7cbb86c15327bf40d4836d87ae2489a7d3b7aea9cae37`，新前端`63e1255250fed25da1ed6fe780545834f073ed8655c9ec41343a6edc8e0d7c08`；前后端回滚标签各为`pre-generation-recovery-20261011`。

Secret Daughter 原用户批次`cover-d94c0acaf7bf4a0481c3570aa14d8f9e`已结束，保持revision10、四图ready，审核A/D为human_review_required，B/C为blocked，不能声称四图达标。此批在更新前启动，未补造任务记录；新记录从更新后的批次生效。未改批准／选择／预算，未重绘。报告：[生成任务恢复](docs/reports/2026-10-11-cover-generation-task-recovery.md)，审计`/private/tmp/novelos-generation-recovery-20261011/`。QA独立浏览器已关闭、下载行为default，未提交／推送。

恢复日期：2026-10-10，Asia/Shanghai。依据用户截图中的 15 条本机会话、当前代码和运行记录整理。原始会话仍保留在 Codex；本文用于后续项目续接，不是聊天记录合并或代码回滚。

完整来源见 [历史会话索引](docs/memory/session-index-2026-10-10.md)。领域术语见 [CONTEXT.md](CONTEXT.md)，五代理职责及质量门见 [AGENTS.md](AGENTS.md)。

## 2026-10-10 至 2026-10-11 本轮接管进度

本轮已继续上述两个任务，审查并修复封面来源约束、完整骨架分析分段、历史兼容和交付批次隔离。最新证据以 [审查报告](docs/reports/2026-10-10-cover-chain-review.md) 与 [封面接管文档](docs/design/2026-10-10-cover-studio-takeover.md) 为准；下节是恢复时的历史基线，不代表此刻仍未同步。

用户指定《He Chose His Hidden Family for Twenty Years…》验收 3 个候选；发行定位为北美市场、40–60 岁女性。用户明确授权将该书完整骨架发送给 OpenAI 官方 Codex gpt-5.6-sol 分析/审核，衍生提示给 gpt-image-2 出 3 图，并由 OpenAI 审图。初始 3 图、额外重绘 0；营销信息不改变故事人物身份。未指定本轮最终选中，必须保留旧选择。

快照 `snapshot-6e249e717dcb4aab87795a00980bc70e`，来源内容 SHA `b9e97861759e9099cd5a6351c16008b6ed06275a96a6f1acd64fc36d3d3fcb63`。原分析 `analysis-97a784d84eb246edb27c78422469ca50` 虽被模型审核为通过，独立审查仍发现关系与事件误读，已通过正式 agent review ledger 停止其规划/缓存用途，不能继续出图。子分析 `analysis-2aa40035a38060509cf9ff5ecb260835` 已执行一次纠错与全来源重审，独立复核确认六条错误修正，父分析和原调用记录保留。最终主工作区后端2,678项、前端226项测试通过，包含通用排版、单图审核范围与旧入口市场修复。最终方向 direction-8c72019caaa442ddb3c07a695d6c54d7 已批准，cover-e63f4fced87941758a74a62453e5aa70 已生成3张原生1024×1536图，实际图片调用3次，额外额度0。三张均已通过正式本地排版提案、精确预览批准和应用流程修正标题，未增加图片调用。最终set revision=13；A/B=human_review_required、无硬阻塞，C=blocked（门框architectural_smearing），不能宣称三图全部达标。A故事反差最直接，B可备选；新封面未选中，原active set与39个旧交付文件的SHA均保持。C如继续修复需另有图片额度授权，不能绕过原额外额度0。审阅ZIP和具体图片/evaluation/amendment信息见交接文档。

用户最新强调交付适用于任意小说的高质量封面框架。本书仅是实际验收案例；书名、人名、受众、构图不得硬编码进通用默认值，人物数量与压力类型应从每本来源推导。跨题材fixture验证工程合同，不能据此宣称各题材审美已通过。旧入口不再从英文推断北美；单图审核区分全书人物registry与已批准cast，作者约束保持原范围。通用本地排版支持overlay和完整原画contain_panel，保留不可变历史、短锁CAS、独立盲读和准确图片预算。仓库封面Skill已更新，本机已安装副本未覆盖。

## 2026-10-11 追加：四图默认与下载修复

用户明确要求后续新批次默认提供 **4 张**，本书保留当前 **3 张**，不补图。代码和实际 Studio 配置原本已经是 4；本次补齐仓库封面 Skill 的四图默认说明，以及项目/讨论页“旧批准三图、新规划四图”回归。旧方向、批准摘要、已用额度和三张图片保持。13:42 核对实际集合已为 revision=14，A 存在 13:34 完成的新审核 `evaluation-67c2b530d76d4f49ae92d390aa6c50c6`；本次下载修复没有触发审核，不覆盖这项并行状态，也不把早先 revision=13 审阅 ZIP 称为最新审核。三图字节、A/B 人工审核与 C 阻塞状态、39 个旧交付文件和原选中均保持。详见 [后续验证报告](docs/reports/2026-10-11-cover-defaults-and-package-download.md)。

工作台“交付包”按钮下载无后缀的原因已复现：Playwright 在用户 Chrome 的默认 context 使用 `Browser.setDownloadBehavior(allowAndName)`，Chrome 按 GUID 保存到 `playwright-artifacts` 临时目录。接口本身返回有效 ZIP 和正确文件名。已恢复当前浏览器 `behavior=default`，同按钮连续两次分别保存 `Downloads/book-package.zip` 与 `book-package (1).zip`；38 条目 CRC 正常，SHA 与接口及原包一致。

后续浏览器验收必须避免遗留下载接管：复用用户 Chrome 的自动化 context 时，验收完成恢复 `Browser.setDownloadBehavior({behavior: "default", eventsEnabled: false})`，并以 Chrome 下载记录和实际文件名验证，不能只看 MCP 的 `saveAs` 副本。新建或重连 Playwright context 可能再次接管；这次恢复不代表全局 MCP 配置永久改变。无需给产品 ZIP 代码加入绕过逻辑。本轮未重新出图或替换正式选中封面；候选审阅 ZIP 与工作台正式图书包用途不同。

## 2026-10-11 追加：工作室视觉、费用账本与图片型号

用户确认费用采用可配置模型单价和汇率的人民币估算；支持 Sunburst / Flare，新 API 图像配置默认 Sunburst。实现及限制见 [本轮交付报告](docs/reports/2026-10-11-studio-costs-and-image-models.md)。继续以主工作区为准，不能用旧临时修复树覆盖。

封面页按 frontend-design Skill 改为浅色出版工作室，压缩骨架来源与四阶段进度、突出完整比例画廊。分析输入仍为完整骨架，不是自动扫描正文。费用账本进入作品详情、头脑风暴和封面；设置页新增“费用估算”。真实 Token 按讨论/正文/封面归属记录，缓存与推理子项不重复计总数，缺失用量不按零计。讨论启动为作品后关联汇总，不复制账本。只有现存诊断能够回收部分旧用量；过去丢失的正文/封面 Token 不能凭字数重建。

费用设置默认参考换算值 7.0，标为假定值；gpt-5.6-sol 标准短上下文参考单价为输入/缓存输入/输出每百万 $4/$0.4/$20，其余型号由用户配置。图片按配置的每张价格估算，图片工具未返回的 Token 仍未知。估算不是订阅扣款；单价修改会重新估算已记录用量。

API 可传精确 gpt-image-2.5-sunburst / gpt-image-2.5-flare；裸名不自动替换。当前 Codex CLI 0.162.1 的图片工具没有精确图片型号参数，因此 Codex 旧通道保持 gpt-image-2 并明确说明限制。已有 profile、legacy 配置、旧批准与生成图均不静默升级。生成记录区分 requested/observed model，没有供应商确认时不宣称已核验型号。此次没有授权或执行新的实际模型调用。

代码、独立审查、全量验证和本机部署均已完成：后端 2,731 项、前端 248 项通过，末轮样式专项/构建/lint 通过。正式 `localhost:5174` 前后端 healthy，160 个运行源码/Skill SHA 与主项目相同；14 本作品费用 API 验收通过；1,219 个原有封面/交付/来源文件及 Studio 配置 SHA 保持不变。Secret Daughter 可回收历史 520,263 Token，按当前参考价可计算小计 ¥15.881869，但 224 条调用缺完整用量且部分型号未配价，不能称作该书总成本。此次未作任何新真实模型调用。

过程审计目录 `/private/tmp/novelos-studio-cost-20261011/`，运行版本回滚标签 `novel-os-backend:pre-studio-20261011` 和 `novel-os-frontend:pre-studio-20261011` 已建立。实际新镜像 SHA 与桌面/窄屏截图见报告。独立浏览器验收 context 已关闭，用户 Chrome 下载行为已恢复 default。用户截图中的 Secret Daughter 骨架分析已在 14:11 正常完成，本轮没有中止它。工作区保留既有改动，未提交/推送。

## 2026-10-11 追加：封面美术方向切页恢复

用户报告「创建美术方向」切换页面后任务状态丢失。已复现并修复：新增项目/讨论所属的持久方向任务，由现有 JobRunner 后台执行，UI 挂载恢复、运行轮询、完成刷新方向和费用，失败/中断保存原因。重复点击合并活动任务；重启后不自动重新调用模型。旧同步 API 兼容，UI 的来源绑定和旧版事实确认入口均使用新后台接口。来源校验、费用归属和删除保护保留。

独立审查修复了提交锁跨作品残留、旧方向列表覆盖新结果，以及派发/完成时误判中断的竞态；状态 GET 不重建已删除目录。后端全量 **2,748 项**、前端 **258 项**通过，最后提示文案修正后专项 31 项、lint/构建通过。正式 `localhost:5174` 已更新且前后端 healthy，161 个运行源码/Skill SHA 与主项目相同。浏览器切页、刷新、失败恢复及窄屏验收通过，14 本作品与讨论任务 GET 通过；1,294 个原有封面/交付/来源文件及 Studio 配置 SHA 保持。没有真实模型调用，没有增加封面或改变已批准数量。

详见 [方向任务恢复报告](docs/reports/2026-10-11-cover-direction-task-recovery.md)。审计目录 `/private/tmp/novelos-direction-task-20261011/`；回滚标签 `novel-os-backend:pre-direction-task-20261011`、`novel-os-frontend:pre-direction-task-20261011`。浏览器 QA context 已关闭并恢复 default 下载行为；未提交/推送。

## 2026-10-11 追加：图片模型列表与保存修复

用户确认报错发生在保存图片模型设置。已修复：图片目录不再复用文本 `discovered_models`，新增独立 `/image-models` 接口、图片缓存、图像端点查询和连接/凭据并发校验。API 目录项是未经图像能力验证的候选，允许服务商自定义完整 ID，刷新不覆盖手填值。新 API 图片配置默认 Sunburst，也支持 Flare；不能从目录成功推断账户已经能出图。

**当前 Codex 内置图片工具没有精确图片型号选择参数。** UI 显示「自动选择」，显式保存提交内部 `codex-image-auto`，不能将其当成真实模型 ID。旧 `gpt-image-2` profile 保持兼容且不静默迁移；读取或拉取目录不会改写。Codex 返回空图片目录并解释能力限制，不再用文本模型或固定 gpt-image-2 冒充拉取结果。生成提示不再要求工具参数无法选择的型号，未返回实际型号仍标记未知/未核验。选择 Sunburst/Flare 需要支持图片生成的 API 连接，不能声称当前 Codex 通道已精确使用它们。

版本说明纠正：本轮正式 Docker Codex 实测 **0.153.0**，宿主机 **0.162.1**；此前引用宿主机版本的报告不代表正式容器版本。本轮未升级 CLI。设置有未保存修改时禁用图片测试，测试确认与请求均绑定已保存配置；保存本身不调用模型。

独立审查通过；最终后端 **2,758 项**、前端 **266 项**通过，lint/构建通过。已更新正式 `localhost:5174`，前后端 healthy、161 个运行源码/Skill SHA 一致，14 本作品及关联讨论查询通过。浏览器 Codex 真实目录 GET + 拦截保存、API 模拟目录/保存、刷新失败保留输入、桌面与 390px 窄屏通过；服务端实际 HTTP 保存由隔离配置回归覆盖。1,296 个既有封面/来源/交付文件和正式配置 SHA 未变；零真实模型调用，未增加封面或改批准、选择和预算。

详见 [图片模型目录与保存报告](docs/reports/2026-10-11-image-model-catalog-and-save.md)。审计目录 `/private/tmp/novelos-image-catalog-20261011/`；回滚标签 `novel-os-backend:pre-image-catalog-20261011`、`novel-os-frontend:pre-image-catalog-20261011`。浏览器 QA context 已关闭，下载行为恢复 default；未提交/推送。

该轮另有只读状态发现：Secret Daughter 的方向任务 `direction-task-8b94c6e861604f8fb1ff1053586772cc` 已在 07:26:59 UTC 失败，原因是 `campaign request exceeds its byte budget; no story facts or evidence were truncated`。此独立问题已在下一节修复；原失败任务保留，没有自动付费重放。

## 2026-10-11 追加：方向请求字节预算修复

用户指定 `b1bd6c12ef6b` 排查 campaign byte budget 报错。正文运行已完成，失败的是上节的独立方向任务。完整骨架 67,306 字节、200 条证据；旧首轮请求 339,814 字节接近 384,000 上限，后续修复/审核重复证据并追加模型稿后容易越界。历史账本确认首轮文本调用已完成（配置 gpt-6-astra，139,771 Token），但原回复未保存，不能断言当时具体是哪一种后续阶段失败。14 本作品、3 个讨论中仅此精确报错有记录；另 10 本骨架更大但没有可同样重放的完整当前分析，因此结论是通用容量缺陷、并非每本必现。

新增 `core/cover_campaign_transport.py`，在创建、格式修复、语义修复、独立审核的发送边界无损合并重复证据，保留完整来源、全部字段、原 ID、所有方案和完整回复；严格复核来源绑定、引文哈希和可逆还原。只在实际请求更小时使用，旧不带来源的调用保持。预算按最终发送字符串核对，未抬高上限；真正超限仍在调用前阻止并显示阶段、实际值及限额。首轮降为 262,612 字节，带 70 KB 注入回复的修复请求由 407,193 降为 329,996 字节；不能把注入回复当成历史模型稿。

独立审查通过，最终完整后端 **2,784 项**通过，正式 `localhost:5174` 已更新、前后端 healthy，162 个源码/Skill SHA 一致。容器内真实保存输入的离线重放通过；14 本作品及关联讨论查询正常，1,296 个原有封面/来源/交付文件和正式设置 SHA 保持。没有真实模型调用、没有自动重试、没有新方向或封面。旧失败任务继续显示失败，刷新后可手动重新创建方向；本轮未进行实际新设计质量验收。

详见 [方向请求预算报告](docs/reports/2026-10-11-cover-campaign-request-budget.md)。审计目录 `/private/tmp/novelos-campaign-budget-20261011/`，回滚标签 `novel-os-backend:pre-campaign-budget-20261011`、`novel-os-frontend:pre-campaign-budget-20261011`；前端镜像未改。全量回归必须给予本机 HTTP bind 与 ps 所需权限，受限沙箱会导致 17 个本机服务案例和 3 个子进程检查失败；已在必要权限环境完整通过，没有跳过。未提交/推送，保留既有工作区改动。

## 2026-10-11 追加：封面构图框架修复

用户以 Secret Daughter 新四张候选均「需修复」要求优化通用框架。真实集合 `cover-3a4e42a72a224d7aadf69ea22ab0ba96` revision 10，方向 `direction-a4aef54cb2cc4b23abf00cb055bbc04a`；四图主要是冲突／行动／因果读不出来，第二图另有合成层混淆，画质评分较高。确认图片与审核绑定正确，不是旧报告套错图。旧 v16 campaign compiler 遗漏全套具体视线（本例 0/13 保留），并未完整传递 frozen_action、缩略图阅读和 safe_zones；其他字段保留部分动作，不应声称全部动作消失。

新 compiler v17 完整保护行动、视线（含 JSON layer/temporal scope）、缩略图要求、标题保护区及合成层边界。规划和独立语义审核新增静态画面竞争解读检查、实际缩略图可读性、全书冲突与单图承诺分离，避免四方案被同一微动作锁死；不强制多人或大动作、不改故事事实。真实编译预检进入原两次修复循环，超 12k 仍不截断。A/B 审核新增各自图像类型、SHA 和遮罩上下文，避免无字原画与排版成品的正常标题差异被误判，但不清除真实阻塞或升级旧不确定性。UI 展示具体原因分类、派生 blocker 证据缺口及独立盲读可靠性。

后端 **2,802 项**、前端 **272 项**通过，构建/lint、独立审查、桌面及 390px 浏览器验收通过。`localhost:5174` 前后端 healthy，162 个源码/Skill SHA 一致；真实四方向 13/13 视线完整保留，容器离线审核重放及此前字节预算案例通过。14 本作品与讨论接口正常，1,380 个已有封面/来源/交付文件及设置 SHA 保持。此轮零真实模型调用、零重绘；四张仍按原审核阻塞且未选中。重新创建方向才使用新版，旧批准仍读冻结 v16 recipe，不能自动迁移审批或消耗额外额度0。

详见 [构图框架报告](docs/reports/2026-10-11-cover-composition-framework.md)。审计 `/private/tmp/novelos-composition-20261011/`；回滚标签 `pre-composition-20261011`（前后端各自镜像）。部署本地 dist 必须使用 **`VITE_API_BASE='' npm run build`**；本轮浏览器 QA 发现缺此变量指向 localhost:8000，已修正重建且验收正常。QA context 关闭、Chrome 下载行为 default；仓库 Skill 已更新，本机安装副本未覆盖。未提交/推送。

## 2026-10-11 追加：规划中状态可见性

用户20:03截图看不到方向任务是否在执行。Secret Daughter 新任务 `direction-task-afabbf9b638149a0b6459882907b2ecc`、job `8fe5d334cdbd42b684a3976c8aa4ed55` 于19:53:08开始；只读账本确认20:01:47和20:06:22分别完成两次调用并进入第三次，20:11验收仍running，非单次调用始终无进展。只有stage=cover，不能推断精确审核／修订阶段；task.updated_at不代表模型心跳，继续时需重新查询实时状态。

状态条从长骨架面板后移至页头下，旧方向存在时也显示；新增活动标记、包含排队的真实耗时、终态finished_at冻结、浏览器成功同步时间。同步失败／超过15秒无响应时标明上次确认和当前待确认；同步时间不冒充模型产出。hook先发布已确认completed再加载方案，方案读取失败不会回退running。无假百分比、无额外模型调用。前端286项、lint/构建、独立审查和桌面／390px／切页刷新验收通过。

已仅更新frontend（`--no-deps`），backend容器ID/StartedAt/RestartCount/PID/镜像/健康完全不变，当前任务未中断。前端镜像`774edd37d38c5cd995f350ed7f2771bdac4bf017c8d27d108fa01cbfd04d501b`，回滚`novel-os-frontend:pre-direction-status-20261011`；740个既有候选／评估／来源／交付文件SHA保持。QA context关闭、下载default，未提交/推送。详见[状态可见性报告](docs/reports/2026-10-11-cover-direction-status-visibility.md)，审计`/private/tmp/novelos-direction-status-20261011/`。

## 2026-10-11 追加：方向互动证据审核修复

Secret Daughter 的 `direction-task-afabbf9b638149a0b6459882907b2ecc` 已于 20:16 失败，最终三条 `campaign_interaction_evidence` 对应同层三项互动，不能解释为三轮修复日志。任务共 4 次已完成文本调用、423,993 Token；旧流程没有保存失败稿，ephemeral 回复已清除，不能确认实际三项引用或断言全部误判。该书真实原文能复现通用缺陷：直接描述同一事件动作的有效引文不在事件摘要锚点列表中，被编号交集规则拦截。

新 live 方向逐故事层、逐互动独立审核自身引用与完整来源，必须有完整唯一的 supported/unsupported/uncertain 判断。仅全部 supported 且整体审核通过才保存应用回执，绑定完整 analysis 和 plans，在保存、批准、生成时复核；未知引用/人物/事件/时空仍阻断，不自动添加摘要锚点。模型提供的 source_binding 仍剥离；历史无回执方向保持原批准与配方。来源损坏、审核协议缺失不触发付费方向修复。新增 owner-local `covers/direction_attempts/<uuid>/` 原子保存调用开始、完整回复、检查和终态，写入失败在下一次模型调用前停止，不进入方向列表或出版交付。第二次调用未收到回复就失败也准确记录 attempt=2。

完整回归 2,852 项：2,851 通过，既有 0.6 秒遥测 timeout 用例首次失败，该组 10 项复跑全部通过；最后改动封面/Skill/交付专项 **1,019 项通过**。独立审查通过。20:33 已部署正式 5174，164 个运行源码/Skill SHA 一致、前后端 healthy、14 本作品及关联讨论接口正常。仅更新后端，前端保持原容器/镜像；1,383 个旧数据/设置文件 SHA 未变。原四图、批准、选择、额度及失败任务均保留。零真实模型调用；没有自动重新规划或出图。

详见 [互动证据修复报告](docs/reports/2026-10-11-cover-interaction-evidence-review.md)。审计 `/private/tmp/novelos-interaction-evidence-20261011/`；回滚 `novel-os-backend:pre-interaction-evidence-20261011`；新后端 `sha256:bb51b42722ca027cfaa288ab671127ae874623353dc6ed496cf7ca38269fc9b4`。下一次用户手动创建方向才应用新规则；不能保证真实故事/构图审核必定通过。仓库 Skill 已更新，宿主机安装副本未覆盖，未提交/推送。

## 2026-10-11 追加：方向规划耗时与定向修订

用户要求解释创建方向和 Token 更新慢并提速。最新任务 `direction-task-4fa966fd24134bc5aca3298bf9d4b37e`（诊断 `d286cac7505f4886b45c4f6a2afd0941`）20:35–20:57 共22分钟，五次调用占99.94%；三次完整四方向生成、两次独立审核，共524,308 Token。两次修订整组重写合计近10分钟。最终第四方向仍有静态画面竞争解读问题，失败记录保留，不能为了速度放行。当前配置Codex/gpt-6-astra/high未改；用量在每次调用结束结算，面板不是实时TPS。

新增 `cover-direction-patch.v1`：来源绑定方向的修订按原始完整稿hash返回顶层字段完整替换，保留ID/顺序/数量；局部合并后继续完整schema、来源、编译及独立逐证据/构图审核。无效补丁保留基线，连续补丁绑定已合并稿，仍最多两次修复；兼容有效完整回复。初轮创意探索保留，修订不重复无关探索。实际保存稿离线差异可减少两轮回复字节86.22%/26.84%，不是已测模型耗时改善。固定来源JSON前置保持全部内容，公共前缀136,041→240,628B；当前Codex通道无应用可控显式缓存断点，共同字节不保证GPT-6缓存命中。

公开任务进度绑定精确owner/task和私有diagnostic UUID，诊断先持久化，再发布真实生成/修订/独立审核/保存阶段、轮次与已收到回复数。切页恢复，规划器完成不冒充保存完成；无虚构百分比/实时速度，失败/重启保留最后阶段且不自动付费重放。

独立审查通过，完整后端2,922项、前端288项、lint/生产构建通过。正式5174已更新且healthy，165源码/Skill SHA一致、14作品与关联讨论GET正常；1,427个旧来源/封面/交付/用量/设置文件SHA保持，容器内真实三稿重建严格一致。零真实模型调用、零图片新增，旧选择/批准/预算/失败任务保留。隔离浏览器15项检查通过（真实页面+本地GET拦截模拟阶段），桌面/390px/切页刷新正常、零POST和页面错误，真实任务未改；context/browser关闭且下载default。详见[耗时与提速报告](docs/reports/2026-10-11-cover-direction-latency.md)，审计`/private/tmp/novelos-direction-speed-20261011/`，回滚标签前后端`pre-direction-speed-20261011`。仓库Skill已更新，宿主机副本未覆盖；未提交/推送。

## 2026-10-11 追加：方向修订反馈与可读性收敛

最新失败任务 `direction-task-ee78e835e01d47a1bd6f994749bcdcbd`（诊断 `027e670c9f1645069400b7e6c7e3a122`）21:44–22:01、约17分43秒，5次调用516,581 Token。保存链确认补丁没有丢失：第二稿已修1/3/4，但4的图片提示12,197字符超12,000，旧流程跳过该轮全部语义审核；第三稿只修4长度，1/3只得到一次语义修订。门口图确实暗示无原文依据的访问限制；朋友图则rationale收窄、hook仍承诺不可从画面证明的“缺失支持”。不能将二者全部归为误判。

本轮来源/结构合法后同轮收集标题、编译与完整语义反馈；保留审核refs与上次实际审核的plan hash/轮次，格式或本地错误不清空。pending feedback无审批权，修改后仍完整独立审核。失败concept ID只是槽位，可以换来源事件、cast、topology、hook和全部依赖；无关有效方案保留。新增共享 `cover-readability.v1` 进入规划、修订、独立审核和Stage B；允许相容情绪歧义与留白，仍阻止虚构行为/权限/身份/因果，收窄承诺不能只改解释或变成普通悲伤。Stage A保持盲读，既有审批不迁移。最多三稿三审仍不变，执行失败稿可能多审一次，不能保证每次更快、更省或真实规划必过。

真实完整链离线重放已确认最后修订能同时收到执行错误和最新语义意见+refs；原坏稿仍阻断。完整67,306B来源及200条证据保留，新最大请求339,814B<384,000。独立审查又发现标题错误遮住别图编译错误，一并修复并先红后绿；10项收敛回归通过。完整后端2,972项首次2,971通过，唯一既有本地CLI替身2秒超时，原断言/时限未改，隔离诊断/遥测16项全过。22:23仅更新5174后端，前端容器/镜像/启动时间不变；167源码/Skill SHA一致、14作品及讨论接口正常、25,044旧数据/配置文件SHA保持。正式容器离线重放通过且原坏稿仍阻断。新后端`sha256:cc9bfd7f622fb7d696239f7d60333a05cec1f1f43499478a5051c175419953b0`，回滚`novel-os-backend:pre-semantic-convergence-20261011`。更多验证及部署结果见[收敛修复报告](docs/reports/2026-10-11-cover-semantic-convergence.md)。本轮零真实模型调用、未自动重试、未出新图。审计目录`/private/tmp/novelos-semantic-convergence-20261011/`；仓库Skill已更新，宿主机副本未覆盖；未提交/推送。

## 恢复时的历史基线

**最近尚未交付的工作是封面 `cover_constraints` 全链路修复及同步验收。** 它来自“查询小说创作任务”会话后半段。2026-10-09 的“已完成”答复被 10 月 10 日的审查推翻，不能作为最终验收结论。

当前本地核对结果：

| 位置 | 分支与 HEAD | 状态 |
| --- | --- | --- |
| `/Users/max/OpenSource/Novel-OS` | `stable/baseline-65e1195`，`b586c0bc4ba2da14468d3dac5c58d159e8318a56` | 恢复前已有 59 个修改文件、69 个未跟踪文件；包含头脑风暴、任务恢复和封面工作台开发 |
| `/private/tmp/Novel-OS-cover-constraints-fix` | `fix/cover-constraints-chain`，`f804bb9186d823619aa247c4f9dacd7bd1d8f9c5` | 以原工作区快照为基线；14 个修改文件和 3 个未跟踪文件，修复尚未提交 |

修复工作树新增 `core/cover_constraint_sources.py` 及两个来源边界测试文件。其 `cover_analysis.py`、`cover_validator.py`、`cover_prompt_compiler.py`、`cover_director.py`、`cover_quality.py` 和相关 Skill/交接文档有后续修复；当前主工作区不能假定已包含这些修复。

历史审查发现的 8 项问题：

1. 默认合成封面的图层和互动动作绕过禁止内容检查。
2. “不要展示……”等否定语句被当成违规内容。
3. 来源 ID 子串匹配导致错误通过。
4. 带冒号的普通文本被误认成来源引用。
5. 长提示词压缩时丢失约束。
6. 全文骨架分析重建 brief 时丢失原始约束。
7. 图片质量审核未接收到用户约束。
8. 章节来源引用非空就通过，未真正解析。

最后进度记录称修复工作树 **533 项封面测试通过**，正在核对 Skill 文档和主工作区差异；更早全仓测试报告有 6 个 Codex telemetry 失败。此处均为历史测试报告，本次恢复没有重跑或证明全仓通过。之后的多次“继续”因模型容量不足结束，没有最终同步交付确认。

后续继续该任务时，应先检查两个工作区的新变化，复核修复及必要测试，再合并到主工作区并完成验收。保留已有改动，不能用 reset、整目录覆盖或旧快照替换当前工作区。独立工作树位于临时目录，继续前先确认它仍存在。

入口：

- [主工作区交接文档](docs/design/2026-10-09-cover-constraints-studio-handoff.md)
- [隔离修复工作树交接文档](/private/tmp/Novel-OS-cover-constraints-fix/docs/design/2026-10-09-cover-constraints-studio-handoff.md)
- [旧限定路径补丁](docs/design/2026-10-09-cover-constraints-studio-handoff.patch)：这是当时 HEAD 到脏工作区的限定路径 diff，包含此前改动；不是最新修复补丁，也不适合盲目再次应用。

## 已确认的产品和创作要求

- 产品以作品库为入口，支持多本旧作、新建小说、任务状态、故事工作区和交付包。流程为：想法 → 多轮头脑风暴 → 完整骨架 → 确认书名和启动参数 → 既有创作引擎 → 章节、进度、封面和交付。
- Web 中文化、充分利用屏幕，详情页要让审核者不读完整正文也能看懂故事、冲突、人物变化和爽点。延续现有浅色蓝紫玻璃风格，减少杂色及大面积黑色。
- 头脑风暴允许反复修正，不限制轮次，可自定义模型；主动提出有因果差异的剧情走线。探索阶段先设计整本书的选择、后果、伏笔与结局，再收敛完整骨架。
- 骨架和用户硬限制要进入实际运行约束。姓名、身份、性别、代词、别名稳定；人物心理按“事件 → 理解 → 情绪冲突 → 选择 → 后果”展开。
- 每章承接前章，分层兑现爽点并制造有因果的新压力。强化前三章，但不能提前耗尽全书吸引力，后半段靠重复冲突凑字数。
- 复仇、伦理、男女成长、CEO、黑手党、狼人、玄幻、恐怖、家庭背叛等扩展类型已纳入 10 月 7 日的 `b586c0b`。类型必须影响故事设计，不能只是标签。
- 用户素材用于原创剧情设计，不能照搬。在创作任务中按适用 Skill 先设计、确认正典再写作；被明确废弃的试写不进入故事状态。
- 商业写作默认不接 RAG、向量检索或模仿语料，不强制联网市场调研；早期要求联网的方案已被后续决定覆盖。学习通用叙事机制，不把原作的独特事件顺序、对白和物件组合换名复制。
- 既有英文家庭背叛作品多面向欧美中年女性：自然英文、强化免费前 3–4 章的主动选择和局部兑现，减少复杂财务/股权线；独立导读进入 MD 和 EPUB 第一章前。年龄、章节数、字数、卷/系列和免费窗口按每本书确认，不提升成全部作品的固定配置。
- 用户确认故事方向与配置后，既有目标是引擎自动规划、写作、编辑、限次修复和全书交付；不要增加逐章人工润色或逐章审批作为必经步骤。
- 头脑风暴和封面改造应复用既有正文执行引擎。历史“只改 Skills，不改框架”属于当时对应任务的范围限制，不等于未来所有开发永久禁止改引擎。

## 封面工作流的最终边界

- 默认使用该项目的完整小说骨架 `outputs/input/prompt.md`，通过项目 ID / 讨论 ID、修订号和摘要值确定来源。用户已要求移除复杂的默认来源选择，不把完整小说正文当作默认分析输入。
- 保留完整骨架来源快照和来源覆盖；相同来源内容复用分析缓存，不能因切换方向或重试图片就重新分析整本故事。
- 头脑风暴负责故事事实、人物关系、关键节点、伏笔及用户限制；封面工作台负责封面时刻、角色动作、空间关系、构图、视觉钩子、方向组合和图片提示词。
- 使用现有 `COVER_HANDOFF` V2，新增薄的可选 `cover_constraints`。此前提出的完整 `cover_design_contract`、`candidate_moments` 等平行设计结构已被收窄，避免两个模块重复做封面分析。
- 封面需要电影海报式的故事感、冲突和悬念；完整保留可用角色与关系，不能总是同一女主换道具。多人是否入画应由故事关系与该方向决定。
- 既有封面偏好为自然、生动的真人电影摄影，避免摆拍、蜡感皮肤和过度锐化；后续已取消固定多人同框比例。不得从英文或发行市场推断人物族裔。
- 外貌、服装、光线等缺失细节允许模型提出封面视觉方案，不应自动阻断；不得将视觉推演写回故事正典。缺少核心关系、冲突、故事节点或明确边界时才需要补足事实。
- 用户明确的禁止内容、人物/节点/章节引用和剧透限制必须从冻结骨架贯穿分析、方向、编译、验证和图片审核。自由文本限制还需语义判断，不能把字符串检查称作全面保证。
- 小说正文任务完成和创建封面方向是独立流程；有封面交接信息不代表方向或图片已经生成。

相关资料：[封面审查](docs/reports/cover-design-audit-and-optimization-2026-10-08.md)、[骨架封面设计](docs/superpowers/specs/2026-10-08-skeleton-driven-cover-studio-design.md)、[骨架清单](docs/reports/novel-skeleton-inventory-2026-10-08.md)。这些文档包含各自时间点的状态，需结合上述较晚决定使用。

## 当前已核对的小说和讨论状态

以下来自本次读取本地 JSON，不使用历史答复替代当前状态。所有数据目录均相对于仓库根目录。

| 作品或讨论 | 标识 | 2026-10-10 核对状态 |
| --- | --- | --- |
| He Chose His Hidden Family for Twenty Years and Abandoned Our Daughter During Her Most Important Life Events. | 项目 `he-chose-his-hidden-family-for-twenty-years-and-abandoned-our-daughter-during-her-most-important-life-events`；run `956ec62d39f6` | `completed`；`book.md` 与 `book.epub` 存在。同项目另有较早失败 run `c53ae28aa970`，不能混淆 |
| My Husband’s Secret Daughter Lived Across the Street | 项目 `my-husband-s-secret-daughter-lived-across-the-street-d7c6589c`；run `b1bd6c12ef6b` | **`completed`**；MD、HTML、DOCX、EPUB、PDF 均存在。旧会话中出版文案失败的状态已过时 |
| 上述 Secret Daughter 的头脑风暴 | `d7c6589ce29349ae99121b5f771fd6d1` | revision 17，`launched`，已关联该项目；`versions/12.md` 只是旧快照 |
| Pregnant and Betrayed: He Chose Another 讨论 | `a9f05b12de274e649ee4ce31e1d16c25` | revision 16，`paused`，未关联项目；保留候选稿，不能等同于已有正式骨架 |
| 连接验收草稿 | `250b8ab6c3b742e2805d1a09458d3708` | revision 2，`draft`，未关联项目 |

恢复小说前定位：

- 讨论：`docker-data/workshop_v2/<讨论 ID>/session.json`，历史骨架 `versions/`、候选稿 `candidates/`。
- 项目骨架：`docker-data/projects/<项目>/outputs/input/prompt.md`。
- 故事正典和章节状态：`outputs/state/story_state.json`。
- 执行状态：`outputs/runs/<run ID>/run.json` 及同目录事件记录。
- 交付文件：`outputs/deliverables/`。

历史 EPUB 缺失案例的原因是启动参数仅设 `NOVEL_OS_OUTPUT=markdown`；该运行的格式被锁定，普通 resume 不会自动增加 EPUB。后来已基于现有 21 章单独补编 EPUB 与 ZIP，没有重新创作。不能据此重复启动整本生成。

## 已完成开发与仍需确认的事项

已在代码/历史交付中记录的能力包括：作品类型扩展、身份锁定与章节递进、Docker 配置整理、头脑风暴暂停恢复、真实任务状态与任务 ID、失败原因与恢复按钮、骨架预览和完整来源封面工作台。这是最初恢复记忆时的记录；随后封面框架已有本轮部署与验证，见开头进度和接管文档。

头脑风暴等待问题曾有多种不同原因，不能合并成一个“已根治”结论：

- 讨论锁文件的线程竞态导致假运行；已有针对性修复报告。
- Python 3.11 下大提示词 stdin 传输未完成；已有修复与容器验证报告。
- 封面字段格式错误触发整稿重试，日志与内容计数又掩盖了已返回的大段输出；已有无损格式修复和骨架恢复报告。
- 另一次完整输入后 900 秒静默缺少足够上游证据，不能断言模型一直思考或已经找到唯一原因。
- 当前正文回复的增量展示 / SSE 属于后续方案；真实阶段与内容计数不等于已经逐字流式展示。
- 已保存数据持久化不等于重启后自动续跑。历史实现使用手动恢复/重试，继续前应核对现有运行器。

诊断入口：[运行状态与流式方案](docs/design/2026-10-08-workshop-progress-and-streaming.md)、[模型诊断](docs/reports/workshop-model-diagnostics-2026-10-08.md)、[stdin 修复](docs/reports/workshop-stdin-deadlock-2026-10-08.md)、[上游静默](docs/reports/workshop-upstream-silence-2026-10-08.md)、[格式重试](docs/reports/workshop-cover-contract-retry-2026-10-08.md)。

H5 历史外部联调问题：2026-10-04 的会话发现导入器硬编码 8 个版本字段，不兼容含 `author_sha256` 的 9 字段包。当时只诊断，未修改或部署 H5；它属于另一个仓库的兼容事项，本文不宣称其现在仍失败或已修复。

## 架构与运行入口

- 2026-09-24 用户停止修复实验版，历史记录将 118 文件的开发状态保存到 `feat/novel-quality-closure` 的 `17aadbb` 后切回稳定分支；不应把该实验版遗留任务当成当前必须恢复的工作。其代码、提交和备份存在性需在真正使用前再核对。
- 五代理：Architect → Scribe → Editor → Continuity Guardian → Style Curator；质量门、正典状态和阶段检查点应保持一致。
- 质量内核的长期约束：LLM 输出是候选，评审绑定具体修订和证据；由受控晋级提交正文与 Canon。恢复时以有效 artifact、heads、ledger、receipts 为依据，不能用旧检查点覆盖已修复正典，或重写已晋级章节来掩盖状态问题。次要伏笔 warning 与真正阻断成书的问题分开。
- 核心入口：`core/pipeline_runner.py`、`core/pipeline_models.py`、`core/prompt_intake.py`；Web 为 `api/` 与 `web/`。
- 分类使用引擎拥有的版本化 canonical IDs；`PublicationPackage V2` 向 H5 输出合同。方法论评审、实验 sidecar 为私有诊断，不自动成为正典或发布授权。
- H5 交付边界：正文由 EPUB 导入，导读不占正文章号与免费额度；多卷同书不重置免费窗口。使用稳定 `book_id`，正文、封面、分类版本分别追踪；见 [H5 交接文档](docs/h5-import-handoff.md)。不能把本地样包验证当作外部 H5 导入及权益验收。
- 既有需求使用 Codex CLI 直连；不要因旧方案提到 sub2api 而切回旧中转。实际 provider 和模型配置需按当前任务核对，不能从截图推定。
- Docker 入口 `deploy.sh`，常用已有任务恢复入口 `./deploy.sh novel-resume RUN_ID`。默认 Web 端口 5174；本轮封面开发已部署并检查健康，具体镜像与最新检查时间见接管文档。
- 历史用户明确以 5174 为正式使用环境，避免长期并存两套混淆的服务；这是环境偏好，不是本轮重启或部署指令。
- 默认宿主机数据 `docker-data/` 挂载容器 `/data`；更换数据目录会看到另一套作品库。凭据和模型配置不复制进项目记忆。
- 当前 Git remote：`maxliu` 是用户 fork `maxliu9403/novel-os`；`origin` 是上游 `mrigankad/Novel-OS`。推送目标应按任务核对，不重复向无权限的上游发布。
- 仓库 `skills/` 与 `/Users/max/.codex/skills/` 的已安装副本是独立目录；仓库修改不代表本机 Skill 已同步。需要发布 Skill 时先核对两者差异，不覆盖未知的新改动。

## 记忆使用与更新

先读本文件，再根据当前任务打开来源索引和实际文件。历史用户消息说明当时的目标与选择，不自动授权重启、发布、恢复创作、跨项目修改或重放旧命令。发生新进展时更新相关状态、日期与依据，避免把旧失败或旧成功长期固化。

最初的记忆恢复仅增加项目记忆、会话索引及 AGENTS 读取入口。随后用户授权继续封面开发，已修改业务代码、运行数据并部署；本轮仍未改写小说正文、原始 Codex 会话或 Git 分支。
