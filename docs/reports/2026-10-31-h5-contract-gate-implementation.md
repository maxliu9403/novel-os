# M0 首批开发：H5 交付合同门

日期：2026-10-31。开发分支：`codex/novel-quality-optimization`，起点 `4a41e55`。

已完成本地工程实现与目标解析器契约验证，未部署、未推送远端，也未启动真实模型或改动用户小说。总体质量改造的 M1–M5 尚未完成，不能把本次交付理解为小说质量已经提升。M0 的真实创作样本基线、实验预算以及 H5 业务数据库验收仍待后续进行。

## 已实现

- `core/h5_contract.py` 固定 H5 profile、字典指纹及目标校验器版本；检查 ZIP/EPUB 真实字节、解压量、条目、路径、压缩方式、CRC、JSON/XML 深度、图片解码与尺寸。当前封面只准 PNG/JPEG。
- `h5_acceptance` / `h5_import` 对最终 ZIP 反读，强制 Manifest V4 / v1 / ready / 唯一 EPUB；校验全量章节和 Introduction、内外分类/分卷、候选关系、作者和各层摘要。新交付不能使用缺正文的 metadata_only 或无章节映射的旧 EPUB。
- `delivery_package` 冻结来源，在私有临时目录组装，校验后重新检查来源，最后原子替换 ZIP。失败保留原 ZIP，不把失败包标记为可下载。回执保存在项目内部，绑定来源、实际 ZIP SHA、profile、字典和校验器版本。
- `compiled_source` 将 EPUB 正文及 Introduction 绑定到已采用正文版本、出版文案和元数据；修改来源后仅重打 ZIP 不能掩盖过期正文。新编译始终产出 EPUB，已知章数超限在请求模型之前失败，最终按真实 spine 计数。
- 旧作品首次显式重建时，可在本地重新渲染已采用正文，并与旧 EPUB 逐章及 Introduction 比较字节。匹配才建立来源记录；canonical Final 缺失时不能回退到旧投影、修订稿或草稿。不匹配时要求重新编译，不篡改旧记录或小说。
- 选中海报保留在 EPUB manifest/guide 中，不插入不受 H5 支持的 cover/title spine 页。重复嵌入不改变正文 XHTML。
- 封面选择和 ZIP 同步分别持久化。没有正文 EPUB 时仍保留封面选择和图片，交付状态提示先完成正文编译；之后可以重建。
- 列表核对回执及来源/包摘要并缓存，不解码候选图片；下载对实际 ZIP 进行完整合同检查。GET 不生成、不迁移、不重打包。未验证、来源变化或被改坏的包不作为当前包下载。

## 验证证据

目标 H5 源码固定为 `ea4e76903789d5885a39a9e07c9772db50a9f732`，9 个生产源码/字典文件 SHA 保存在 `resources/h5-target/validator.lock.json`。校验时先验证源码文件清单与各 SHA，再复制到临时目录编译真实 Go `zipimport.Parse`；不修改 H5 仓库，不联网、不连接数据库。

- 真实 Go 解析器的 **19 个用例**均符合预期：新建、重复、正文/前言/分类/卷名更新、旧书封面更新、坏包，以及 512 正文章、511 正文章加 Introduction、新版海报嵌入。技术正文与图片均为测试夹具。
- 边界测试覆盖等界/越界、损坏图片、JSON/XML、路径/压缩方式/CRC、章节映射、错误 MIME、封面版本号、来源变化、旧稿来源补建、下载竞态及隐私文件排除。
- 一次完整后端回归：**4,049 通过，1 跳过，1 失败**（4,051 项）。跳过项是未设置源码位置的真实 Go 契约测试，它已在上述独立运行中通过。
- 失败项为 `test_book_author.py::test_author_failure_retains_pipeline_retry_policy[cause0-True]`：测试要求作者生成超时自动重试，现有执行停止策略返回不可重试。在修改前 `4a41e55` 的独立临时副本也复现同一失败。本轮没有修改作者生成和重试语义。
- 完整回归后补充的旧稿来源核验、元数据快照、压缩流校验和发布失败恢复，对受影响范围再次验证：**344 项通过**（343 项联跑与 1 项最终发布失败专项），包含真实 Go 契约、编译流水线、封面 API/服务及交付。
- `git diff --check` 通过。未改变前端视觉和交互，无真实文学效果或用户留存实验。

绑定源码和样包 SHA 的[目标解析器回执](2026-10-31-h5-target-contract-evidence.json)已保存。

本机详细日志在 `/private/tmp/novelos-m0-full-final.log`、`novelos-m0-baseline-test.log`、`novelos-m0-complete-regressions.log`、`novelos-m0-cover-final.log`、`novelos-m0-release-tests.log`；XML 回执同目录。临时日志不提交到仓库。

## 可复现命令与 CI

```bash
H5_ZIPIMPORT_SOURCE=/path/to/h5/apps/api/internal/studio/zipimport \
  python -m pytest tests/test_h5_target_contract.py -v
python -m pytest tests/test_h5_contract_gate.py tests/test_delivery_package.py \
  tests/test_h5_import.py tests/test_h5_acceptance.py -v
```

`.github/workflows/h5-contract.yml` 提供手动触发的 CI：输入获授权 H5 仓库位置，按锁定提交 checkout，再运行相同契约测试；私有源需配置只读 `H5_CONTRACT_READ_TOKEN`。工作流和源码指纹已提交为代码，但本轮未推送或执行远端 CI，不能宣称 CI 已绿。没有授权源码 checkout 的普通 pytest 会明确跳过目标解析器测试。

源码增加文件或任何被锁文件变化均拒绝继续，必须先审查目标变化，再更新 profile/pin 和对应测试；不能只改版本字符串。

## 使用与限制

部署后，已有 ZIP 在没有新校验回执时会显示需重新生成。用户显式重建是本地操作；正文与 EPUB 一致的旧作品可通过上述来源核验直接重建，不重新推理。旧 EPUB 没有显式映射、与已采用正文不一致、或资源超限时会保留旧文件并报告问题，需要重新编译或调整交付内容。

ZIP 是原子替换的发布点；外围 metadata 投影先写入，期间状态为 pending，失败为 failed，下载不会把它们作为成功的新包。没有宣称整个目录是一个跨文件系统事务。历史 ZIP 原文件保留，但尚无新增“历史版本下载”产品入口。

H5 Parse 通过仅证明格式契约；真实数据库中的书章更新、阅读进度、已解锁权益仍需测试环境验收回执。业务验收保持 pending。

下一批进入 M1：题材资料、本书具体机制与主动讨论决定记录；随后 M2 贯通骨架、章节任务和正文上下文。文学评议仍须按方案旁路、受预算限制并经真实阅读校准，不借本次工程测试宣称文学效果。
