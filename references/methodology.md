# 保守额度建议模型

## 输入

- `U`：当前周额度已使用百分比。
- `R = 100 - U`：当前周额度剩余百分比。
- `H`：距离正常周重置的小时数。
- `b80`：近期每小时消耗速度的 80 分位数；冷启动时使用当前窗口平均速度。
- `S_event`：未来 48 小时临时重置的社区启发式评分；用户手动提供经过判断的概率时才记为概率。
- `P_arrival`：临时重置实际覆盖当前用户套餐与额度桶的折扣概率，默认 75%。

用于决策的组合分：

```text
S_user = S_event × P_arrival
```

公共来源缺少按套餐统计时，`P_arrival` 只是风险折扣，不是经验命中率；因此 `S_user` 也不是统计概率。

## 正常需求与储备

```text
Need = min(100, b80 × H)
Reserve = max(10, Need × 0.25)
Required = Need + Reserve
```

近期历史至少需要两个同一周窗口内的有效采样点。排除额度下降、跨窗口和小于一小时的间隔。冷启动时：

```text
elapsed = weekly_window_hours - H
b80 = U / elapsed
```

## 风险折算

保守模式不直接按期望值消费潜在重置额度：

```text
W = clamp((S_user - 65) / 30, 0, 1)
ResetCredit = min(U, 25) × W
SafeHeadroom = R + ResetCredit - Required
```

含义：

- `S_user ≤ 65`：不把临时重置计入可消费额度。
- `S_user = 80`：只认可一半、且最多 25% 周额度的潜在收益。
- `S_user ≥ 95`：认可上限内的全部潜在收益。

已存在的 banked reset credit 是另一种确定性资源。默认单独展示，不自动计入 `ResetCredit`，避免替用户决定何时消费。

## 档位

```text
SafeHeadroom ≥ 20       猛猛蹬
8 ≤ SafeHeadroom < 20  可以加速蹬
-5 ≤ SafeHeadroom < 8  稳着蹬
SafeHeadroom < -5      收着蹬
周额度或正常重置缺失     先别赌
```

这些阈值是产品的保守默认值，不是经过长期回测的最优策略。公开使用时应保留版本号，并依据真实历史做校准。

公共信号缺失但周额度有效时，仍可给出“不押注临时重置”的基础建议；这种情况下不得输出“猛猛蹬”或“可以加速蹬”，最多为“稳着蹬”。
