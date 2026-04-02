# 远程模式手动命令参考

> 如果使用 `adb_wrapper.sh`，无需阅读此文档。本文档仅供需要手动构造 SSH/ADB 命令的高级场景。

## 规则

### 规则 1：使用 sshpass

远程模式必须使用 `sshpass -p '密码' ssh ...` 格式，禁止普通 `ssh`（会要求交互输入）。

### 规则 2：Windows 用户名格式

Windows 用户名如 `COMPUTER\user`，SSH 时只用 `user` 部分。

### 规则 3：临时文件位置

- **Windows**：`$env:TEMP`（通常 `C:\Users\<用户>\AppData\Local\Temp`）
- **Linux**：`/tmp`

---

## 命令模板

### 模板 R1：执行 shell 命令

```bash
sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP "adb shell 命令"
```

### 模板 R2：抓取 logcat

**Linux 平台**：
```bash
sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP "adb logcat -d > /tmp/logcat.txt"
sshpass -p '密码' scp -o StrictHostKeyChecking=no 用户名@IP:/tmp/logcat.txt /tmp/logcat.txt
cat /tmp/logcat.txt
```

**Windows 平台**：
```bash
sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP "adb logcat -d > %TEMP%\logcat.txt"
WIN_TEMP=$(sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP 'echo %TEMP%' | tr -d '\r' | tr '\\' '/')
sshpass -p '密码' scp -o StrictHostKeyChecking=no "用户名@IP:${WIN_TEMP}/logcat.txt" /tmp/logcat.txt
cat /tmp/logcat.txt
```

### 模板 R3：推送文件到设备

**Linux 平台**：
```bash
sshpass -p '密码' scp -o StrictHostKeyChecking=no <本地文件> 用户名@IP:/tmp/
sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP "adb push /tmp/<文件名> <设备路径>"
```

**Windows 平台**：
```bash
WIN_TEMP=$(sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP 'echo %TEMP%' | tr -d '\r' | tr '\\' '/')
sshpass -p '密码' scp -o StrictHostKeyChecking=no <本地文件> "用户名@IP:${WIN_TEMP}/"
sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP "adb push %TEMP%\<文件名> <设备路径>"
```

### 模板 R4：从设备拉取文件

**Linux 平台**：
```bash
sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP "adb pull <设备路径> /tmp/<文件名>"
sshpass -p '密码' scp -o StrictHostKeyChecking=no 用户名@IP:/tmp/<文件名> <本地路径>
```

**Windows 平台**：
```bash
WIN_TEMP=$(sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP 'echo %TEMP%' | tr -d '\r' | tr '\\' '/')
sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP "adb pull <设备路径> %TEMP%\<文件名>"
sshpass -p '密码' scp -o StrictHostKeyChecking=no "用户名@IP:${WIN_TEMP}/<文件名>" <本地路径>
```

### 模板 R5：重启设备并捕获启动日志

**Linux 平台**：
```bash
sshpass -p '密码' ssh -o StrictHostKeyChecking=no 用户名@IP "bash -c 'adb reboot; sleep 5; timeout 60 adb logcat > /tmp/boot_log.txt'"
sshpass -p '密码' scp -o StrictHostKeyChecking=no 用户名@IP:/tmp/boot_log.txt /tmp/boot_log.txt
cat /tmp/boot_log.txt
```

---

## 参数替换说明

执行命令前，替换以下占位符：
- `密码` → SSH 密码
- `用户名` → SSH 用户名
- `IP` → 远程 PC IP 地址
- `命令` → 要执行的 adb shell 命令
- `<本地文件>` → 本地文件路径
- `<设备路径>` → Android 设备上的路径
