# AI HOT Daily Digest

把 `aihot` skill 脚本化，并用 GitHub Actions 每天自动生成 AI HOT 中文简报。

默认运行时间：每天北京时间 08:30。

默认推送方式：在当前 GitHub 仓库创建一条 Issue，标题形如 `AI HOT · 2026-05-26`。你只要 Watch 这个仓库或打开 GitHub 通知，就能收到提醒。

## 文件结构

- `scripts/aihot_digest.py`：调用 AI HOT 公共 API，生成 Markdown 简报。
- `scripts/publish_github_issue.py`：把生成的简报发布成 GitHub Issue。
- `scripts/push_webhook.py`：可选，把简报推送到 webhook。
- `.github/workflows/aihot-daily.yml`：每天定时运行。

## 本地试跑

```bash
python scripts/aihot_digest.py --mode selected24h --hours 24 --take 50 --output digest.md
```

支持模式：

- `selected24h`：最近 24 小时精选，默认。
- `daily`：AI HOT 最新日报。

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
