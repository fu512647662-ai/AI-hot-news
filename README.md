# AI HOT Daily Digest

把 `aihot` skill 脚本化，并用 GitHub Actions 每天自动生成 AI HOT 中文简报。

默认运行时间：每天北京时间 08:30。

默认推送方式：在当前 GitHub 仓库创建一条 Issue，标题形如 `AI HOT · 2026-05-26`。你只要 Watch 这个仓库或打开 GitHub 通知，就能收到提醒。

## 文件结构

- `scripts/aihot_digest.py`：调用 AI HOT 公共 API，生成 Markdown 简报。
- `scripts/publish_github_issue.py`：把生成的简报发布成 GitHub Issue。
- `scripts/push_webhook.py`：可选，把简报推送到 webhook。
- `.github/workflows/aihot-daily.yml`：每天定时运行。
- `tests/test_aihot_digest.py`：离线回归测试，使用 Python 标准库，无需安装依赖。
- `.github/workflows/tests.yml`：在 Push / PR 时运行测试，不发布 Issue 或推送 Webhook。

## 本地试跑

```bash
python scripts/aihot_digest.py --mode selected24h --hours 24 --take 50 --output digest.md
```

支持模式：

- `selected24h`：最近 24 小时精选，默认。
- `daily`：AI HOT 最新日报。

## API v1 兼容性与验证

接口依据：[官方迁移指南](https://aihot.news/agent?tab=api#legacy-api-migration)、[OpenAPI](https://aihot.news/openapi-v1.json)。

- `selected24h` 调用 `https://aihot.news/api/v1/items`，使用 `mode=selected`、`by=published`、`window`、`limit`，需要时按 `page.nextCursor` 翻页。`--hours` 支持 `1–168`：不超过 24 小时使用 `24h`，其余使用 `7d`，再按原文 `publishedAt` 精确过滤，排除未来时间，排序去重后截取 `--take`（延续原来的 `1–100` 限幅）。
- 原文发布时间缺失、无时区或无效的条目不纳入严格时间窗，并在日志中提示；不使用 `discoveredAt` 冒充发布时间。未知分类合并为“其他”。
- `daily` 调用 `/api/v1/dailies/latest` 并读取顶层 `report`，保留本期日期、导语、栏目和快讯；不再对官方编排的日报应用滚动 24 小时筛选。
- 标题回退读取 `originalTitle`，来源读取 `source.name`，链接优先读取 `links.original`，缺失时使用 `links.aihot`。
- 请求支持 gzip。网络错误、429 和 5xx 最多尝试三次，使用指数退避并遵守 `Retry-After`；566/567 最多重试一次。服务器要求等待超过 60 秒时直接报错并保留等待信息，不提前重试。400/403/404 不重试；分页期间的 `invalid_cursor` 最多从第一页重新取一次。
- 不复用旧游标或 ETag，也不持久化跨次请求缓存。接口失败、结构异常或日报缺失时返回非零退出码，不生成假成功的空摘要；工作流会停止后续发布。有效的空资讯列表和空日报仍可正常生成。

运行离线测试（所有 HTTP 请求和推送均被 Mock）：

```bash
python -m unittest discover -s tests -v
```

仅生成文件，使用真实 API 验证两种模式（不会创建 Issue 或推送 Webhook）：

```bash
python scripts/aihot_digest.py --mode selected24h --hours 24 --take 50 --output digest.md
python scripts/aihot_digest.py --mode daily --output daily.md
```

合并修复后，在 GitHub 的 `Actions -> AI HOT Daily Digest -> Run workflow` 选择 `main` 发起新运行。确认 `Test digest`、`Generate digest`、`Upload digest artifact` 成功，下载 `aihot-digest` 检查分类、来源、原文链接及时间窗；启用 Issue 发布时确认当日 Issue 正文一致，配置 Webhook 时确认接收端收到消息。
另将 `AIHOT_MODE` 切换为 `daily` 手动验证一次，然后恢复原配置。最后确认下一次北京时间 08:30 的定时触发成功（GitHub 调度可能延迟）。不要只重跑旧失败记录：旧运行仍使用旧提交，应从含修复的新提交发起运行。

## 放到 GitHub 上自动运行

1. 新建一个 GitHub 仓库，例如 `aihot-daily-digest`。
2. 把本目录所有文件提交并推送到仓库。
3. 进入仓库的 `Actions` 页面，确认 workflow 已启用。
4. 进入仓库的 `Settings -> Actions -> General`，确保 `Workflow permissions` 允许 `Read and write permissions`，否则无法创建 Issue。
5. Watch 这个仓库，或打开 GitHub 通知。每天北京时间 08:30 会创建一条新 Issue。

## 可选配置

在仓库 `Settings -> Secrets and variables -> Actions -> Variables` 里可设置：

- `AIHOT_MODE`：`selected24h` 或 `daily`。
- `AIHOT_TAKE`：最多取多少条，默认 `50`。
- `AIHOT_HOURS`：`selected24h` 模式的时间窗口，默认 `24`。
- `AIHOT_CREATE_GITHUB_ISSUE`：设为 `false` 可关闭 GitHub Issue 推送。

## 可选 webhook 推送

在仓库 `Settings -> Secrets and variables -> Actions -> Secrets` 里设置：

- `AIHOT_WEBHOOK_URL`：webhook 地址。
- `AIHOT_WEBHOOK_TYPE`：`generic`、`dingtalk` 或 `serverchan`，默认 `generic`。
- `AIHOT_DINGTALK_SECRET`：钉钉机器人加签密钥。仅 `AIHOT_WEBHOOK_TYPE=dingtalk` 且机器人开启加签时需要。

`generic` 会发送：

```json
{
  "title": "AI HOT · 2026-05-26",
  "content": "Markdown text"
}
```

`dingtalk` 会按钉钉机器人 Markdown 消息格式发送。
如果配置了 `AIHOT_DINGTALK_SECRET`，脚本会自动按钉钉要求生成 `timestamp` 和 `sign`。

`serverchan` 会按 Server 酱 `title` / `desp` 表单格式发送。
