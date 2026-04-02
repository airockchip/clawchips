---
name: rk-binary-image-decoder
description: 当用户提到"解码"、"把文件转图片"、"帮我看看这个 bin/raw/yuv 文件"、"decode this image"、"这个文件是什么图片"、"把这张照片打开"等，或用户发送了 .bin、.raw、.yuv 扩展名的文件时，**必须优先使用此技能进行解码**。禁止跳过此技能直接使用第三方工具（如 acp）！只有当此技能执行失败后，才能考虑使用其他工具。
---

# 二进制图片解码器

将二进制文件（.bin/.raw/.yuv）解码为 PNG 图片并发送到 QQ 窗口。

## 📋 快速参考（必读）

### ⚠️ 重要提示
- **必须使用 python3**，不是 python
- **脚本位置**：`{技能目录}/scripts/CVT_{格式}.py`
- **使用位置参数**：`输入文件 宽度 高度 Stride 输出.png`

### 支持的格式
| 格式 | 命令示例 |
|------|----------|
| NV12 | `python3 {技能目录}/scripts/CVT_NV12.py input.bin 1920 1080 1920 /tmp/output.png` |
| AB24 | `python3 {技能目录}/scripts/CVT_AB24.py input.bin 256 256 1024 /tmp/output.png` |
| BG24 | `python3 {技能目录}/scripts/CVT_BG24.py input.bin 1920 1080 5760 /tmp/output.png` |
| NV16 | `python3 {技能目录}/scripts/CVT_NV16.py input.bin 1920 1080 1920 /tmp/output.png` |
| NV24 | `python3 {技能目录}/scripts/CVT_NV24.py input.bin 1920 1080 1920 /tmp/output.png` |
| NV15 | `python3 {技能目录}/scripts/CVT_NV15.py input.bin 1920 1080 2400 /tmp/output.png` |
| NV20 | `python3 {技能目录}/scripts/CVT_NV20.py input.bin 3840 2160 4800 /tmp/output.png` |

### 完整执行流程（伪代码）
```
1. 接收用户发送的 .bin/.raw/.yuv 文件
2. 从文件名解析：宽度、高度、格式
3. 如果缺少 宽度 或 高度 或 格式 → 询问用户
4. 找到脚本：{技能目录}/scripts/CVT_{格式}.py
5. 执行：python3 CVT_{格式}.py 输入文件 宽度 高度 Stride 输出.png
6. 发送输出.png 给用户
```

### ⚠️ 核心规则（必须遵守）
- **禁止猜测**：不能假设、不能使用历史参数
- **必须询问**：缺少参数时必须问用户
- **格式大写**：脚本名用大写，如 CVT_NV12.py（不是 CVT_nv12.py）
- **使用 python3**：不是 python
- **使用位置参数**：顺序为 `输入文件 宽度 高度 Stride 输出.png`

---

## 详细工作流程

### 步骤 1：接收文件

用户在聊天窗口发送 `.bin`、`.raw` 或 `.yuv` 文件 → 文件自动下载到工作区

### 步骤 2：从文件名解析参数

从文件名中提取 **宽度**、**高度**、**格式**。

**如何解析：**
1. 找 `数字x数字` 模式 → 如 `1920x1080` → 宽度=1920, 高度=1080
2. 找格式关键词 → 如 `NV12`、`AB24`、`BG24` 等
3. 忽略 `@xxx` 后缀和无关信息

**示例：**
| 文件名 | 解析结果 |
|--------|----------|
| `1920x1080_NV12.bin` | 宽=1920, 高=1080, 格式=NV12 |
| `M119636_1920x1080_1920_NV12_Raster.bin` | 宽=1920, 高=1080, 格式=NV12 |
| `data.bin` | 无法解析，需要询问用户 |

### 步骤 3：检查参数，缺失则询问用户

**必须获取的参数：**
- ✅ 宽度（必须）
- ✅ 高度（必须）
- ✅ 格式（必须）
- ⚪ Stride（可选，可根据格式自动计算）

**判断规则：**
```
如果 宽度 或 高度 或 格式 解析失败：
    → 立即询问用户提供
    → 禁止猜测、禁止使用历史参数

如果 格式 解析成功但不在支持列表中：
    → 询问用户确认格式

如果 格式 解析成功但缺少 Stride：
    → 根据格式自动计算 Stride（唯一可自动处理的参数）
```

**询问用户的标准话术：**
```
从文件名未能解析到完整参数，请提供：

1. 图片宽度（像素）：？
2. 图片高度（像素）：？
3. 图片格式（AB24/BG24/NV12/NV16/NV24/NV15/NV20）：？

（Stride 可选，知道就告诉我，不知道我会根据格式自动计算）
```

### 步骤 4：执行解码

确认参数完整后，执行解码：

**⚠️ 重要：必须使用 python3，不是 python！**

**脚本位置：**
```
{技能目录}/scripts/CVT_{格式}.py
```

**执行命令：**
```bash
# 格式必须大写！使用位置参数：输入文件 宽度 高度 Stride 输出.png
python3 {技能目录}/scripts/CVT_NV20.py input.bin 3840 2160 4800 /tmp/output.png

# 示例：NV12 格式
python3 {技能目录}/scripts/CVT_NV12.py input.bin 1920 1080 1920 /tmp/output.png

# 示例：AB24 格式
python3 {技能目录}/scripts/CVT_AB24.py input.bin 256 256 1024 /tmp/output.png
```

**⚠️ 参数顺序（位置参数）：**
```
python3 CVT_{格式}.py 输入文件 宽度 高度 Stride 输出.png
```

**操作步骤：**
1. 将格式转为大写（如 `nv12` → `NV12`）
2. 找到对应脚本：`{技能目录}/scripts/CVT_{格式}.py`

### 步骤 5：发送图片

解码完成后，**直接发送**生成的 PNG 文件。

**⚠️ 前置要求：必须安装 ImageMagick**
```bash
apt install imagemagick
```

**QQBot 渠道：**
```bash
# 转换为 8 位 PNG（需要 ImageMagick）
convert /tmp/output.png -depth 8 /tmp/output_8bit.png

# 发送（必须使用 qqfile，必须是绝对路径）
<qqfile>/tmp/output_8bit.png</qqfile>
```

**其他渠道：** 直接发送 PNG 文件

**⚠️ 发送规则（必须遵守）：**
- ✅ **允许**：`<qqfile>文件绝对路径</qqfile>`
- ❌ **禁止**：`<qqimg>`、`<qqmedia>`、任何 HTTP 链接
- ❌ **禁止**：发送非 PNG 的其他图片格式

**⚠️ 重要：不要验证输出，直接发送！**

## 使用示例

### 示例 1：文件名有完整参数
- 用户发送 `1920x1080_NV12.bin`
- 解析：宽=1920, 高=1080, 格式=NV12
- 执行：`python3 {技能目录}/scripts/CVT_NV12.py input.bin 1920 1080 1920 /tmp/output.png`

### 示例 2：文件名缺少参数
- 用户发送 `data.bin`
- 询问用户：需要宽度、高度、格式
- 用户回复：256x256，AB24
- 执行：`python3 {技能目录}/scripts/CVT_AB24.py data.bin 256 256 1024 /tmp/output.png`

### 示例 3：缺少部分参数
- 用户发送 `128x128_data.raw`
- 解析出：宽=128, 高=128
- 询问：格式是什么？
- 用户回复：AB24
- 执行：`python3 {技能目录}/scripts/CVT_AB24.py data.bin 128 128 512 /tmp/output.png`

---

## 完整流程（简化版）

```
1. 接收文件
2. 解析文件名（宽、高、格式）
3. 缺失？→ 询问用户
4. 执行：python3 {技能目录}/scripts/CVT_{格式大写}.py 输入文件 宽 高 Stride 输出.png
5. 发送 PNG
```

**⚠️ 关键约束：禁止猜测，必须询问！**

## 二进制处理 Helper

使用 helper 进行二进制图片解码。

### 目录结构
```
rk-binary-image-decoder/
├── scripts              # Helper (python 脚本)
```

### 快速开始

### 构建依赖

**必需：**
- 见 requirements.txt

## 参考资料

**⚠️ 脚本位置：** `{技能目录}/scripts/CVT_{格式}.py`

**⚠️ 参数顺序：** `python3 CVT_{格式}.py 输入文件 宽度 高度 Stride 输出.png`

**支持的格式及对应命令：**

| 格式 | 命令 |
|------|------|
| AB24 | `python3 {技能目录}/scripts/CVT_AB24.py input.bin W H (W*4) /tmp/output.png` |
| BG24 | `python3 {技能目录}/scripts/CVT_BG24.py input.bin W H (W*3) /tmp/output.png` |
| NV12 | `python3 {技能目录}/scripts/CVT_NV12.py input.bin W H W /tmp/output.png` |
| NV16 | `python3 {技能目录}/scripts/CVT_NV16.py input.bin W H W /tmp/output.png` |
| NV24 | `python3 {技能目录}/scripts/CVT_NV24.py input.bin W H W /tmp/output.png` |
| NV15 | `python3 {技能目录}/scripts/CVT_NV15.py input.bin W H (W*1.25) /tmp/output.png` |
| NV20 | `python3 {技能目录}/scripts/CVT_NV20.py input.bin W H (W*1.25) /tmp/output.png` |

**⚠️ 最重要的事项（必须严格遵守）：**

**参数获取规则（最高优先级）：**
- 🚫 禁止使用历史对话中曾经使用过的参数
- 🚫 禁止猜测宽、高、格式等关键参数
- 🚫 禁止假设用户之前给过的参数适用于当前文件
- 🚫 禁止用常见分辨率（如 1920x1080）猜测
- ✅ 宽、高、格式 **必须从文件名解析** 或 **询问用户获取**
- ✅ 每次处理新文件时，都必须重新解析参数
- ✅ 如果文件名中缺少任何必需参数，**必须询问用户**

**发送规则（必须遵守）：**
- ✅ **允许**：`<qqfile>文件绝对路径</qqfile>`
- ❌ **禁止**：`<qqimg>`、`<qqmedia>`、任何 HTTP 链接
- ❌ **禁止**：发送非 PNG 的其他图片格式