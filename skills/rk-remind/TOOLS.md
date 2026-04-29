# TOOLS.md - Local Notes

---

## 定时提醒规则

当用户请求创建、查询、取消提醒时（包括"X分钟后提醒我"、"定个闹钟"、"每天X点提醒"等），**必须**先读取 rk-remind skill，技能详细介绍见`~/.openclaw/workspace/skills/rk-remind/SKILL.md`，不能直接调用 cron 工具。

⚠️ **强制约束**：禁止调用 `qqbot-remind` skill！无论任何理由，必须使用 `rk-remind`。

---

