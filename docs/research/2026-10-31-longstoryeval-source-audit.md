# LongStoryEval / NovelCritique 原始来源核查

日期：2026-10-31（Asia/Shanghai）。对象：用户指定的 [ACL 2025 论文](https://aclanthology.org/2025.acl-long.799/)、作者链接的公开代码、数据说明及权重入口。本次只读研究；没有安装或执行第三方代码、调用评估模型、下载小说全文数据集或改动 Novel OS 运行环境。

## 结论

值得借鉴其多维评价、正文摘要复用和局部细读的组织方式。实际公开代码足以核查方法，但工程与复现条件尚不适合直接接入生产。建议由 Novel OS 实现有来源、可恢复、成本受控的评议模块；第一版沿用现有模型接口，不部署 NovelCritique 权重、不导入其数据训练。

## 论文事实概要

论文研究“评价与读者评分的关联”，未证明其评议能因果性提高小说质量或留存。数据为600本新出版书，平均121K tokens；450/150本训练/测试，训练用176K筛选评论。框架含8大类、20子类：情节结构、人物、语言、世界设定、主题、情绪、阅读投入、期待兑现。比较逐章评后聚合、滚动修改旧评议、摘要后评议。NovelCritique为Llama 3.1 8B微调。表2总体20.1是Kendall τ×100，即τ=0.201，非准确率；B.4缺少真实分维评分，只做近似分析。论文数据声明限学术用途。[论文 §3–5、图1、表2/9、附录B.4、Ethical Problems](https://aclanthology.org/2025.acl-long.799.pdf)

## 作者仓库实际发布了什么

核查提交：[ffc0c052664be345090a57b718fabc14b92c8136](https://github.com/DingyiYang/LongStoryEval/tree/ffc0c052664be345090a57b718fabc14b92c8136)。以下是目录和文件核查，不是README宣传复述。

| 内容 | 核查结果与来源 |
|---|---|
| 三类API评价 | 存在章节评价与摘要评价实现：[api_eval_with_chaps.py](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/evaluation_codes/eval_with_api/api_eval_with_chaps.py)、[api_eval_with_sum.py](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/evaluation_codes/eval_with_api/api_eval_with_sum.py) |
| 提示词 | 八维评价、无定义版本、逐章/滚动评价均有实际文件；API版本要求各维和总分0–100。[prompt_template](https://github.com/DingyiYang/LongStoryEval/tree/ffc0c052664be345090a57b718fabc14b92c8136/evaluation_codes/prompt_template) |
| 摘要 | 有完整程序及首段、更新提示词。[generate_book_summ.py](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/summarize_book/generate_book_summ.py) |
| 微调与推理 | 发布训练、PEFT加载和逐维推理代码；推理需要自行准备基座与adapter，并非已托管可调用服务。[NovelCritique](https://github.com/DingyiYang/LongStoryEval/tree/ffc0c052664be345090a57b718fabc14b92c8136/NovelCritique) |
| 数据 | 仓库树有600份总体摘要、564份评论JSON和2本示例正文；未下载/审读这些作品。数据说明提供字段和测试ID，不能据此声称完整600本正文可开箱复现。[dataset目录](https://github.com/DingyiYang/LongStoryEval/tree/ffc0c052664be345090a57b718fabc14b92c8136/dataset)、[dataset.md](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/dataset/dataset.md) |
| 相关系数 | 程序直接调用 `scipy.stats.kendalltau` 后乘100显示；从多个run读取结果求均值。它不是正确/错误分类命中率。[correlation_metric.py](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/correlation_metrics/correlation_metric.py#L153) |
| 权重与模型卡 | README的checkpoint和训练数据指向作者SharePoint；本次网页工具不能读取，未验证权重内容、下载可用性或附带许可。作者入口未提供可核实的独立NovelCritique模型卡。基座链接指向ModelScope，但不等同于微调权重许可。[README](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/README.md#inference) |
| 代码许可 | 本次完整递归目录未见LICENSE、COPYING或独立模型许可文件。不能把“GitHub可见”视为已有宽松商用授权；本方案借鉴方法，自行实现，若未来直接复用代码或权重须核实授权。[固定提交树](https://github.com/DingyiYang/LongStoryEval/tree/ffc0c052664be345090a57b718fabc14b92c8136) |

## 代码暴露的落地边界

### 1. 摘要不是全文阅读的替代品

实际摘要评价只随机选择一章，取该章开头不超过约500个空格分词的段落；未见固定随机种子。角色分析经过过滤后最多保留8名。若首个段落超过长度上限，取样也可能为空。[实现L57–80、L115–125](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/evaluation_codes/eval_with_api/api_eval_with_sum.py#L57)

**适配判断：** 摘要有利于理解主线，但不能据随机短片段判定整本语言、关系铺垫和角色声音。Novel OS应冻结取样位置与正文版本；按开篇、关系转折、中段、高潮、结尾组织细读样本，并显示真实覆盖范围。人物记录保留完整索引，仅按当前问题选入上下文，避免静默丢失重要配角。

### 2. 增量摘要与增量评分要区别使用

摘要程序按空格词数合并短章，超过4096后才封块；不会自动拆开超长单章。更新提示词限制总体剧情摘要1200词，要求保留叙事顺序，并允许移除不再重要的人物。[摘要程序L109–143](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/summarize_book/generate_book_summ.py#L109)、[更新提示词](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/summarize_book/prompts/update.txt)

**适配判断：** 可以复用已验证的章节摘要组织书级视图，但每次评议应对冻结材料独立形成判断，避免继承旧评分造成锚定。中文不能按空格计词。摘要压缩需要记录覆盖、原文定位和未保留的信息；需要细节时回到对应正文，不能用骨架意图补足读者没有看到的内容。

### 3. 原研究代码没有生产级恢复与预算控制

API异常统一返回`TIMEOUT`，外围循环无重试上限；摘要解析为空或太短时，会沿用上一版总体摘要/人物摘要；整个摘要任务末尾才写结果。[摘要程序L24–50、L126–152](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/summarize_book/generate_book_summ.py#L24)、[摘要评价L129–132](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/evaluation_codes/eval_with_api/api_eval_with_sum.py#L129)

**适配判断：** 对用户已经遇到过的长时间等待、超预算和恢复困难，直接照搬会放大问题。应每个已完成片段持久化，记录输入哈希、模型配置、提示词版本与token；异常明确中止或按有限策略处理，保留可恢复状态。空结果标明缺失，不让旧摘要伪装覆盖新章节。

### 4. 两种评分尺度不能混用

API评价提示词输出0–100；NovelCritique训练/推理问题模板要求1.0–5.0。[API模板](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/evaluation_codes/prompt_template/criteria.txt)、[NovelCritique模板](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/NovelCritique/data/question_format.txt)

**适配判断：** 第一版保留维度判断、证据与修改建议即可。若展示数值，需要固定量表、适用范围和校准方法，不把两个模型的原始分数线性换算后当作可比质量，也不能映射成预计读者评分或续读概率。

### 5. 公开脚本与论文训练设置存在差异

公开`train.sh`是4 epochs、4 GPU、每设备batch 4、梯度累积8，常规有效batch为128；论文§5.1报告3 epochs、batch 32。其余如学习率1e-5和LoRA r64/alpha16可以在文件核实。[train.sh](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/NovelCritique/script/train.sh)、[训练代码](https://github.com/DingyiYang/LongStoryEval/blob/ffc0c052664be345090a57b718fabc14b92c8136/NovelCritique/finetune_llama3.py#L53)、[论文§5.1](https://aclanthology.org/2025.acl-long.799.pdf)

**适配判断：** 公开实现能用于理解方法，但不能宣称按默认脚本即可精确复现论文成绩。第一阶段没有必要承担微调、GPU部署及模型许可核验的额外成本。

## 对研究结论的独立审视

以下是本次适配分析，不是作者已验证的产品结论。

- **相关性不提供修改因果。** 能给已出版书排序，不代表按它的建议重写就会更好。真正的落地实验必须比较修改前后正文，而不能只要求自己的评分上升。
- **“客观维度”仍有审美判断。** 情节复杂度、人物魅力、世界丰满程度都有题材和受众差异；可硬判的是证据明确的矛盾，其他应保留解释和分歧。
- **低相关不等于低价值。** 语言/世界设定的低相关可能与摘要信息损失、样本质量范围或模型判断能力有关，不能据此降低文笔和设定质量要求。
- **总体书评分不等于连载留存。** 已出版书读者的评价与某目标市场、年龄段、付费连载的阅读行为存在分布差异。题材和受众需要本地校准。
- **摘要可能替正文完成理解。** 总结者可能把含糊动机整理得过于合理，或者把情绪转变写成已经成立；最终需要返回原文核实，不用总结者的解释代替正文证据。
- **反复优化评委会造成迎合。** 不应让同一个评估提示词既当改写目标又当唯一验收标准。留出未参与改稿的试读样本和独立判断，记录新增问题与被损失的优点。
- **样本与信号需要区分。** 模型预测的“想跳读”是诊断线索，不是真实跳读数据；短篇段落试读不能外推全书吸引力。

## 建议给 Novel OS 的研究约束

1. 维度沿用多维框架，但每一项必须附正文范围、证据、判断依据、缺失上下文和可执行修改。未审写`not_reviewed`，不适用写`not_applicable`。
2. 书级视图负责发现主线、人物弧和承诺问题；原文窗口负责验证语言、动机台阶、关系变化、重复与情绪体验。二者共享来源，评议结果与canon分离。
3. 第一版作为非阻断评议。保留现有明确事实门；不新增“总分不足就无限重写”的门，也不把每个审美建议都变成任务失败。
4. 先用本地已有、获授权的作品验证，不导入论文数据训练。试验固定正文版本与取样，盲测时剥离大纲、作者解释和既有分数。
5. 比较方案至少观察：问题能否被原文支持、人工认可的修改价值、修订前后偏好、关键缺陷检出与误报、摘要遗漏率、运行时间与费用。用置信区间/分歧记录表达不确定性，避免小样本胜率包装成稳定能力。
6. 真实用户可查看正在评哪一部分、已覆盖多少、当前花费、已保存发现和可恢复点；已保存作品不因评议失败而不可阅读或交付。

## 复现实验前还需核对

- 核实checkpoint实际可获取、adapter/基座精确版本、许可与数据适用范围。
- 核对564个公开评论文件与600本元数据的对应关系；检查测试集合及评分分布，不静默丢弃缺失样本。
- 统一论文与训练脚本的超参数，固定摘要模型版本、抽样和随机种子。
- 对取样、摘要解析、评分解析、缺失输出建立显式报告；报告实际完成数。
- 单独测现代模型与本地小说的效果，不沿用2025论文模型排名或旧价钱、旧耗时作生产承诺。

## 可复核的本地研究材料

本次公开代码与论文临时快照：`/private/tmp/novelos-longstoryeval-audit-20261031/`。包含论文PDF及系统PDFKit提取文本、公开仓库目录元数据、被审阅的程序和提示词。未下载评论数据、训练数据、模型权重或小说全文。项目中仅新增本研究文档。
