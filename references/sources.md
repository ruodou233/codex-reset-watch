# 数据源与降级

## 默认公共来源

### Codex Resets

- Endpoint: `https://codex-resets.com/api/v1/status`
- 使用字段：`meta.generated_at`、`data.active_watch`、`data.latest_reset` 及其原始证据 URL。
- 定位：第三方聚合的 Tibo 动态、active watch 和重置历史，不是 OpenAI 官方接口。
- `reset_chance_percent` 是 AI 分类得到的启发式评分，不视为统计校准概率。

只保留这一条公共来源，是因为旧来源在 2026-08-29 的现场核验中分别出现持续漏报和上游停摆。来源数量不等于证据数量；多个网站转载同一条 Tibo 帖子不会增加独立信息。

## 事件选择与时间语义

- 手动输入的概率优先，并标记为 `manual-probability`。
- 公共事件分来自一个可用信号，不做多站点平均、求和或投票。
- API 健康度由 `meta.generated_at` 判断：超过两小时或时间无效时不采信。
- active watch 的有效性由 `observed_at` 与 `expires_at` 判断；在 `observed_at <= now < expires_at` 内使用其启发式评分。原帖较早不代表预测窗口已经失效。
- 新鲜的 regular `latest_reset` 只在两小时内作为公共确认事件；若它早于当前 active watch，不覆盖新的预测。banked reset 继续由本机账户数据单独展示，不当作即时全量重置。
- active watch 已过期、字段无效或 API 失败时，公共信号记为缺失，不把失败解释为“不会重置”。

## 本机 Codex 数据

脚本优先启动本机 `codex app-server`，调用：

```text
account/rateLimits/read
```

读取 `usedPercent`、`windowDurationMins`、`resetsAt` 与 reset-credit 摘要。只接受接近 10080 分钟的窗口作为周额度；没有匹配窗口时要求手动输入，不把最长窗口猜成周额度。

失败时不读取聊天、prompt 或认证文件，也不把历史 rollout 快照冒充实时数据；改为要求用户手动输入周额度和重置时间。

公共来源的“确认”不代表用户个人到账。本机额度变化只能称“观察到变化”或“疑似到账”，不能据此断言重置原因。

## 隐私

- 不需要普通 X/Twitter 登录账号。
- 不读取浏览器 Cookie。
- 不上传 Codex 凭据、邮箱、会话或提示词。
- 网络请求会向 `codex-resets.com` 暴露 IP、时间和 User-Agent。
- 本地历史只记录额度百分比、窗口重置时间和采样时间，用于估算消耗速度。
