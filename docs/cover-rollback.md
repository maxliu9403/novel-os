# 封面代码回退：只切换封面，不回退整库或小说数据

## 本次可用回退点

自然度优化前的 v7 / compiler v12 与优化后的 v8 / compiler v13 已保存在本地：

```text
.cover-checkpoints/cover-20260908T050053Z-76f1e033.zip
```

SHA-256：`bc9d64df01db8d2ab9e542006be6924a9d18c5af3a338d1da61d3ebf27330d1f`。

本次基线来自修改前的本地源文件，不是未经验证的 Git HEAD，也不是 Docker 镜像备份。
临时副本切回 v12 后，最新封面集的四份提示词均与实际保存的生图提示词逐字一致。
再切回 v13 的往返验证通过。[验证记录](diagnostics/2026-09-08-cover-naturalness-rollback.json)。

仅保存/切换以下六个封面专属源文件：

- `core/cover_director.py`
- `core/cover_prompt_compiler.py`
- `core/cover_render_policy.py`
- `core/cover_validator.py`
- `core/cover_quality.py`
- `api/cover_service.py`

正文、数据库、封面原图、ZIP、分类、凭据、模型配置、H5 接口等均不写入或恢复。
共享接口文件只保存哈希用于兼容性检查，不在回退覆盖列表中。

## 查看、预演、恢复

在仓库根目录执行：

```bash
CP='.cover-checkpoints/cover-20260908T050053Z-76f1e033.zip'

# 查看源代码状态及校验结果；不需要 Docker
./deploy.sh cover-rollback status "$CP"

# 预演退回本次自然度优化前的代码，不写文件
./deploy.sh cover-rollback restore "$CP" --to before

# 确认需要回退后才执行；仅恢复源文件，不部署、不重启
./deploy.sh cover-rollback restore "$CP" --to before --apply

# 恢复本次自然度优化版，同样先去掉 --apply 可预演
./deploy.sh cover-rollback restore "$CP" --to after --apply
```

`before`、`after` 指该回退包保存的两个版本，不是任意时间的“最新版”。
`active_source` 只反映工作区源文件，不证明运行中的容器已加载该版本。

- 源文件有后来新增的修改，或者共享契约文件已变化：停止恢复，不覆盖修改。
- 包内文件、目录清单和 SHA-256 校验异常：停止恢复。
- 源路径是符号链接：停止恢复。
- 恢复有排他锁、逐文件原子写入、写后验证；普通写入失败会尽力恢复本次修改前的文件。
- 进程被强制终止时可能留下 `restore.lock`。先确认没有恢复进程，再处理过期锁。
  若文件处于两份已知快照的混合状态，可再次预演并完成指定方向；未知修改仍会阻止操作。

操作前应暂停其他人对这六个文件的编辑。原生 Python 服务需先停止相关任务，避免代码切换
过程中重新导入模块。此工具不是多文件文件系统事务，也不替代整机/数据库备份。

## 部署是单独操作

Docker 后端从构建镜像加载代码。源码恢复后，已有容器仍运行原镜像。
待小说和图片任务结束或进入已确认可恢复状态，再由操作人员显式重建部署。
不要为了切换封面而清空 `docker-data`、执行整库 reset，或用旧镜像覆盖其他框架修复。
只恢复本文六个文件，再用当前其他源代码构建，保留其他修复。

部署后重新规划并确认封面方向，确保方向版本与当前策略一致。
旧图、旧方向、选中关系和生成记录不会被代码回退删除，也不会自动重新生图或改选封面。
已有封面若需恢复为出版封面，应在 Cover Studio 的历史版本中明确选择，和代码回退分开处理。

## 后续测试前创建新的回退点

当“运行中的后端”是要保留的版本，“本地源码”是待测试版本时：

```bash
./deploy.sh cover-rollback capture
```

只读运行中容器的六个 `/app` 源文件及容器/镜像 ID，和本地六个源文件一起保存。
不读取认证文件，不调用模型，不重启服务。需要访问本地 Docker 守护进程。
工具输出完整路径、哈希和前后版本。捕获当前运行代码并不保证该版本效果好，需自行确认基线。

`.cover-checkpoints/` 被 Git 忽略；如需在其他电脑使用，单独安全复制回退 ZIP 并核对 SHA。
它是源代码快照，不是小说导入包，不要上传到 H5。
