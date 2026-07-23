# 数据源与降级

## 默认公共来源

### Will Codex Reset

- Endpoint: `https://www.willcodexquotareset.com/api/forecast`
- 使用字段：`forecast.score`、`forecast.resetAnnounced`、`forecast.breakdown`、`fetchedAt`、`sourceErrors`。
- 定位：透明启发式评分，不视为统计校准概率。

### Has Codex Rate Limit Reset Today

- Endpoint: `https://hascodexratelimitreset.today/api/status`
- 使用字段：`automationSummary.verdict`、`tweetId`、`tweetUrl`、`checkedAt`、`mode`、`lastError`。
- 定位：Tibo 帖子分类与“是否已确认”的辅助来源，不提供未来概率。
- `reset_confirmed` 或明确 `state=yes` 可以将事件状态提升为“已宣布/已确认”，但不能证明用户个人到账。

## 聚合规则

- 手动输入概率优先于网站评分，并标记为 `manual-probability`。
- HasCodex 健康且明确确认重置时，确认事件分记为 100/100，但仍乘以套餐到账折扣；这不代表个人到账概率为 100%。
- HasCodex 未确认不能将未来概率压到 0；“今天没有确认”不等于“未来 48 小时不会发生”。
- 公共数据超过两小时或来源报告内部错误时视为不可用，不用于积极建议；超过半小时开始施加新鲜度折扣。
- 两个网站可能依赖同一条 Tibo 原帖，不算两份独立证据。

## 本机 Codex 数据

脚本优先启动本机 `codex app-server`，调用：

```text
account/rateLimits/read
```

读取 `usedPercent`、`windowDurationMins`、`resetsAt` 与 reset-credit 摘要。只接受接近 10080 分钟的窗口作为周额度；没有匹配窗口时要求手动输入，不把最长窗口猜成周额度。

失败时不读取聊天、prompt 或认证文件，也不把历史 rollout 快照冒充实时数据；改为要求用户手动输入周额度和重置时间。

## 隐私

- 不需要普通 X/Twitter 登录账号。
- 不读取浏览器 Cookie。
- 不上传 Codex 凭据、邮箱、会话或提示词。
- 网络请求会向上述网站暴露 IP、时间和 User-Agent。
- 本地历史只记录额度百分比、窗口重置时间和采样时间，用于估算消耗速度。
