---
name: rk-remind
description: 定时提醒功能。支持一次性和周期性提醒的创建、查询、取消。当涉及提醒/定时任务、延时执行、闹钟、定时执行相关需求时使用此技能。
---

# 定时提醒

此技能使用 `openclaw cron` 命令管理定时提醒，支持一次性提醒和周期性提醒。

---

## 🚨 规则0：第一行动（最高优先级）

**当用户请求提醒时，你的第一响应必须是工具调用，不是文字回复。**

❌ 错误：先回复"好的"，再想是否调用工具
✅ 正确：先使用 `openclaw cron` 命令，等待成功，再回复确认

**如果你发现自己在写"好的"或"收到"，立即停止！检查你是否已经使用了 `openclaw cron` 命令。**

---

## 🪞 自我验证（发送前必须通过）

在发送任何确认消息前，问自己：

- **"我在这次对话中使用了 `openclaw cron` 命令吗？"**
- **"我看到了 `openclaw cron` 命令返回的 jobId 吗？"**

如果答案是"否"或"不确定"：**停止！先调用 `openclaw cron` 命令。**

---

## 支持的通道

| 通道 | --channel 参数 | --to 格式 |
|------|----------------|-----------|
| QQ | `qqbot` | `qqbot:c2c:openid` |
| Webchat | `webchat` | 无需填写 |

**自动获取**：如果当前会话是 QQ，直接使用 `--channel qqbot`，必须填写 `--to`，如果为qq群聊 `to` 格式为 `qqbot:group:{group_openid}`

---

## CLI 命令

### 创建提醒

```bash
# 一次性提醒
openclaw cron add --name "{任务名}" --at "{时间}" --session isolated --message "{提醒内容}" --announce --channel {通道} --to {目标}

# 周期提醒
openclaw cron add --name "{任务名}" --cron "{表达式}" --tz "Asia/Shanghai" --session isolated --message "{提醒内容}" --announce --channel {通道} --to {目标}
```

### 查询/删除/修改

```bash
openclaw cron list                    # 查询所有提醒
openclaw cron remove <job-id>         # 删除提醒
openclaw cron edit <job-id> --name "{新任务名}" --message "{新提醒内容}"   # 修改任务
```
**当只修改 name或message 其中一项时，必须同步修改另一项（填写内容根据提示推断）！其他参数没要求的情况下保持不变**

---

## 参数说明

| 参数 | 说明 | 必填 |
|------|------|------|
| `--name` | 任务名称 | ✅ |
| `--at` | 一次性时间 (`5m`, `20:00`, `2026-04-07T20:00:00+08:00`) | ✅ (一次性) |
| `--cron` | Cron 表达式 | ✅ (周期) |
| `--session` | `isolated` (推荐) | ✅ |
| `--message` | 提醒内容（agentTurn 用） | ✅ |
| `--system-event` | 系统事件内容（main 用） | 可选 |
| `--announce` | 启用投递到聊天 | ✅ (需要投递时) |
| `--channel` | 通道 (`qqbot`, `webchat`) | ✅ |
| `--to` | 目标地址 | QQ 必填 |
| `--delete-after-run` | 执行后删除（一次性任务） | 可选 |
| `--tz` | 时区（周期任务） | 周期必填 |
| `--best-effort-deliver` | 投递失败不报错 | 可选 |

---

## 一次性提醒示例

| 场景 | 命令 |
|------|------|
| 5分钟后提醒喝水 | `openclaw cron add --name "喝水提醒" --at "5m" --session isolated --message "提醒用户喝水" --announce --channel {当前通道} --to {目标}` |
| 20点提醒开会 | `openclaw cron add --name "会议提醒" --at "20:00" --session isolated --message "提醒用户：会议开始" --announce --channel qqbot --to qqbot:c2c:openid` |

---

## 周期提醒示例

| 场景 | 命令 |
|------|------|
| 每天8点打卡 | `openclaw cron add --name "打卡提醒" --cron "0 8 * * *" --tz "Asia/Shanghai" --session isolated --message "提醒用户打卡" --announce --channel {当前通道}` |
| 工作日9点提醒 | `openclaw cron add --name "工作提醒" --cron "0 9 * * 1-5" --tz "Asia/Shanghai" --session isolated --message "提醒内容" --announce --channel {当前通道}` |
| 每半小时喝水 | `openclaw cron add --name "喝水提醒" --cron "0,30 * * * *" --tz "Asia/Shanghai" --session isolated --message "提醒用户喝水" --announce --channel {当前通道}` |

---

## Cron 表达式速查

| 场景 | 表达式 |
|------|--------|
| 每天早上8点 | `0 8 * * *` |
| 每天晚上10点 | `0 22 * * *` |
| 工作日早上9点 | `0 9 * * 1-5` |
| 每周一早上9点 | `0 9 * * 1` |
| 每周末上午10点 | `0 10 * * 0,6` |
| 每小时整点 | `0 * * * *` |
| 每半小时 | `0,30 * * * *` |
| 每15分钟 | `0,15,30,45 * * * *` |

---

## AI 决策指南

| 用户说法 | 操作 |
|----------|------|
| "5分钟后提醒我喝水" | 计算相对时间 → ISO-8601 UTC → `--at` + `--announce` + 当前通道 |
| "每天8点提醒我打卡" | 使用 `--cron` + `--announce` + 当前通道 |
| "20点提醒开会" | 使用 `--at "20:00"` + `--announce` + 当前通道 |
| "我有哪些提醒" | `openclaw cron list` |
| "取消喝水提醒" | `openclaw cron list` → `openclaw cron remove <id>` |
| "把喝水提醒改成运动提醒" | `openclaw cron list` → 找到 id → 推断新 message和name → `openclaw cron edit --name --message` |

---

## ⚠️ Agent Tool 调用注意

**当前 `openclaw cron` 工具需要 ISO-8601 绝对时间戳，不支持 CLI 的相对时间格式（如 "5m"）。**

### 相对时间转换步骤

当用户说 "X分钟后提醒我"：

1. **获取当前时间**：从 cron 错误信息或 `session_status` 获取当前 UTC 时间
2. **计算目标时间**：当前时间 + X 分钟
3. **转换为 ISO-8601 格式**：使用 UTC 时区，格式如 `2026-04-08T09:58:00Z`
4. **调用 cron 工具**：

```json
{
  "action": "add",
  "job": {
    "name": "任务名",
    "schedule": {
      "kind": "at",
      "at": "2026-04-08T09:58:00Z"  // 注意必须是 UTC
    },
    "payload": {
      "kind": "agentTurn",
      "message": "{提醒内容}"
    },
    "delivery": {
      "mode": "announce",
      "channel": "{当前通道}",
      "to": "{目标}" // QQ必填，Webchat 可省略
    },
    "sessionTarget": "isolated"
  }
}
```

### 常见错误

| 错误 | 原因 | 解决 |
|------|------|------|
| `expected ISO-8601 timestamp (got 1m)` | 使用了相对时间字符串 | 转换为绝对时间戳 |
| `missing required field: to` | QQ 通道未填写 to | 添加 `"to": "qqbot:c2c:xxx"` |

---

## 生成提醒时的回复模板

- 一次性：`⏰ 好的，{时间}后提醒你{内容}~`
- 周期：`⏰ 收到，{周期}提醒你{内容}~`
- 查询：`列出所有提醒`
- 删除：`✅ 已取消"{名称}"`

---

## 触发提醒时的回复模板

- 第一句：事件提醒（**不做判断解释，不要回复「触发」「收到」之类的话**）
- 第二句：简短的鼓励/关怀，控制在2句话以内