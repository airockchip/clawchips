# clawchips (OpenClaw plugin)

ClawChips 是一个 OpenClaw 插件，用于在 OpenClaw 对话中根据任务自动选择本地或云端模型，并提供 Dashboard 查看任务路由与记忆信息。

## ClawChips 安装步骤

### 1. 安装 OpenClaw

请参考 [OpenClaw 官方文档](https://docs.openclaw.ai/install)安装配置 OpenClaw，已安装可以跳过。

```bash
npm install -g openclaw@2026.3.24
openclaw onboard --install-daemon
```

备注：当前在 OpenClaw 2026.3.24 (cff6dc9) 版本上测试通过。

### 2. 安装 ClawChips

#### 获取安装包

方式 1：直接下载发布包

访问 [release 页面](https://github.com/airockchip/clawchips/releases)下载。

方式 2：自己构建插件包

首次构建需要安装一些依赖包：

```bash
git clone https://github.com/airockchip/clawchips
cd clawchips-plugin/
npm install
```

后面每次只要在工程根目录执行如下命令打包即可：

```bash
bash scripts/package_dist.sh
```

将 `dist/clawchips.zip` 拷贝到开发板安装。

#### 安装插件

安装命令如下：

```bash
openclaw plugins install clawchips.zip
```

#### 初始化配置

根据提示执行如下命令进行初始化配置：

```bash
node ~/.openclaw/extensions/clawchips/scripts/setup.mjs
```

### 3. 重启 OpenClaw

```bash
openclaw gateway restart
```

启动后即可访问 Dashboard Web 页面，访问 URL：`http://<ip>:18789/plugins/clawchips/dashboard`

## 使用指南

### 测试路由功能

直接和 OpenClaw 对话后，可以查看 Dashboard 中的 `Tasks` 页面。

这里也可以对结果进行标记，标记后的任务可以在 Dashboard 中的 `Memory` 页面查看。

### 对话指令

如果对路由结果不满意，也可以直接在对话中添加 `@` 开头的指令来直接选择模型或者路由到哪层，当前支持以下指令。

- `@model(model-id)`

例如：

```text
@model(Qwen3.6-Plus) 请写一个可以收发邮件的 SKILL
```

- `@local` / `@cloud`

例如：

```text
@local 你好
```

```text
@cloud 请写一个可以收发邮件的 SKILL
```

指令设置之后会被记住，影响下次的选择，可以在 Dashboard 中的 `Memory` 页面查看。
