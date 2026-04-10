<div align="center">
  <img src="res/logo.png" width="300" alt="ClawChips logo" />

  <h1 align="center"><strong style="color:rgb(202, 31, 31);">ClawChips</strong></h1>

**ClawChips**: 面向端侧部署优化OpenClaw的开源解决方案

**[English](./README.md) | 中文**

</div>

---

**说明**：当前版本为v0.5.0技术预览版，正式版即将更新，敬请期待。

## 关于 ClawChips

ClawChips开源项目是一套面向端侧部署和优化OpenClaw的开源参考解决方案。提供了智能端云路由网关，一系列端侧好用的SKILL，可视化Dashboard等功能，优化在端侧平台使用OpenClaw的体验。

| 功能 | 说明 |
| --- | --- |
| **本地 / 云端智能路由** | 智能识别任务复杂度，在本地模型与云端模型之间自动分发请求，节省Token用量 |
| **反馈驱动记忆** | 支持将请求历史与反馈标签写入记忆库，用于优化后续相似请求的路由决策。 |
| **SKILLS** |  内置一系列端侧好用的SKILL，并且将持续不断丰富 |
| **Dashboard** | 提供路由统计、运行时配置、Provider 管理、反馈标注和记忆查看等功能。 |

---

## 端云路由

ClawChips 内置一个本地运行的智能路由网关，位于 OpenClaw 与多个模型后端之间。

- 智能识别任务复杂度，支持任务请求在本地 / 云端路由本地智能路由
- 支持记忆路由配合使用，持续优化路由决策
- 支持Dashboard配置界面

```text
┌─────────────────┐     ┌──────────────────────┐               ┌─────────────────────────┐
│    Channels     │────▶│       OpenClaw       │──────────────▶│          CLOUD          │
│                 │     │     Gateway/App      │────────┐      │ ┌─────────────────────┐ │
└─────────────────┘     └──────────────────────┘        │      │ │        Qwen         │ │
                               │      ▲                 │      │ └─────────────────────┘ │
                               │      │                 │      │ ┌─────────────────────┐ │
                               │      │                 │      │ │        Kimi         │ │
                               ▼      │                 │      │ └─────────────────────┘ │
                        ┌─────────────────────┐         │      │ ┌─────────────────────┐ │
                        │      ClawChips      │         │      │ │         GLM         │ │
                        │     LocalRouter     │         │      │ └─────────────────────┘ │
                        └─────────────────────┘         │      │ ┌─────────────────────┐ │
                                                        │      │ │    OpenAI / More    │ │
                                                        │      │ └─────────────────────┘ │
                                                        │      └─────────────────────────┘
                                                        │
                                                        │      ┌─────────────────────────┐
                                                        └─────▶│          LOCAL          │
                                                               │      RKLLM Server       │
                                                               └─────────────────────────┘
```

---

## Dashboard 功能

网关提供一个Dashboard能够方便进行配置，使用演示如下：

![Dashboard 预览](res/dashboard.gif)

---

## SKILLS

在[skills](./skills)目录内置精选端侧芯片平台的好用的SKILL，后续将不断适配更新丰富，欢迎各位开发者提交分享。

## 安装指南

### 环境要求

- RK3588+RK1828 Debian/Ubuntu操作系统

### 快速开始

完整的开发板环境搭建和安装请参考[ClawChips_Quick_Start](./ClawChips_Quick_Start.md)

### ClawChips安装步骤

#### 1. 安装OpenClaw

请参考 [OpenClaw 官方文档](https://docs.openclaw.ai/install)安装配置OpenClaw，已安装可以跳过

```
npm install -g openclaw@2026.3.24
openclaw onboard --install-daemon
```

备注：当前在OpenClaw 2026.3.24 (cff6dc9)版本上测试通过

#### 2. 安装ClawChips

- 获取安装包

方式1：直接下载发布包

访问[release页面](https://github.com/airockchip/clawchips/releases)下载

方式2：自己构建插件包

首次构建需要安装一些依赖包
```bash
git clone https://github.com/airockchip/clawchips
cd clawchips-plugin/
npm install
```

后面每次只要在工程根目录执行如下命令打包即可：
```
bash scripts/package_dist.sh
```

将`dist/clawchips.zip`拷贝到开发板安装，

- 安装

安装命令如下：

```
openclaw plugins install clawchips.zip
```

- 初始化配置

根据提示执行如下命令进行初始化配置

```
node ~/.openclaw/extensions/clawchips/scripts/setup.mjs
```

#### 3. 安装记忆路由依赖（可选）

如果开启记忆路由功能，还需要按照如下在RK3588开发板本地部署一个embedding模型服务（如果使用提供的固件有带则不需要安装，只需确认一下服务是否正常运行）

```
cd /userdata/
curl -fsSL https://raw.githubusercontent.com/airockchip/clawchips/main/scripts/install_memory_router_deps.sh | bash -s --
```

确认服务是否正常运行
```
journalctl -u embedding-rknn-server.service
# 有看到如下日志表示服务有启动成功
I:        Application startup complete.
I:        Uvicorn running on http://0.0.0.0:18080 (Press CTRL+C to quit)
```

#### 4. 重启OpenClaw

```
openclaw gateway restart
```

启动后即可访问Dashboard Web页面，访问URL： `http://<ip>:18789/plugins/clawchips/dashboard`

## 使用指南

### 测试路由功能

直接和OpenClaw对话后，可以查看dashboard中的`Tasks`页面

![Dashboard Tasks](res/dashboard-tasks.png)

这里也可以对结果进行标记，标记后的任务可以在dashboard中的`Memory`页面查看

![Dashboard Memory](res/dashboard-memory.png)

### 对话指令

如果对路由结果不满意，也可以直接在对话中添加`@`开头的指令来直接选择模型或者路由到哪层，当前支持以下指令

- @model(model-id)

例如：

```
@model(Qwen3.6-Plus) 请写一个可以收发邮件的SKILL
```

- @local / @cloud

例如：

```
@local 你好
```

```
@cloud 请写一个可以收发邮件的SKILL
```

指令设置之后会被记住，影响下次的选择，可以在dashboard中的`Memory`页面查看

## 常见问题

- 局域网无法访问Dashboard web页面

openclaw的gateway配置可以参考如下进行修改，但是安全性会降低需要注意：

```
  "gateway": {
    "port": 18789,
    "mode": "local",
    "bind": "lan",
    "controlUi": {
      "allowedOrigins": [
        "http://localhost:18789",
        "http://127.0.0.1:18789",
        "http://192.168.31.82:18789"
      ],
      "dangerouslyAllowHostHeaderOriginFallback": true,
      "allowInsecureAuth": true,
      "dangerouslyDisableDeviceAuth": true
    },
  }
```

## 诚邀开发者“一起玩出百样精彩”
为全力支撑开发者高效开发与创新，我们特别推出专属共创支持机制：
您可以扫描图中二维码申请RK3588+RK1828开发套件的无偿借用权益，我们将根据填写质量及适配情况，为企业和开发者提供为期一个月的套件体验机会，方便大家能够更便捷地体验ClawChips的全量能力、打磨优质技能。

![报名二维码](res/baoming.png)

## 参考项目

- [OpenClaw](https://github.com/openclaw/openclaw)
- [EdgeClaw](https://github.com/OpenBMB/EdgeClaw)
- [UncommonRoute](https://github.com/CommonstackAI/UncommonRoute)
- [ClawRouter](https://github.com/BlockRunAI/ClawRouter)
- [LLMRouter](https://github.com/ulab-uiuc/LLMRouter)

---

## License

MIT