# 美术方向运行状态可见性修复

2026-10-11。用户 20:03 截图中只有生成按钮旁的「美术方向正在规划」，无法确认任务是否仍在进行。

## 实际任务与原因

Secret Daughter 的 `direction-task-afabbf9b638149a0b6459882907b2ecc` 于 19:53:08 创建；job 为 `8fe5d334cdbd42b684a3976c8aa4ed55`。只读运行记录确认第一轮调用 20:01:47 完成、第二轮 20:06:22 完成，随后第三轮开始。截至 20:11 浏览器验收，该任务仍为 running，无 error。现有调用元数据只有 cover 阶段，不能从这些记录准确区分审核或修订，也不能凭 running 承诺完成时间。

任务不是因浏览器切页停止：旧页面把完整状态条放在较长的骨架面板之后；有旧方向时顶部也不显示创建按钮中的「规划中」。任务记录 updated_at 只在开始／结束写入，不能将它当作模型心跳或最新内容产出时间。

## 改动

- 状态条移到页头之后、骨架之前；有旧方向时仍然可见。延续现有出版工作室配色、间距和字形。
- 区分提交、排队、运行、同步、完成、失败和中断。活动标记尊重 reduced-motion。
- 真实计时从 created_at 计算，说明包含排队等待；终态按 finished_at 冻结，缺失／无效时间不伪造时长。
- hook 返回最近成功确认任务状态的浏览器时间 lastSyncedAt。失败保留上次确认，跨作品清空；这个时间不是模型进度。
- 连接错误或超过 15 秒未收到状态响应时，显示上次确认状态和「当前运行情况待确认」。挂起请求不声称已经自动重试；查询失败仍按原轮询机制恢复。
- 已确认 completed 但方向列表读取失败时保留完成事实，继续锁住审批／生成直到结果同步完成，不回退到旧 running。
- 计时与同步时间在 aria-live=off 区域，避免每秒播报；状态变化保留 polite 提示。

没有新增模型调用、自动重试规划、百分比或推测阶段，没有改后端任务执行逻辑。

## 验证与部署

复现先有 7 项页面失败与 1 项挂起响应失败；hook 的同步时间／跨作品隔离两项及完成后列表失败一项也先失败，再修复。最终前端 **286 passed**；完整 ESLint、TypeScript／Vite 生产构建、独立审查、git diff --check 通过。后端未修改，不重复运行上一轮已通过的后端全量。

生产构建使用 `VITE_API_BASE='' npm run build`。仅 `docker compose up -d --no-build --no-deps --force-recreate --wait frontend` 更新前端；前后对比 backend 容器 ID、镜像、StartedAt、RestartCount、PID 和健康状态完全相同，任务 ID 与 created_at 保持。允许原任务正常推进，未取消或重启它。

前端镜像 `sha256:774edd37d38c5cd995f350ed7f2771bdac4bf017c8d27d108fa01cbfd04d501b`；回滚标签 `novel-os-frontend:pre-direction-status-20261011`。后端仍为上轮构图框架镜像。

实际浏览器隔离 context 验收：顶部首屏可见、离开后返回、刷新恢复、390px 无横向溢出、减少动画生效；无脚本错误、失败 HTTP 响应或写请求。QA context 关闭，Chrome 下载行为恢复 default。740 个原有候选／评估／交付／来源文件摘要保持。测试未新增模型调用；用户原任务自身的后续调用继续正常执行。

审计：`/private/tmp/novelos-direction-status-20261011/`。截图：`.playwright-mcp/direction-status-20261011/desktop.png`、`mobile.png`。未提交／推送共享工作区的改动。
