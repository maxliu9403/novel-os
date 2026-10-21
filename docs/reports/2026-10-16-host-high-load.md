# 宿主机高负载：旧 Next.js 开发实例异常空转

2026-10-16 21:02–21:12 上海时间，只读现场诊断及有界合成实验。没有停止或重启真实服务，没有改业务代码或配置，没有调用模型。诊断材料在 `/private/tmp/novelos-load-diagnosis-20261016/`。

## 结论与置信边界

**直接耗 CPU 的原因已确认：三组旧 H5 项目的开发实例在未捕获异常和日志输出路径持续空转，九个高负载实例叠加。** 它们不是 Novel OS 当前前后端，后者是 Docker 中的 uvicorn 与 nginx。长期遗留、重复启动及未随临时目录/依赖清理回收，是明确的生命周期管理问题。

一个与现场高度吻合的具体机制也已复现：日志接收端断开，非 console 写入引发流错误，Next 的 uncaughtException 处理器又向同一流打印错误，反复产生 EPIPE、格式化和内存回收开销。但真实进程的错误 code/message 未获得，**不能把 EPIPE 或 package.json 被删除认定为已确证的第一触发器**。低负载实例也存在无对端输出 socket，断开本身不足以证明循环。

## CPU 与实例归属

`baseline.py` 在约 8.202 秒内做三次 ps 测量。18 个 next-server 的累计 CPU 时间增加 50.73 秒，区间平均约 **618% CPU，约6.18个核心**；其中九个高负载占617.87%，其余九个接近空闲。本机共10个逻辑/物理核心，这不是整机618%。ps瞬时平滑显示连续两次为560.1%和580.6%；与用户早先480%为不同时间窗，不相矛盾。

| 所属目录 | 高负载 PID | 区间 CPU 合计 | 监听端口 |
| --- | --- | ---: | --- |
| `/private/tmp/ai-novel-h5-meta-minimal-ui` | 70763、70764、80859、80860 | 265.78% | 4493、4492、4682、4582 |
| `/private/tmp/meta-event-reliability-20261008` | 58625、58626、74404、74405 | 257.48% | 4497、4496、4512、4513 |
| `~/.codex/worktrees/fix-zip-plan-version-validation/ai-novel-h5` | 80060 | 94.61% | 3197 |

相同临时项目的 admin/storefront 各有两套开发实例，占用不同端口。九条 `pnpm → next dev → next-server` 进程链的顶层 pnpm 均 PPID=1，生存5–9天；说明原始父进程已退出，不能仅据此判断任务所有者已不再需要服务。检查瞬间九个仅LISTEN，无已建立TCP连接；不能据一次连接快照保证今后无人使用。

两个临时项目的根与应用 package.json 已不存在；第二临时项目及修复 worktree 的 Next 依赖软链失效。当前进程仍可运行已加载代码，不代表项目仍能正常启动或热更新。

## 栈采样：不是普通编译热点

各采样2秒，间隔1毫秒。主线程记录全部包含 `node::errors::TriggerUncaughtException`，随后进入 `InspectorConsoleCall`、Error处理、内存分配和GC。

| PID | 主线程采样 | 处于未捕获异常路径 | 物理 footprint |
| --- | ---: | ---: | ---: |
| 80060 | 1362 | 1362 | 2.9G |
| 70764 | 1606 | 1606 | 2.2G |
| 58625 | 1585 | 1585 | 1.4G |

这些数量是栈采样，不是异常发生次数。原始材料为 `sample-*.txt`。没有向真实进程注入调试代码、启用 inspector 或读取大堆转储。

## 日志链路与有界复现

九个高负载实例 stdout/stderr 都是 unix socket，lsof 显示 `->(none)`，而不是持久日志文件。Next15.5.21源码路径：`next-dev-server.js:265` 的异常监听 → `logErrorWithOriginalStack:457` → `dev-bundler-service.js:22` → `setup-dev-bundler.js:832` → `build/output/log.js:80–102` → `console.error`。该监听路径只输出、不退出/熔断。`next-dev-server.js:127–128` 还存在 worker.stdout/stderr 直接 pipe 到 process 输出流的写入通道。

在相同本机 Node26.7.0、真实 Next logger 下，使用合成socket与异常，每例最多3秒/100次异常；没有端口、外网或业务数据，实验子进程均已退出。

| 对照条件 | 结果 |
| --- | --- |
| socket断开，仅throw普通异常；有/无Next环境补丁 | 各仅1次异常后正常结束 |
| socket断开，直接写stderr | 30ms内100次EPIPE，达到实验硬上限 |
| 同上，加载Next环境补丁 | 67ms内100次EPIPE，达到实验硬上限 |
| socket正常，直接写stderr | 0异常 |
| socket断开，但输出流单独监听error | 1次流错误，0次未捕获异常 |

这证明了条件性日志循环，并排除了“只要socket断开就必然循环”的过度推断。原异常既可能由输出流产生，也可能先来自依赖/目录变化或其他回调，现场没有初始日志可判定。详细源码SHA、脚本和结果见审计目录 `next-exception-chain.md`、`next-source-manifest.json`、`socket-log-probe*`。

## Docker 与内存

本轮成功读取Docker，不沿用之前权限受阻的结论。21:03:48与21:03:54两次采样：

| 范围 | 第一次 CPU | 第二次 CPU |
| --- | ---: | ---: |
| Novel OS 前端 | 0.02% | 0.01% |
| Novel OS 后端 | 0.19% | 25.03% |
| 其余10容器合计 | 3.73% | 3.52% |
| 全部12容器 | 3.94% | 28.56% |

后端仅docker-init、uvicorn，无Codex/Node/生成子进程；前端nginx。该采样不足以解释宿主九个Next进程数百%的持续占用，宿主虚拟机进程的开销也不能全部等同某个容器。另有浏览器/窗口系统的短时波动，未将全部机器负载归给Next。

32GiB物理内存，swap已用28272.81MiB（约27.61GiB）。约5.028秒的内存增量测量：swap-in约5.65MiB/s、swap-out0；压缩/解压处理页量约1842/1514MiB/s，压缩器物理占用约13.97GiB。压缩数字是内存页处理量，**不是磁盘吞吐**。换页与压缩会放大卡顿，但无法据这些系统总量将全部内存压力归因九个Next实例。

## 建议处理顺序

1. 再次确认PID启动时间、cwd、端口和所属任务，精确退出这九条废弃开发进程组。不要按名称杀所有node/next，也不要先重启Novel OS/Docker。当前报告没有执行退出。
2. 停止后复跑同一CPU/内存采样，验证负载下降，而不是仅看端口消失。
3. 开发服务绑定任务生命周期：保存pid/进程组、cwd、端口、日志路径；任务关闭回收子进程，删除worktree前先停止服务。每个项目/应用避免重复启动。
4. 需要跨会话保留的服务使用受管启动器和可轮转文件日志，不把临时工具socket当作永久输出目标。不可恢复输出流故障或异常风暴应有退出/熔断策略；只吞掉EPIPE会掩盖其他失败，不能视为完整修复。

Node官方同样建议不要在未捕获异常后盲目继续正常运行，应清理后退出，再由外部监控恢复。[Node.js进程文档](https://nodejs.org/api/process.html#warning-using-uncaughtexception-correctly)

以上为最初诊断记录。当时未停止服务；后续用户明确授权“请帮我处理掉吧”，清理结果如下。

## 21:15 清理完成与复测

执行前再次验证九个next-server的PID、启动时间、cwd、监听端口、父进程链以及进程组成员；每组恰好是已确认的pnpm、next dev、next-server三个进程。无已建立TCP连接，另九个Next实例单独记录保留。pnpm在项目根目录、子进程在app目录，两者分别核验。

2026-10-16 **21:15:45** 完成：向九个已验证进程组发送SIGTERM，**27个目标进程全部正常退出，SIGKILL使用0次**。没有删除工作树、项目文件或依赖，没有修改配置，没有停止其余九个Next实例。

清理后约8.155秒实测：

| 指标 | 诊断时 | 清理后 |
| --- | ---: | ---: |
| Next实例数 | 18 | 9，全部是原保留实例 |
| Next合计区间CPU | 618.47% | **1.10%** |
| 九个目标端口 | LISTEN | **全部释放** |
| 内存压缩器物理占用 | 13.97GiB | 5.72GiB |
| swap已用 | 27.61GiB | 22.61GiB |
| 内存页压缩速率 | 1842.18MiB/s | 0.00MiB/s |

两轮时间窗不同，数据用于清理前后观察，不视为严格基准性能测试。残留swap属于系统全局状态，不为清零而执行内存purge或重启；浏览器和macOS系统进程仍有独立波动，不宣称整机CPU为零。

21:16:52独立核验：Novel OS前后端容器完整ID与StartedAt和清理前相同，均running/healthy；localhost:5174/api/health返回200/status=ok。21:16:53容器CPU合计3.33%，Novel OS后端0.23%、前端0.00%。Docker和Novel OS均未重启。

清理脚本只有严格绑定本次现场的白名单，不是按名称批量结束进程；重复执行会被审计标记拒绝。证据：`cleanup-plan.json`、`cleanup-actions.json`、`cleanup-result.json`、`cleanup-measurements.json`、`cleanup-runtime-{before,after}.json`。本轮完成即时清理；跨H5任务统一生命周期管理与持久日志仍是后续预防性改进，未隐式修改其他项目。
