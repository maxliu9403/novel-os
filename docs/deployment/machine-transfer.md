# 通用源码部署与换机

主路径继续使用 **`./deploy.sh up` 从源码构建**，不依赖旧电脑导出的镜像。
支持装有 Docker Compose 的 macOS、Linux、Windows WSL；不同 CPU 架构在目标机本地构建。

## 随代码交付什么

- `resources/novel-knowledge/`：本次已将实际启用的 **1 个 Genre、41 个 Tags** 原文同步为默认 Markdown，包括页面中自定义的“冒险”。未改写用户内容。
- `resources/system-prompts/title-design.md`：当前启用的通用书名提示词原文。
- `skills/`：`novel-brainstorm-workshop`、`high-retention-web-novel`、`novel-cover-studio`、`novel-corpus-study`，以及各自引用的资料与脚本。Docker 会复制它们；`deploy.sh up/restart` 会同步四个项目 Skills 至宿主 Codex，并备份被替换版本。
- `agents/`、`templates/`、其他 `resources/`：各创作阶段提示词、产物合同、字体/排版相关资源及默认方法论。
- `requirements.lock`、`web/package-lock.json`、Dockerfile 中固定的 Codex CLI 版本：限制换机重建时依赖漂移。Python lock 来自当前验证过的运行环境；更新时需重新构建、验证。

默认 Markdown 保证新安装使用当前写作说明。页面维护的**历史版本、草稿、启用版本号**另存于运行数据，不能仅靠复制 Markdown 保留。当前知识库累计 **206 个版本**，已单独导出资料库历史包。

## 新电脑部署步骤

安装 Git、Docker Desktop/Engine + Compose、Python 3，以及需要使用的 Codex App/CLI。启动 Docker。

1. 使用本次源码分支或私有 Git bundle 创建目录：

   ```bash
   git clone --branch codex/migration-baseline-20261108 /你的交付目录/novel-os.bundle Novel-OS
   cd Novel-OS
   ```

2. 如需保留知识库全部历史版本，**在首次启动服务之前**恢复资料包：

   ```bash
   python3 scripts/portable_data.py restore /你的交付目录/library-history.tar.gz ./docker-data
   ```

   恢复前完整校验 SHA-256。目标目录必须不存在，防止覆盖已有数据。如果选择恢复完整 `docker-data`，不要再叠加恢复这个资料包：完整备份已经包含它。

3. 创建 `.env`，根据 `.env.example` 配置本地端口和连接。若迁移已有模型设置，使用私有配置备份中的 `studio_settings.json` 与 `studio_settings.secrets.json`，放入 `docker-data/`。它们不提交 Git。

4. 当前 Compose 挂载 Codex 登录文件。运行 `codex login`，确认 `~/.codex/auth.json` 存在；没有自定义配置时可创建空 `~/.codex/config.toml`。也可通过 `.env` 的 `NOVEL_OS_CODEX_AUTH_FILE`、`NOVEL_OS_CODEX_CONFIG_FILE` 指向迁移的私有文件。登录状态可能失效，失效时重新登录。

5. 启动源码部署：

   ```bash
   ./deploy.sh up
   ./deploy.sh status
   ```

   打开 `http://localhost:5174`。部署不会自动启动新的小说创作。

## Gemini 本地网关必须同步

当前网关位于独立仓库 `gemini-web2api-go`，包含本地 `bounded-attachment-v1` 扩容代码。只搬 Novel OS 或改用上游 `latest` 网关，可能退回旧输入限制。

交付中的 `gateway-source.tar.gz` 保存当前工作源码，包含 `docker-compose.override.yml`，会从源码构建，不要求搬镜像：

```bash
python3 Novel-OS/scripts/portable_data.py restore /你的交付目录/gateway-source.tar.gz ./gemini-web2api-go
cd gemini-web2api-go
# 按网关文档配置 .env 中的 ADMIN_TOKEN，在管理页登录账号/配置 API Key。
docker compose up -d --build
```

Novel OS 容器连接本机网关使用 `http://host.docker.internal:8083/v1`。
如需保留网关账号、Cookie、运行参数，恢复单独的 `gateway-data.tar.gz`；该文件含敏感数据。
它采用 SQLite backup API，不能用随手复制一个正在写入的 `.db` 文件替代。

恢复网关数据时，先启动过的网关必须停止，且目标是空的新卷。下面只对**新电脑**执行：

```bash
# 解出数据库至临时目录（目标必须不存在）
python3 ../Novel-OS/scripts/portable_data.py restore /你的交付目录/gateway-data.tar.gz ./gateway-restore
# 用 Compose 创建容器和卷，不启动网关；查出实际卷名
docker compose create gemini-web2api
GW_VOLUME="$(docker inspect gemini-web2api --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')"
test -n "$GW_VOLUME"
docker run --rm --network none --user 0 \
  --mount "type=volume,source=$GW_VOLUME,target=/target" \
  --mount "type=bind,source=$PWD/gateway-restore,target=/source,readonly" \
  --entrypoint python novel-os-backend:latest -c '
from pathlib import Path
import os, shutil
dst = Path("/target")
assert not list(dst.iterdir()), "目标卷不是空卷，拒绝覆盖"
shutil.copytree("/source", dst, dirs_exist_ok=True)
for path in [dst, *dst.rglob("*")]:
    os.chown(path, 65532, 65532)
'
docker compose up -d
```

不要对含现有数据的卷执行覆盖。账号恢复后核对网关设置与连接状态；重新登录是更简单的可选路径。

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

1. 页面/API 正常；Genre 1 个、Tags 41 个，当前 Markdown SHA 与交付清单一致。
2. 恢复历史包后版本合计 206，启用版本号、草稿、书名提示词版本一致。
3. 四个项目 Skills 完整；其他个人通用 Skills 不是应用运行依赖。
4. 模型路由、计价和 Token 配置正确；网关是包含本地扩容的源码版本。
5. 如果恢复完整数据，检查作品、来源和讨论数量，打开正文、骨架、封面再手动恢复任务。

源码、提示词、配置和保存的内容可以校验一致；生成式模型有随机性，不承诺逐字输出相同小说。私有备份含作品和密钥，勿上传公开仓库。本次没有推送远端。
