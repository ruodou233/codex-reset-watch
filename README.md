# codex-reset-watch — Codex 额度哨兵

Codex 周额度该省着用，还是可以猛猛蹬？这个 Skill 会把本机周额度、正常重置时间、公共临时重置信号和安全储备放进同一笔保守账里，给出可执行的使用建议。

## 这是什么

`codex-reset-watch` 是一个本地优先的 Codex Skill。它解决的不是“猜 Tibo 什么时候按按钮”，而是两个更实际的问题：

- 如果临时重置没来，按当前速度会不会提前用完周额度？
- 如果临时重置真的来了，现在没用掉的额度会不会白白变空气？

默认输出分为“猛猛蹬、可以加速蹬、稳着蹬、收着蹬、先别赌”五档。社区风声只作为折扣后的辅助信号；来源过期、报错或不可用时，不会被当成确定额度。

当前版本是实验性的保守决策工具：模型阈值是可审计的产品默认值，但尚未经过长期回测，不应把输出理解为重置预测保证。

## 它会检查什么

- 通过本机 `codex app-server` 读取接近 7 天窗口的 Codex 周额度。
- 读取两个公开网站的临时重置信号，并检查来源新鲜度与健康状态。
- 结合近期消耗速度、距正常重置时间和安全储备，计算保守的安全余量。
- 单独展示 banked reset 数量与最近到期时间，但默认不替你决定何时使用。
- 将结果翻译成一条行动建议，同时保留可审计的数字与来源状态。

## 安装

Claude Code：

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/ruodou233/codex-reset-watch.git ~/.claude/skills/codex-reset-watch
```

Codex：

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/ruodou233/codex-reset-watch.git ~/.codex/skills/codex-reset-watch
```

或通过 [agentops-skills](https://github.com/ruodou233/agentops-skills) 安装整套 Skill。

安装后可以直接问：

> 检查一下我的 Codex 周额度，现在能不能猛猛蹬？

也可以直接运行：

```bash
python3 scripts/resetwatch.py check
```

机器可读输出：

```bash
python3 scripts/resetwatch.py check --json
```

本机额度自动读取失败时，可以手动提供：

```bash
python3 scripts/resetwatch.py check \
  --weekly-used 42 \
  --weekly-reset-at 2026-07-29T01:00:00+08:00
```

## 输出示例

```text
收着蹬｜先松油门，别让额度提前下班
今天怎么用：只跑今天非做不可的重任务，其余先排队；至少留住 25.0% 的安全垫。
为什么：照当前速度，额度可能比正常周重置更早见底。

重置风声：偏弱（34%）｜按你的到账折扣剩 25.5%，不当额度花
公共哨兵：还没等到可信口供。别把 Tibo 的心情当你的预算。
```

实际数字会随账户额度、使用速度与公共信号变化。

## 依赖与平台边界

- 运行脚本只需要 Python 3.10+ 标准库。
- 自动读取本机额度需要可用的 Codex CLI；没有时仍可使用手动参数。
- 自动读取以 macOS/Linux 为主要验证环境；Windows 的子进程管道兼容性未实测，失败时使用手动参数。
- 公共信号来自第三方网站，不是 OpenAI 官方 SLA，也不是统计校准概率。
- `Tibo 已宣布` 不等于每个套餐、账户和额度桶都已经到账；最终以自己的 Usage 状态为准。
- 本地历史只保存额度百分比、窗口时间与采样时间，不读取聊天、提示词、浏览器 Cookie 或 Codex 凭据。
- Skill 在成功核验后 7 天内不再访问 GitHub；失败时本次任务不重试，但后续任务可能再次尝试。最近成功核验时间只保存在 `~/.cache/codex-reset-watch/update-checked-at`，未来或无效时间不会抑制检查。可创建 `~/.config/agentops-skills/codex-reset-watch.no-update-check` 关闭。

## 如何验证

```bash
python3 -m unittest scripts/test_resetwatch.py
python3 -m py_compile scripts/resetwatch.py
```

测试覆盖周窗口选择、过期时间、公共信号降级、banked reset 展示、五档文案及百分比格式。保守模型与数据源边界见 [`references/methodology.md`](references/methodology.md) 和 [`references/sources.md`](references/sources.md)。

## License

MIT

## 反馈与作者

有修改方案或问题，欢迎在本仓库提 issue / PR；也可以通过小红书「错误乱码」、微信公众号「能工智人错误乱码」、B站「若逗道人」找到我。
