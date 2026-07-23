---
name: codex-reset-watch
description: 检查 Codex 周额度、Tibo/社区临时重置信号与来源新鲜度，并用保守数学模型给出“猛猛蹬、可以加速蹬、稳着蹬、收着蹬”建议。Use when the user asks about Codex usage limits, weekly quota, reset probability, whether to conserve or spend tokens, Tibo/Thibault Sottiaux reset announcements, banked resets, or whether they can “猛猛蹬”.
---

# Codex Reset Watch

将公共临时重置信号与用户本机 Codex 周额度分开读取，再生成保守的额度使用建议。默认不要求 X/Twitter 账号，也不依赖作者维护的服务器。

## 执行流程

1. 运行：

```bash
python3 "<skill-dir>/scripts/resetwatch.py" check
```

需要机器可读结果时追加 `--json`。

2. 自动读取失败时，先说明失败的是“公共信号”还是“本机额度”，不要把缺失值猜成 0：

```bash
python3 "<skill-dir>/scripts/resetwatch.py" check \
  --weekly-used <已使用百分比> \
  --weekly-reset-at <ISO时间或Unix秒> \
  --probability <未来48小时临时重置百分比>
```

3. 用户只提供部分信息时：
   - 有周额度、没有公共信号：按临时重置贡献 0 计算基础额度建议；即使余量充足也最多输出“稳着蹬”。
   - 有概率、没有周额度：只报告信号，不能给“猛猛蹬”结论。
   - 没有近期历史：用当前窗口的平均消耗速度估计；明确标注为冷启动。

4. 输出固定包含：
   - 周额度已用、剩余、距正常重置时间；
   - 临时重置原始信号、套餐到账折扣后的组合决策分；手动输入时才称概率；
   - 预计正常需求、安全储备、风险折算额度；
   - 最终档位与一句可行动建议；
   - 数据来源、更新时间和降级状态。

## 建议档位

- **猛猛蹬**：可以安排高消耗任务，但仍保留安全垫。
- **可以加速蹬**：可以提高强度，不要一次性清空额度。
- **稳着蹬**：维持节奏，不要押注临时重置。
- **收着蹬**：按当前速度可能撑不到正常周重置。
- **先别赌**：来源过期、冲突，或关键数据不足。

固定使用保守算法，当前版本不实现均衡或激进模式。

## 事实边界

- 将现成网站的百分比称为“启发式评分”或“社区预测”，除非来源公开了校准结果。
- `Tibo 已宣布` 不等于每个套餐、每个额度桶都已到账。
- 本机额度增加只能称“观察到变化”或“疑似到账”，不能凭相关性断言因果。
- 公共来源全部失败时使用周额度给出“不押注重置”的基础建议并标记 `stale`；不得把抓取失败解释为“不会重置”。
- 默认公共来源不需要用户的 X 账号。X Developer API 是未来可选增强，不读取普通用户登录 Cookie。
- 自动读取本机额度失败时，可让用户从 Codex 的 Usage 页面或 `/status` 抄录“weekly used”和正常重置时间，再使用手动参数。

## 资源

- 数学模型、字段定义与解释：按需读取 [references/methodology.md](references/methodology.md)。
- 数据源、降级和隐私边界：按需读取 [references/sources.md](references/sources.md)。
- 确定性检查脚本：[scripts/resetwatch.py](scripts/resetwatch.py)。
