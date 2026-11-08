# 52482a7186d0 分卷骨架 KeyError 排查

2026-11-07。仅诊断，未修改应用代码、任务数据或模型设置，未重启、重试或发起真实模型调用。

## 现场证据

任务 `52482a7186d04e3eab635764d3b4c26e` 为 `story-first.v1`，作者设定48章、每章800英文词、`multi_volume`。当前revision20，intent为deliver，status为error；最后失败北京时间12:59:56。13:27截图显示的是已保存失败状态。

revision17–20共4次交付尝试，每次都收到2次模型回复（初次回复后进行一次格式修订），合计8次。最后一轮12:57:56启动，66.262秒后收到3583字符；格式检查失败后修订，54.286秒后收到3754字符，再于response_parse阶段抛出KeyError。各轮均未完成全局故事设计检查点，尚未进入完整章节规划。

现有日志只记录request.completed、回复长度和异常类型；没有保存这些失败的全局设计原始回复，也没有记录KeyError的具体键名和原始堆栈。因此不能声称线上此次具体漏了target_words、structural_role或某个其他字段，也不能对未保存回复的文学内容作判断。

## 可复现缺陷

真实任务的内存副本经真实`_complete_impl`运行；仅将模型客户端替换为离线桩、保存回调替换为内存收集器，不触碰运行数据。结果：

- 返回完整合法分卷结构：全局设计通过并保存，进入chapter_plan；证明这个任务快照可以经过该阶段。
- 同一结构只删去第一卷structural_role：稳定抛出`KeyError: 'structural_role'`，页面错误字符串与截图一致。
- 单独在parse_stage移除target_words：也复现相同类型异常；不是特定剧情触发。
- 离线构建出的system SHA与实际第20轮第1次请求收据相同。分卷schema及structural_role等字段说明确实存在于实际请求中，不能归因于完全没有提供格式说明。
- 运行容器的4个相关模块SHA与离线代码相同。

最小复现命令（不调用模型）：

```sh
PYTHONDONTWRITEBYTECODE=1 venv/bin/python - <<'PY'
import json
from core.story_design import StoryDesign
from api.workshop_delivery import parse_stage
value = {
    'narrative_mode': 'multi_volume', 'total_chapters': 48,
    'story_design': {key: 'diagnostic placeholder' for key in StoryDesign.model_fields},
    'narrative_format': {'mode': 'multi_volume', 'total_chapters': 48, 'volumes': []},
}
parse_stage(json.dumps(value), 'story_design', None, story_first=True)
PY
```

已运行，exit1：`KeyError: 'target_words'`。正确行为应是带字段路径的受控合同错误。

## 错误链路

`api/workshop.py:669` → `api/workshop_delivery.py:148` → `core/story_design.py:99` → `core/narrative_format.py:301`。

NarrativeFormat/VolumeSpan/LongFormSuitability直接索引模型JSON的必填键，漏字段抛KeyError；parse_stage只接住ValueError。KeyError越过有限格式修订机制，被_failure统一标成“模型调用失败，请检查配置”。worker又把原异常替换成RuntimeError并舍弃原始堆栈。已确认这是系统解析与错误归类缺陷；由于原始回复未留存，具体线上缺失字段不能进一步确认。

现有story-first复杂结构测试覆盖“整个narrative_format缺失”，没有覆盖其为对象但内部缺字段的场景。正常短篇/单本长篇不走该分卷解析分支；分卷和系列模式均有同类风险，其他调用该解析器的入口也应审查。

## 后续修复建议（尚未执行）

1. 在结构边界统一校验完整字段与类型，提供准确JSON字段路径；保持章数、卷区间和内容校验，不以补默认剧情或绕过校验掩盖问题。
2. 保留模型每次阶段原始回复、哈希和脱敏字段错误，成功与失败均可追踪；区分服务连接、响应合同与系统内部故障。
3. 分离代码可确定的序列化字段与模型负责的卷边界/故事设计，减少模型填工程字段的负担。只做有界、定向修订，避免无差别重推完整故事。
4. 以真实多卷任务形状补缺字段、错误类型、格式修订与保存恢复回归，再部署。当前重复点击或只重启服务不能修复这个代码缺陷。
