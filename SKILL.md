---
name: codex-reset-watch
description: 检查 Codex 周额度、Tibo/社区临时重置信号与来源新鲜度，并用保守数学模型给出“猛猛蹬、可以加速蹬、稳着蹬、收着蹬”建议。Use when the user asks about Codex usage limits, weekly quota, reset probability, whether to conserve or spend tokens, Tibo/Thibault Sottiaux reset announcements, banked resets, or whether they can “猛猛蹬”.
---

# Codex Reset Watch

将公共临时重置信号与用户本机 Codex 周额度分开读取，再生成保守的额度使用建议。默认不要求 X/Twitter 账号，也不依赖作者维护的服务器。

## 设计理由与平台边界

社区风声最容易制造两种后悔：猛蹬但没有重置会提前断粮，没蹬但真的重置又会让剩余额度变空气。因此本 skill 先用本机周额度计算基础续航，再把公共信号作为折扣后的辅助项；信号不可用时绝不猜成确定额度。

- 执行脚本只依赖 Python 3.10+ 标准库；自动读取额度需要本机可用的 Codex CLI。
- `codex app-server` 不可用时改用手动参数，不读取聊天、提示词、认证文件或浏览器 Cookie 兜底。
- 公共来源是第三方网站，不是 OpenAI 官方接口；平台没有网页访问能力时仍可用 `--no-network` 运行本机基础建议。
- 其他支持 `SKILL.md` 的 Agent 可以执行相同脚本；若平台不能调用本机命令，只能按本文模型解释用户手动提供的数据，不得声称已自动检查。
- 自动读取目前以 macOS/Linux 为主要验证环境；Windows 的子进程管道兼容性未实测，失败时使用手动参数。

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
   - 有周额度、没有公共信号（含来源过期或失败）：按临时重置贡献 0 计算基础额度建议；即使余量充足也最多输出“稳着蹬”——积极档位容易被理解成“可以押注临时重置”。不升级为“先别赌”。
   - 有概率、没有周额度：只报告信号，不能给“猛猛蹬”结论。
   - 没有近期历史：用当前窗口的平均消耗速度估计；明确标注为冷启动。

4. 以脚本输出为准转述档位、可行动建议和关键数字；任何来源过期、失败或手动输入的降级状态都要说出来。

## 建议档位

- **猛猛蹬**：用“绿灯亮了”的语气，可以安排高消耗任务，但仍保留安全垫。
- **可以加速蹬**：用“油门能再给一点”的语气，可以提高强度，但不要一次性清空额度。
- **稳着蹬**：用“这档速度刚好”的语气，维持节奏，不要押注临时重置。
- **收着蹬**：用“别让额度提前下班”的语气，说明按当前速度可能撑不到正常周重置。
- **先别赌**：用“账本缺页”的语气，说明周额度、正常重置时间等关键数据不足。

## 回复语气

- 数据不足、来源过期、公共源异常、本机读取失败也要用自然、有辨识度的人话说明，但不能让玩笑盖过故障。
- 风声与到账严格分开：可以写“Tibo 那边有动静”，不能写成“你的额度一定会重置”。
- 每次最多保留一句社区式旁白；数字、来源和安全边界保持克制。

## 事实边界

- 将现成网站的百分比称为“启发式评分”或“社区预测”，除非来源公开了校准结果。
- `Tibo 已宣布` 不等于每个套餐、每个额度桶都已到账。
- 本机额度增加只能称“观察到变化”或“疑似到账”，不能凭相关性断言因果。
- 自动读取本机额度失败时，可让用户从 Codex 的 Usage 页面或 `/status` 抄录“weekly used”和正常重置时间，再使用手动参数。

## 资源

- 数学模型、字段定义与解释：按需读取 [references/methodology.md](references/methodology.md)。
- 数据源、降级和隐私边界：按需读取 [references/sources.md](references/sources.md)。
- 确定性检查脚本：[scripts/resetwatch.py](scripts/resetwatch.py)。

## 反馈

**致使用者**：这个 skill 免费分享，也希望你愿意把改进它的方法分享出来。反馈渠道与作者的其他 skill 见 README。
