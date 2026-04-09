# Rockchip ADB 连接

通过 ADB（Android Debug Bridge）连接 Rockchip 设备，支持本地有线/网络连接和远程模式

## 环境要求

- **本地主机**：adb 工具（`sudo apt install adb`）、sshpass（`sudo apt install sshpass`，远程模式需要）
- **远程 PC**：Windows/Linux + OpenSSH 服务器 + adb 工具
- **目标设备**：Rockchip 设备（RK3588/RK3568/RK3399等）

## 使用示例

### 1. 本地有线 ADB

设备通过 USB 线直连本地主机（最稳定），调用示例：

```
我有一块 Rockchip RK3588 开发板，通过 USB 连接在你所在的主机上，帮我通过 ADB 连上这台设备，检查系统状态，并抓取一段 kernel log
```

### 2. 本地网络 ADB

设备与终端在同一网段，通过网络连接，调用示例：

```bash
我有一块 Rockchip RK3588 开发板，ip地址和端口信息为<设备IP>:5555，帮我通过网络 ADB 连上这台设备，检查系统状态，并抓取一段 kernel log
```

### 3. **远程模式**

设备通过 SSH 连接到远程 Windows/Linux PC，通过 SSH 转发实现本地操作，调用示例：

```
连接实验室的 Windows 电脑，网络IP地址为 <网络IP>，并查看连接在该 Windows 电脑上的 RK3588 开发板的设备信息
```

## 文件结构

```
rk-adb/
├── SKILL.md               # Skill 定义文档
├── README.md              # 本文件
├── references
│   └── manual_commands.md #远程模式命令参考
├── scripts/
│   └── adb_helper.py      # ADB 辅助脚本
└── config/
    └── config.json        # 保存的配置（首次使用后自动生成）
```
