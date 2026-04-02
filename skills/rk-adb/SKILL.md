---
name: rk-adb
description: Rockchip 设备 ADB 连接技能。支持本地网络/本地有线 ADB 和通过 SSH 连接远程 Windows/Linux PC 的远程 ADB。当用户需要：推送文件到设备、设置系统属性（setprop）、抓取 logcat/kernel log、重启设备、执行 adb shell 命令、dump 设备信息、视频播放测试、SurfaceFlinger 日志抓取时，使用此技能确保 ADB 连接可用。
---

# Rockchip ADB 连接

此技能帮助用户连接 Rockchip Android 设备，支持三种模式：
1. **本地有线 ADB**：设备通过 USB 线直连本地主机 (最稳定)
2. **本地网络 ADB**：设备与终端同网段，直接网络 ADB
2. **远程模式**：通过 SSH 连接远程 PC（Windows/Linux），PC 与设备 USB 连接

## 触发条件

检测到以下需求时触发：
- 推送文件 / adb push / adb pull
- 设置属性 / setprop / getprop
- 抓取日志 / logcat / kernel log / dmesg
- 重启设备 / reboot
- 执行 shell 命令 / adb shell
- dump 信息 / dumpsys
- **视频播放测试 / 视频卡顿 / SurfaceFlinger**
- **渲染线程性能 / HWC / VOP 配置**

> [!IMPORTANT]
> **工作目录规则**: 所有辅助脚本必须在**用户的工作目录**执行，使用脚本的**绝对路径**调用。
> - 不要 `cd` 到技能目录执行脚本。
> - 这样可以确保生成的 wrapper 和配置文件保存在当前项目目录下。

> [!IMPORTANT]
> **自动保存配置**: 首次成功连接 ADB 设备后，技能会**主动询问**用户是否保存配置到 skill 目录。保存后可快速复用，无需重复输入 SSH 信息。

---

## 全局行为规则

### 自动 Root 权限处理

当检测到以下情况时：

* 执行需要 root 权限的命令（如访问 `/data`、`dmesg`、`setprop persist.*` 等）
*  出现 `Permission denied` 或权限不足错误

必须自动尝试执行：

```
adb root
adb wait-for-device
```

然后重试原命令。

> ⚠️ 注意：
>
> - 若设备不支持 `adb root`（user 版本），需提示用户设备限制
> - 若是远程 ADB wrapper，同样通过 wrapper 执行 `./adb root`

### 多语言响应规则

- 若用户使用**中文提问** → **必须使用中文回复**
- 若用户使用英文提问 → 使用英文回复
- 默认优先使用中文（如果无法判断）

> ⚠️ 所有交互提示（选择、确认、错误提示）必须与用户语言保持一致

###  **多设备连接处理规范 **

场景定义**：当 `adb devices` 或远程 Wrapper 列表中显示 **2 台及以上设备时。

**处理策略**： 

1. **识别**：自动执行 `adb devices -l` 获取所有设备的 Serial Number（序列号）

2. **交互**：**必须** 中断自动化流程，向用户展示设备列表，并询问：“检测到多台设备在线，请指定本次操作的目标设备 Serial：“

   示例列表：

   - `RK3588_BOX` (Serial: ABCD1234) 
   - `RK3399_PHONE` (Serial: XYZZ9999) 

3.  **执行**：在所有后续的 ADB 命令中，**强制注入** `-s <用户选择的Serial>` 参数。

   错误示例：`adb shell getprop` 

   正确示例：`adb -s ABCD1234 shell getprop` 

**例外**：仅当用户明确要求“对所有设备执行”时（如批量重启），才遍历执行，否则默认只操作单台设备。

## 使用流程

> ⚠️ **重要**：每次使用此技能时，必须先检查是否有已保存的配置！

### Step 0: 检查已保存配置（必须执行）

```bash
python3 <SKILL_PATH>/scripts/adb_helper.py list-profiles
```

**如果有配置** → 询问用户是否复用，确认后生成 wrapper 或直接使用配置信息

**如果没有配置** → 进入「首次使用引导」

---

## 首次使用引导

### Step 1: 确定连接模式

> 请选择 ADB 连接方式：
> 1. **本地有线 ADB** - 设备通过 USB 线直连本地主机
> 1. **本地网络 ADB** - 设备与终端在同一网段
> 2. **远程 USB ADB** - 设备通过 USB 连接到远程 Windows/Linux PC

### Step 2a: 本地有线模式

```
adb devices
adb shell
```

### Step 2b: 本地网络模式

```bash
adb connect <设备IP>:5555
adb devices
```

### Step 2c: 远程模式

询问远程 PC 操作系统（Windows/Linux）。

**Windows 用户**需先确认：
1. 已开启 OpenSSH 服务器
2. **防火墙已允许 SSH 连接**（如果连接超时，尝试关闭防火墙）

收集 SSH 信息后测试连接：
```bash
python3 <SKILL_PATH>/scripts/adb_helper.py test-ssh --host IP --user 用户名 --password "密码"
python3 <SKILL_PATH>/scripts/adb_helper.py discover-devices --host IP --user 用户名 --password "密码"
```

### Step 3: 保存配置

```bash
python3 <SKILL_PATH>/scripts/adb_helper.py save-profile \
  --name "profile-name" \
  --host IP --user 用户名 --password "密码" \
  --platform linux/windows
```

---

## 无感使用远程 ADB（推荐）

> 通过 ADB wrapper，可以像使用本地 adb 一样操作远程设备，无需手动处理 SSH。

### 生成 Wrapper

```bash
python3 <SKILL_PATH>/scripts/adb_helper.py generate-wrapper --profile "profile-name"
```

### 使用 Wrapper

```bash
./adb devices
./adb shell getprop ro.build.fingerprint
./adb pull /sdcard/test.txt ./
./adb push ./file.apk /sdcard/
./adb logcat -d > logcat.txt
```

### 支持的命令

| 命令 | 处理方式 |
|-----|---------|
| `pull` | 先拉取到远程 PC 临时目录，再 scp 到本地 |
| `push` | 先 scp 到远程 PC 临时目录，再 push 到设备 |
| 其他 | 直接通过 SSH 转发执行 |

### 切换 Profile

```bash
# 重新生成 wrapper
python3 <SKILL_PATH>/scripts/adb_helper.py generate-wrapper --profile "other-profile"

# 或使用环境变量
ADB_PROFILE="other-profile" ./adb devices
```

---

## 本地模式常用命令

| 操作 | 命令 |
|------|------|
| 推送文件 | `adb push <本地路径> <设备路径>` |
| 拉取文件 | `adb pull <设备路径> <本地路径>` |
| 设置属性 | `adb shell setprop <属性名> <值>` |
| 查看属性 | `adb shell getprop <属性名>` |
| 抓取并保存 logcat | ``adb logcat -d > ./filename.log`` |
| 抓取并保存 kernel log | `adb shell dmesg > ./filename.log` |
| 重启设备 | `adb reboot` |

## 日志文件保存流程 

当用户要求抓取日志（如 `dmesg`, `logcat`）或拉取文件时，必须遵循以下流程： 

* 确定保存路径 
  * **路径**：使用脚本绝对路径或 `$(pwd)` 指向**用户的工作目录**    
  * **目的**：确保生成的文件能直接出现在用户本地的项目文件夹中，而非 AI 的临时目录
* 日志文件命名规则
  * 格式：`[设备简写]_[日志类型]_[描述]_[时间戳].log` 
  * 示例：`RK3588_dmesg_boot_20251230.log` 或 `ABC123_logcat_main_1000.log`

* 执行命令并重定向
  * **本地 ADB**：直接使用 `> ./filename.log`
  * **远程 Wrapper**：同样使用 `> ./filename.log`。Wrapper 会处理 SSH 传输，但重定向由本地 Shell 处理
* 反馈结果 命令执行完成后，必须明确告知用户： "日志已保存在: [绝对路径/相对路径]/filename.log"

---

## 视频播放测试

> 用于检测 RK3588 或其他 Android 设备视频播放卡顿问题，支持 SurfaceFlinger 日志抓取和性能分析。

### 触发条件

- 视频卡顿 / 播放测试
- SurfaceFlinger 日志 / 渲染性能
- HWC / VOP 配置检查
- 帧率分析 / Frame Duration

### 使用流程

#### Step 1: 确保 ADB 连接

```bash
adb devices
```

若连接多个设备，主动告知用户并让其选择：

```bash
# 查看设备列表
adb devices -l

# 指定设备操作
adb -s <设备序列号> shell dumpsys SurfaceFlinger
```

#### Step 2: 执行视频播放测试

**自动模式（推荐）**：
```bash
# 用户只需描述需求，技能自动执行以下命令
adb shell dumpsys SurfaceFlinger
adb shell getprop | grep -E "gpu|hwc"
```

**手动模式**：
```bash
# 1. 启动 4K 视频播放测试（替换为实际视频路径）
adb shell am start -a android.intent.action.VIEW \
  -d file:///sdcard/Movies/SVEP_sony_show/04_MEMC_race_4K_30fps.mp4 \
  -t video/mp4

# 2. 等待 30 秒后检查 SurfaceFlinger 状态
sleep 30
adb shell dumpsys SurfaceFlinger

# 3. 获取 GPU 服务状态
adb shell getprop | grep -E "gpu|hwc"

# 4. 检查 Frame Duration 统计
adb shell dumpsys SurfaceFlinger | grep "Static screen stats:"
```

### 视频路径优先级

```
/sdcard/Movies/
/sdcard/Download/
```

若未找到视频，告知用户，并让用户指定视频路径，然后重新执行播放测试。

### 测试结果格式

```
=== RK3588 4K 视频播放测试结果 ===

【1. SurfaceFlinger 帧率情况】
Frame Rate Overrides (backdoor): {}
60.00fps: 0d00:XX:XX.XXX

Total missed frame count: 1
HWC missed frame count: 1
GPU missed frame count: 1

Frame Duration 统计:
  < 1 frame:    XX.XXXs (XX.X%)
  < 2 frame:    XX.XXXs (XX.X%)
  < 7 frame:    XX.XXXs (XX.X%)
  7+ frame:     XX.XXXs (XX.X%)

【2. GPU 服务状态】
[init.svc.gpu]: [running]

【3. 核心性能指标】
- Target FPS: 60.00fps ✅
- HWC 帧丢失：X 帧
- GPU 帧丢失：X 帧
- 重度延迟：XX.X% (93.2% = 2743s >7 帧)
```

### 常见性能问题

| 问题 | 原因 | 参考解决方案 |
|------|------|-------------|
| 93% 时间延迟超过 7 帧 | HWC 渲染延迟 | 检查 HWC2 配置 |
| GPU 带宽协商失败 | DPM 问题 | 检查 Display Power Management |
| AVC 权限拒绝 | RenderThread 配置 | 检查 SELinux 策略 |

### 硬件配置参考

```ini
HWC version: HWC2-1.6.1
[display]: [HDMI-A-1:70:connected]
```

---

## 故障排除

### 本地模式连接失败

1. **开启开发者选项**：设置 > 关于设备 > 连续点击版本号 7 次
2. **开启网络 ADB**：开启 USB 调试 + 无线调试（Android 11+）或 `adb tcpip 5555`
3. **检查网络**：`ping <设备IP>` 和 `nc -zv <设备IP> 5555`

### 远程模式连接失败

```bash
# 测试 SSH
python3 <SKILL_PATH>/scripts/adb_helper.py test-ssh --host IP --user 用户名 --password "密码"

# 发现设备
python3 <SKILL_PATH>/scripts/adb_helper.py discover-devices --host IP --user 用户名 --password "密码"
```

### Windows SSH 未开启

使用 PowerShell（管理员）：
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'

# 警告：如果连接失败，可能是防火墙拦截。
# 尝试关闭防火墙（仅测试用）：
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

# 或仅允许 SSH 端口（推荐）：
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

---

## Windows 远程 PC 注意事项

> ⚠️ 通过 SSH 连接 Windows 远程 PC 时，命令在 Windows cmd 环境执行，与 Linux 行为不同。

### 1. 避免在 Windows 上使用 grep

Windows 没有 `grep` 命令。过滤输出应在**本地 Linux 端**进行：

```bash
# ❌ 错误：grep 在 Windows 上执行，会报错
./adb shell "getprop | grep hdr"

# ✅ 正确：adb shell 输出后，在本地 grep
./adb shell getprop 2>/dev/null | grep -i hdr
```

### 2. 处理乱码输出

Windows SSH 返回的中文可能显示为乱码（编码问题），这是正常的，不影响功能。

### 3. 使用 adb shell 内置命令过滤

如需在设备端过滤，使用 Android shell 自带的工具：

```bash
# 在 Android shell 内使用 grep（设备上有）
./adb shell "getprop | grep ro.build"

# 查看 DRI 节点（HDR 状态）
./adb shell "cat /d/dri/0/summary"
```

---

## 常见错误

| 错误 | 原因 | 解决 |
|-----|------|-----|
| `Permission denied` | SSH 密码错误 | 确认密码 |
| `Connection refused` | SSH 未开启 | 开启远程 PC 的 SSH 服务 |
| `no devices found` | 无 ADB 设备 | 检查 USB 连接和设备授权 |
| `device unauthorized` | 设备未授权 | 在设备上确认调试授权弹窗 |
| `adb: command not found` | ADB 未安装 | 在远程 PC 安装 ADB |
| `'grep' 不是内部或外部命令` | Windows 无 grep | 在本地端 pipe grep |
| 乱码输出 | Windows 编码问题 | 正常现象，不影响功能 |

---

## 辅助脚本命令

```bash
python3 <SKILL_PATH>/scripts/adb_helper.py list-profiles      # 列出配置
python3 <SKILL_PATH>/scripts/adb_helper.py get-profile        # 获取配置详情
python3 <SKILL_PATH>/scripts/adb_helper.py generate-wrapper   # 生成 wrapper
python3 <SKILL_PATH>/scripts/adb_helper.py test-ssh           # 测试 SSH
python3 <SKILL_PATH>/scripts/adb_helper.py discover-devices   # 发现设备
python3 <SKILL_PATH>/scripts/adb_helper.py test-adb           # 测试 ADB
python3 <SKILL_PATH>/scripts/adb_helper.py exec               # 执行命令
```

> 如需手动构造 SSH/ADB 命令，参见 [references/manual_commands.md](references/manual_commands.md)

---

## 配置文件格式

配置文件保存在 skill 目录内，例如：`~/.openclaw/workspace/skills/rk-adb/config/config.json`

```json
{
  "last_used_profile": "lab-windows",
  "profiles": {
    "lab-windows": {
      "platform": "windows",
      "host": "172.16.21.200",
      "username": "admin",
      "password": "password123",
      "device_serial": "ABCD1234",
      "last_success": "2025-12-30T10:00:00+08:00"
    }
  }
}
```

### 自动保存提示

首次成功连接 ADB 设备后，技能会主动询问：

```
✓ ADB 连接成功
是否保存此配置以便下次快速连接？(y/n)
→ 输入 "y" 保存配置
→ 输入 "n" 跳过
```

保存后可用 `generate-wrapper` 快速生成 wrapper 复用配置。
