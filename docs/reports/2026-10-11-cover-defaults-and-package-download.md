# 四图默认与工作台交付包下载验证

日期：2026-10-11，Asia/Shanghai。承接 [封面框架交接](../design/2026-10-10-cover-studio-takeover.md)。

## 用户范围与结果

- 后续新批次默认四张；当前《He Chose His Hidden Family for Twenty Years…》保留三张，不补图。
- 用户指出的入口是封面工作台的“交付包”按钮。
- 代码、CLI、前端以及实际持久化配置已经默认四张，无需修改业务默认值。仓库封面 Skill 原先仅写 3–5，本次明确四张默认，并要求旧批准保留原计划数量、摘要和图片预算。
- ZIP 内容与接口均正确；实际问题是自动化接管了用户 Chrome 下载，将保存文件命名为无后缀 GUID。已恢复当前浏览器默认下载行为，原按钮连续两次下载正常。

## 下载问题的实证与修复

HTTP 检查：`GET /api/projects/<project-id>/deliverables/package` 返回 200、`application/zip`、`attachment; filename="book-package.zip"`，10,605,021 字节。ZIP 内 38 个条目 CRC 校验通过。

Chrome 下载记录中原先三个文件名为 GUID，实际位于 `playwright-artifacts-re88nn` 临时目录。读取文件证明它们都是同一有效 ZIP。完整浏览器复现脚本点击真实“交付包”按钮，检查 Chrome 下载记录的实际文件名，而非 Playwright 提供的建议文件名：

```text
before: suggestedFilename=book-package.zip
        actual filename=385521af-eb6b-41bf-b952-fbd2e67fecd2
        passed=false: Browser saved package without .zip
after:  actual filename=book-package.zip; passed=true
again:  actual filename=book-package (1).zip; passed=true
```

本机 Playwright Chromium 实现为接受下载的 context 设置 `Browser.setDownloadBehavior`，`behavior=allowAndName`；该模式按 download GUID 命名。MCP 再用 `suggestedFilename()` / `saveAs()` 另存一份正常名称，导致仅观察 MCP 输出无法发现用户下载记录里的错误。这是自动化验收遗留的浏览器状态。

已对当前默认 context 恢复 `behavior=default`，验收结束关闭下载事件接管（`eventsEnabled=false`）。正常文件现在位于：

- `/Users/max/Downloads/book-package.zip`
- `/Users/max/Downloads/book-package (1).zip`

两个文件、接口响应、MCP 另存文件及原正式包的 SHA-256 均为：

```text
bd1814a999336d069cd711392988bb2a966b7065637ea41e378fe206eb9515b1
```

没有修改产品下载接口、ZIP 生成器或全局 MCP 配置，也无需重新部署服务。新建或重连 Playwright context 仍可能再次接管；后续复用用户 Chrome 验收时必须恢复默认下载行为，并核对实际文件名。这项要求已写入 MEMORY.md。

## 回归与数据保护

`web/src/routes/CoverStudio.test.tsx` 新增两个参数化案例，覆盖项目与讨论工作台：默认配置为四张时，旧三图批准依然按三图生成；重新规划传四；新方向须重新批准；旧三图仍显示，旧批准对象不被改写。

- 默认设置、工作流、工作台后端相关测试：50 项通过（本轮子代理执行）。
- CoverStudio 测试文件：17 项通过，含新增两项（本轮子代理执行）。
- `npm run lint` 与 `tsc -b`：通过。
- 真实浏览器：原下载文件名断言先失败，恢复后连续两次通过；磁盘 ZIP CRC 和 SHA 均通过。
- 39 个旧交付文件与先前基线摘要一致，三张候选图片摘要未变；原选中集合保持。
- 原批准仍为 initial_image_calls=3、extra_image_attempts=0、batch_reserved=true。本次修复未调用图片生成或质量审核服务。

核对运行数据时发现集合已从早先交付的 revision=13 变为 revision=14，更新时间 13:34:24；A 的新审核为 `evaluation-67c2b530d76d4f49ae92d390aa6c50c6`。不推定该并行操作的发起者，不覆盖其结果。A/B 仍为 human_review_required，C 仍为 blocked，三图原图与成品均保留。早先审阅 ZIP 继续作为 revision=13 的历史交付，不静默改包。

本轮改动仅为测试、仓库 Skill 说明、项目记忆与诊断报告；未提交或推送 Git，未覆盖本机安装的 Skill。正式图书包仍包含原选中封面；新候选须明确选择后才更新正式图书包。

## 证据位置

本轮独立证据目录：目标作品的 `outputs/covers/delivery-followup-20261011/`，临时工作副本 `/private/tmp/novelos-package-fix-20261011/`。包含 HTTP 结果、浏览器修复前后文件摘要、真实点击复现脚本、下载记录快照、数据核验、lint/typecheck 日志和本轮文件摘要。前一轮 `delivery-20261010` 的交付证明和候选 ZIP 保留原样。
