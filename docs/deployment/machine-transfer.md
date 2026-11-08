# 通用源码部署与换机

主路径继续使用 **`./deploy.sh up` 从源码构建**，不依赖旧电脑导出的镜像。
支持装有 Docker Compose 的 macOS、Linux、Windows WSL；不同 CPU 架构在目标机本地构建。

默认部署仅包含 Novel OS 后端与前端。Gemini 网关不是项目依赖，不纳入部署步骤、换机必带清单或验收前提；模型服务由使用者在设置页按需连接。

## 随代码交付什么

- `resources/novel-knowledge/`：源码当前有 **1 个 Genre、40 个 Tags**，包括页面中自定义的“冒险”。2026-11-08 频道功能只在原文末尾追加 `audience-guidance.v1` 模块，原文件字节及原 SHA 可核验；41份均有女频指导，其中12份同时有男频指导。源码种子与运行库的停用／删除状态分开，不能用这41项覆盖作者恢复的库。
- `resources/system-prompts/title-design.md`：当前启用的通用书名提示词原文。
- `skills/`：`novel-brainstorm-workshop`、`high-retention-web-novel`、`novel-cover-studio`、`novel-corpus-study`，以及各自引用的资料与脚本。Docker 会复制它们；`deploy.sh up/restart` 会同步四个项目 Skills 至宿主 Codex，并备份被替换版本。
- `agents/`、`templates/`、其他 `resources/`：各创作阶段提示词、产物合同、字体/排版相关资源及默认方法论。
- `requirements.lock`、`web/package-lock.json`、Dockerfile 中固定的 Codex CLI 版本：限制换机重建时依赖漂移。Python lock 来自当前验证过的运行环境；更新时需重新构建、验证。

默认 Markdown 用于全新安装。页面维护的**历史版本、草稿、启用版本号、停用与删除状态**另存于运行数据，不能仅靠复制 Markdown 保留。先前迁移检查点的资料库包为209个版本（含删除项历史），这是该备份的数量，不是以后每次迁移都应凑齐的固定指标；当前数量和摘要以最新导出清单为准。

2026-11-08 18:33（UTC+08:00），最终版本 `audience-author-recovery-20261108` 已部署至5174，前后端均 healthy；运行资料库已完成追加升级，频道、资料维护、创作依据和历史讨论恢复的浏览器复验通过。仅重启Novel OS前后端，网关未改动，未调用真实模型或恢复业务任务。详细发布证据见[实施记录](../reports/2026-11-08-audience-author-workflow.md)。先前已经生成的 Git bundle／迁移分支不会自动包含之后的工作区改动；正式换机前需将最终源码及升级后的资料库重新导出，并核对包含 `core/audience_guidance.py`、`scripts/upgrade_audience_guidance.py` 及追加后的 Markdown。不要仅因分支名称相同就假定是最新代码。

## 新电脑部署步骤

安装 Git、Docker Desktop/Engine + Compose、Python 3，以及需要使用的 Codex App/CLI。启动 Docker。

1. 正式迁移最新功能，先取得重新导出的源码分支或私有 Git bundle，并核对提交与资料清单。以下旧迁移分支命令**仅用于恢复历史检查点**，不能代替最新源码导出：

   ```bash
   git clone --branch codex/migration-baseline-20261108 /你的交付目录/novel-os.bundle Novel-OS
   cd Novel-OS
   ```

2. 如需保留知识库全部历史版本，**在首次启动服务之前**恢复资料包：

   ```bash
   python3 scripts/portable_data.py restore /你的交付目录/library-history.tar.gz ./docker-data
   ```

   恢复前完整校验 SHA-256。目标目录必须不存在，防止覆盖已有数据。如果选择恢复完整 `docker-data`，不要再叠加恢复这个资料包：完整备份已经包含它。

3. 创建 `.env`，根据 `.env.example` 配置本地端口和连接。完整数据备份已包含 `studio_settings.json` 与 `studio_settings.secrets.json`；仅恢复资料库历史时，需要在设置页重新配置模型连接。单独的 `private-config.tar.gz` 仅保存 Novel OS 的 `.env` 和 Codex 配置/登录文件，不包含这两份模型设置文件。这些私有文件不提交 Git。

4. 当前 Compose 挂载 Codex 登录文件。运行 `codex login`，确认 `~/.codex/auth.json` 存在；没有自定义配置时可创建空 `~/.codex/config.toml`。也可通过 `.env` 的 `NOVEL_OS_CODEX_AUTH_FILE`、`NOVEL_OS_CODEX_CONFIG_FILE` 指向迁移的私有文件。登录状态可能失效，失效时重新登录。

5. 若恢复的是频道升级前的资料库，先按下节在恢复副本预检、备份并执行资料升级；使用已安装项目依赖的 Python，或用本次新版源码构建的容器执行。不要用旧运行容器里的迁移脚本。

6. 启动源码部署：

   ```bash
   ./deploy.sh up
   ./deploy.sh status
   ```

   打开 `http://localhost:5174`。部署不会自动启动新的小说创作。

## 已有资料库的频道指导升级

升级工具只追加频道指导并保存新版本，保留原文件正文和全部历史，不把种子库强行同步成运行库。源码更新、历史包恢复、资料升级是三个独立步骤。已删除／停用项目不恢复，已有有效频道模块不覆盖，作者新改的原文或未发布草稿不替换。

先在恢复的数据副本上只读预检。以下 `docker-data` 是数据根目录，工具实际访问 `creative_types/default`；其他工作区用 `--workspace` 指定：

```bash
venv/bin/python scripts/upgrade_audience_guidance.py \
  --data-dir ./docker-data --workspace default > /private/tmp/audience-upgrade-plan.json
```

不带 `--apply` 的命令只输出 JSON 计划，不激活版本、不调用模型。首次部署应使用已安装 `requirements.lock` 的 Python；目录路径需替换为本机实际位置。无需为了预检启动小说任务。

确认备份完整、没有资料编辑并核对计划后，显式执行：

```bash
venv/bin/python scripts/upgrade_audience_guidance.py \
  --data-dir ./docker-data --workspace default --apply > /private/tmp/audience-upgrade-result.json
```

也可在**已构建新版源码镜像**的环境运行一次性命令，不启动依赖：

```bash
docker compose run --rm --no-deps -T backend python scripts/upgrade_audience_guidance.py \
  --data-dir /data --workspace default > /private/tmp/audience-upgrade-plan.json
# 核对后才在同一命令末尾添加 --apply。
```

报告 `schema=novel-os.audience-upgrade.v1`，包含模式、每条ID／版本／原文和目标 SHA、数量及原因。退出码0表示没有冲突，2表示冲突或整体预检失败；不能只看已更新数量。执行模式逐条应用合格项，若另有冲突，结果可能为部分成功，完整记录仍在报告中。重复执行已合格项为 `already_guided`，不重复追加版本。

| 报告原因 | 操作 |
|---|---|
| `append_guidance` / `updated` | 原文逐字匹配种子原文，追加模块并激活新版本；旧版本保留 |
| `deleted` / `not_enabled` | 保留删除／停用状态，不恢复 |
| `already_guided` | 保留现有频道指导，包括用户自行修改的指导 |
| `no_matching_seed` | 自定义资料不猜测方法，稍后在维护页明确补充 |
| `unpublished_draft` | 存在未发布草稿，先由作者处理，不覆盖草稿 |
| `original_changed` | 当前原文与种子原文不同，需按当前内容审阅补指导，不覆盖作者修改 |
| `invalid_existing_module` / `mutation_rejected` | 检查模块或修订并发冲突；已保存记录保留，解决后重新预检 |

2026-11-08 已完成实际运行库升级：39项追加更新、3项删除跳过、0冲突，全部旧版本及升级前活动原文精确保留。其中 `tag.face_slapping`、`tag.fast_paced_plot` 在源码种子中但已由用户删除；`tag.runaway_with_a_baby` 已删除且不在种子中。这三项未被恢复。升级后的运行库为39项女频可用、11项男频可用；全新安装的源码默认库则是41／12，差异是有意保留的作者状态。恢复升级后的新备份时，工具会对已有模块返回 `already_guided`；恢复旧备份时仍须先预检，不能照搬本次数量强行迁移。

升级后重新导出资料库清单，比较每份模块的 `original_sha256` / `original_bytes`，核对原文、旧版本、草稿和删除状态；不要把启用版本号增加误认为旧历史丢失。新讨论读取新版本，已有讨论与小说仍使用原冻结快照，不因库升级改写其故事或执行锁。若某份资料没有完整频道指导，会在新选择中提示不可用，旧冻结作品仍可恢复。

回滚应恢复发布前的数据备份与匹配源码，或在维护页明确采用历史版本；不能删除版本JSON来伪造回滚。发布前还有活动任务时先安排窗口，资料迁移本身不构成重启服务或恢复付费任务的授权。

## 模型连接按需配置

应用启动后，在设置页选择所需模型服务，配置访问地址、模型与凭证。可使用 Codex、Gemini API 或受支持的 OpenAI 兼容连接；这些是模型连接方式，不是额外的必装容器。

恢复的模型设置可能仍指向旧电脑上的服务地址。正式创作前，应改为新电脑可访问的地址，或选择其他可用连接，再获取模型列表并进行可用性测试。外部服务的安装、账号、数据库和运维由该服务独立管理，Novel OS 不负责部署或搬运。

已有模型适配器和长输入能力协商保留；请求容量按实际选中连接校验，不因省略某个网关而静默截断输入或承诺相同上下文容量。默认验收不要求安装、启动或迁移 Gemini 网关。

## 作品与任务记录

完整数据都在默认 `docker-data/`：作品、章节、封面、参考小说、讨论历史、版本快照、费用及数据库。只搬源码和资料库包不会带走作品。

本次任务已自行失败停止，确认没有活动任务后，已生成完整检查点备份（2026-11-08 16:03:02 +08:00，33,919 个文件）。没有暂停或重启服务。之后若继续创作，正式切换电脑前等任务结束或由作者暂停，再刷新备份：

```bash
python3 scripts/portable_data.py snapshot docker-data /你的私有备份目录/novel-os-data.tar.gz
python3 scripts/portable_data.py verify /你的私有备份目录/novel-os-data.tar.gz
```

在新电脑首次启动前恢复：

```bash
python3 scripts/portable_data.py restore /你的私有备份目录/novel-os-data.tar.gz ./docker-data
```

快照期间若任何源文件变化，工具拒绝生成备份；不会为了“成功”忽略写入。SQLite 使用备份 API，包含已提交 WAL 内容。恢复只允许不存在的目标目录，拒绝链接、越界路径、重复文件和哈希不符。正在等待的模型请求不能迁移；恢复已保存进度后由用户手动继续，可能重新产生该次模型用量。

## 以后页面修改了 Genre / Tags

源码默认资料不会自动随页面编辑变化。换机前导出实际资料库：

```bash
# 宿主已安装项目依赖时
venv/bin/python scripts/export_creation_library.py --data-dir docker-data --output /你的私有目录/creation-library

# 或在新版后端容器内运行（结果在宿主 docker-data/exports 下）
docker compose exec -T backend python scripts/export_creation_library.py \
  --data-dir /data --output /data/exports/creation-library-new
```

输出 `resources/` 中是逐字保留的当前启用 Markdown；`data/` 包含完整资料库历史和书名提示词版本；`manifest.json` 包含逐文件 SHA。工具只读当前资料，不调用模型，不激活版本。若要作为下一次源码默认值发布，应明确审阅后把导出的 `resources/` 纳入 Git；不要把运行数据和密钥提交仓库。

## 验收标准

1. 页面/API 正常；资料数量与所恢复的工作区清单一致，不固定强制41项。频道迁移按计划核对支持范围，Markdown 原文收据与交付清单一致。
2. 恢复历史包后，历史版本、草稿、删除状态及书名提示词与该包清单一致；若执行频道升级，只增加明确记录的新版本，不改旧版本。先前209版检查点只能用于核对那一份旧备份。
3. 四个项目 Skills 完整；其他个人通用 Skills 不是应用运行依赖。
4. 所选模型服务的地址、凭证、路由与计价配置正确，并通过连接可用性检查；无需部署特定网关。
5. 如果恢复完整数据，检查作品、来源和讨论数量，打开正文、骨架、封面再手动恢复任务。

源码、提示词、配置和保存的内容可以校验一致；生成式模型有随机性，不承诺逐字输出相同小说。私有备份含作品和密钥，勿上传公开仓库。本次没有推送远端。
