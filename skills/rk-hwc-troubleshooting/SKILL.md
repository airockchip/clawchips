---
name: rk-hwc-troubleshooting
description: Rockchip Android 图形系统 HWC 模块问题排查技能。当用户遇到HWC模块问题或显示问题包括显示异常、花屏、黑屏、卡顿、崩溃等问题时触发。此技能可检索本地经验库中的解决方案和参考案例，并提供问题排查方案。
license: Classmate terms in LICENSE.txt
---

# RK HWC Troubleshooting Skill

Rockchip Android 图形系统 HWC 模块问题排查技能，基于内部经验库进行渐进式问题定位。

## 技能档案

| 字段 | 说明 |
|------|------|
| **任务类型** | 问题排查 / 诊断 / 解决方案 |
| **适用平台** | Rockchip RK3528/RK3562/RK3566/RK3568/RK3576/RK3588 |
| **运行环境** | Android 11/12/13/14/15 |
| **调用频率** | 当用户描述显示问题时触发 |
| **技能复杂度** |medium（支持基础 SDK 代码生成和工作流编排） |
| **状态** | 开发中 |
| **最后更新** | 2026-03-27 |

---

## 数据源

经验库保存在本地，用于检索解决方案和参考案例。

### 核心参考文档

| 文件 | 说明 | 用途 |
|------|------|------|
| `module_overview.md` | 图形模块架构概览 | 了解系统架构和各模块职责 |
| `HwcDumpBuffer.md` | 送显图像数据抓取调试 | 确认 HWC 输出是否异常 |
| `HwcSideband2.md` | Sideband 2.0 调试功能 | 日常功能调试，性能追踪 |
| `HwcDebug.md` | DRM HWC2 调试命令 | 显示问题调试命令和案例分析 |

### 参考文档结构

```
references/
├── module_overview.md          # 模块架构 + 调试命令速查
├── HwcDumpBuffer.md            # 送显图像数据抓取与诊断
├── HwcSideband2.md             # Sideband2 调试方案
└── HwcDebug.md                 # DRM HWC2 调试命令和用例
```

### 模块职责

| 模块 | 职责 | 常见问题 |
|------|------|----------|
| **SurfaceFlinger** | 显示合成器 | 镜像输出、梯形矫正、开机动画显示不全 |
| **HWC** | 硬件合成器 | HDMI 热插拔异常、双屏显示问题、分辨率切换失败 |
| **GPU** | 3D 渲染/2D 合成 | Fence Timeout、驱动初始化失败、性能不足 |
| **RGA** | 2D 图形加速 | 颜色偏差、对齐约束、内存越界 |
| **HWUI** | 硬件 UI 渲染引擎 | APK 崩溃、关闭硬件加速 |
| **Gralloc** | 图形内存分配 | 内存泄露、异常横条纹 |

**重要名词说明：**

- AFBC 压缩技术：AFBC 是 Arm Frame Buffer Compression 的缩写，是由 Arm 提出的一种无损图像帧缓冲压缩技术，主要用于 GPU、显示控制器（Display Controller）、视频编解码器等图形子系统中，它的核心目标是以下几点：
  - 减少读取图形缓冲区的内存带宽占用，典型格式的内存带宽占用降低数据为 RGB格式可降低50%，YUV格式可降低30%；
  - 降低功耗
  - 保持图像质量完全无损
- RFBC 压缩技术：RFBC 是 Rockchip Frame Buffer Compression 的缩写，是由 Rockchip 提出的一种无损图像帧缓冲压缩技术，主要用于RK自研IP VOP/VPU/RGA 等硬件中，它的核心目标是以下几点：
  - 减少读取图形缓冲区的内存带宽占用，典型格式的内存带宽占用降低数据为 RGB格式可降低50%，YUV格式可降低30%；
  - 降低功耗
  - 保持图像质量完全无损

---

## 典型问题场景

### 🖼️ 显示异常类

| 现象 | 可能模块 | 排查重点 |
|------|----------|----------|
| **花屏/死屏** | HWC, GPU, RGA | HWC 输出层、GPU 渲染、RGA 处理 |
| **黑屏/无显示** | HWC, GPU, VOP | 送显数据、帧缓冲、控制器 |
| **闪屏** | HWC, GPU | 摆渡异常、资源冲突 |
| **不满屏/不满栅格** | HWC, DRM | 分辨率配置、边距处理 |
| **梯形显示** | HWC, GPU | 梯形矫正参数、视角数据 |

### ⚡ 性能问题类

| 现象 | 可能模块 | 排查重点 |
|------|----------|----------|
| **屏幕刷新卡顿** | HWC, GPU | 合成率、GPU 负载、摆渡带宽 |
| **FPS 低** | HWC, GPU | 合成任务、渲染管线 |
| **掉帧/掉画面** | HWC, DRM | 资源竞争、资源不足 |

### 🔄 稳定性问题类

| 现象 | 可能模块 | 排查重点 |
|------|----------|----------|
| **应用崩溃/ANR** | GPU, HWUI, HWC | 渲染资源、状态机 |
| **输入输出不一致** | GPU, HWUI, HWC | 关键任务、数据一致性 |

### 🎯 功能缺失类

| 现象 | 可能模块 | 排查重点 |
|------|----------|----------|
| **3D 应用不可用** | GPU | Vulkan/OpenCL 驱动、版本兼容性 |

---

## 功能说明

### ✅ 支持的能力

1. **问题分析**
   - ✅ 接收用户描述的问题
   - ✅ 识别问题类型和可能模块
   - ✅ 构建问题场景

2. **方案查询**
   - ✅ 检索参考文档中的解决方案
   - ✅ 匹配相似案例
   - ✅ 提供排查步骤

3. **方案设计**
   - ✅ 生成排查命令序列
   - ✅ 提供日志分析建议
   - ✅ 给出验证方法

### ❌ 边界限制

1. **能力边界**
   - ❌ 无法直接执行设备命令
   - ❌ 无法直接上传文件
   - ❌ 无法访问云端数据

2. **适用场景**
   - ✅ Android 平台 (11/12/13/14/15)
   - ✅ Rockchip 芯片平台 (RK3528/RK3562/RK3566/RK3568/RK3576/RK3588)
   - ✅ 显示类问题：花屏、黑屏、闪屏、卡顿、崩溃、AFBC、RFBC等

3. **不支持的场景**
   - ⏸️ 非 Android 系统
   - ⏸️ 非 Rockchip 芯片平台
   - ⏸️ 音频/电源/网络等其他模块问题

---

## 使用指南

### 📝 问题描述模板

用户应提供以下信息（尽可能详细）：

**1. 设备信息**
```
设备型号：[XX]
芯片型号：[RK3588]
系统版本：Android [X.Y.Z]
```

**2. 问题现象**
```
现象描述：[例如：花屏，黑屏，闪屏等]
发生场景：[例如：看视频时，录屏时，启动时等]
触发操作：[例如：滑动，点击，/等操作]
```

**3. 复现步骤**
```
操作步骤 1：[描述第一步]
操作步骤 2：[描述第二步]
...（按实际步骤描述）
```

**4. 预期结果 vs 实际结果**
```
预期结果：[例如：显示正常画面]
实际结果：[例如：画面花屏，黑屏等]
```

**5. 附加信息**
```
已有排查：[例如：已执行某些命令]
日志/截图：[如有可提供]
```

### 🔍 排查流程

**第一步：明确问题类型**
```
- 用户问题 → 问题分类 → 可能模块
```

**第二步：检索参考案例**
```
模块 → 参考文档 → 相似案例 → 排查方案
```

**第三步：生成排查流程**
```
执行命令 → 查日志 → 分析 → 验证
```

**第四步：方案输出**
```
问题原因 → 解决方案 → 验证方法
```

---

## 调试命令速查

### 通用命令

```bash
# 抓取完整日志
$ adb logcat -c && adb logcat > log.txt

# 查看 SurfaceFlinger 状态
$ adb shell dumpsys SurfaceFlinger

# 查看 VOP 配置
$ adb shell cat /d/dri/0/summary
```

### HWC 专用

```bash
# 开启 HWC 日志
$ adb shell setprop vendor.hwc.log 511

# 关闭 HWC (使用 GPU 合成)
$ adb shell setprop vendor.hwc.enable 0
$ adb shell setprop vendor.hwc.compose_policy 0

# 查看 HWC 版本
$ adb shell getprop vendor.ghwc.version
```

### GPU 专用

```bash
# 查看 GPU 负载
$ cat /sys/devices/platform/*.gpu/utilisation

# 查看 Mali DDK 版本
$ adb shell getprop | grep mali

# 定频 GPU
$ echo 400000000 > /sys/class/devfreq/*.gpu/min_freq
$ echo 400000000 > /sys/class/devfreq/*.gpu/max_freq
```

### RGA 专用

```bash
# 查看 RGA 版本
$ cat /sys/kernel/debug/rga/rga
```

---

## 参考文档导航

### module_overview.md

**系统架构**：理解 GPU、HWC、RGA、HWUI、Gralloc 模块之间的关系

**模块职责**：
- HWC：硬件合成器，决定 GPU vs hardware overlay
- GPU：3D 渲染和 2D 合成
- RGA：2D 图像处理（缩放/旋转）
- HWUI：硬件 UI 引擎

**调试速查**：包含各类模块的调试命令和参数

### HwcDumpBuffer.md

**功能介绍**：抓取送显图像数据，确认图像是否正常

**核心操作**：
1. 设置 `vendor.dump true`
2. 触发系统刷新
3. 分析 `/data/dump/` 目录下的图像数据
4. 关闭 dump：`vendor.dump false`

**应用场景**：
- 应用原始图像异常：Launcher 花屏
- 非 VOP 处理异常：RGA 旋转后处理问题
- VOP 处理异常：WriteBack 录屏输出异常

**工具推荐**：
- YuView：解析 Raster 非压缩 YUV/RGB 格式
- 7YUV：另一种图像分析工具

### HwcSideband2.md

**Sideband 2.0 功能**：新显示性能的调试功能

**调试目的**：
- 性能追踪
- 显示状态监控
- 异常日志采集

### HwcDebug.md

**DRM HWC2 调试**：主要的调试命令与案例分析

**典型内容**：
- DRM HWC2 API 使用
- 显示问题排查用例
- 常见异常的分析和解决方法

---

## 案例说明

### 案例 1：Launcher 界面花屏

**现象**：Launcher 界面出现花屏

**排查思路**：
1. 判断异常发生层：SurfaceFlinger 日志查看异常层
2. 抓取应用原始图像数据：`HwcDumpBuffer.md`
3. 检查图像数据格式：AFBC 压缩需要转换
4. 分析图像内容：确认 GPU 渲染异常

**解决方案**：
```bash
# 关闭 AFBC 压缩
echo 'vendor.gralloc.no_afbc_for_sf_client_layer=1' >> /vendor/build.prop
echo 'vendor.gralloc.no_afbc_for_fb_target_layer=1' >> /vendor/build.prop
adb reboot
```

### 案例 2：视频旋转播放异常

**现象**：视频旋转 90 度后显示不正确

**排查思路**：
1. 查看 SurfaceFlinger 图层订单和变换信息
2. 抓取 VPU 输出图像和 RGA 处理后的图像
3. 对比 VPU 原始数据和 RGA 变换后的数据

**解决方案**：
确认 RGA 旋转后处理数据不正确，调整 RGA 配置参数。

### 案例 3：WriteBack 录屏输出异常

**现象**：录屏图像有黑边、色偏等异常

**排查思路**：
1. 开启 WriteBack 功能 `debug.sf.enable_hwc_vds=true`
2. 抓取原始图像数据和 WriteBack 图像数据
3. 对比两者差异

**解决方案**：
确认 HWC -> RGA -> SurfaceFlinger 转换过程中出现异常。

---

## 使用说明

**调用方式**：当用户描述显示问题或请求排查时触发

**输出格式**：
```markdown
## 问题分析

根据您描述的问题，可能存在以下异常：
- [问题类型]
- [可能模块]
- [相关原因]

## 排查建议

### 步骤 1：检查 [模块] 状态
```bash
[命令]
```

### 步骤 2：抓取 [调试数据]
```bash
[命令]
```

### 步骤 3：分析结果
根据输出分析异常原因...

## 参考文档

- [module_overview.md](module_overview.md)
- [HwcDumpBuffer.md](HwcDumpBuffer.md)
- [HwcSideband2.md](HwcSideband2.md)
- [HwcDebug.md](HwcDebug.md)
```
