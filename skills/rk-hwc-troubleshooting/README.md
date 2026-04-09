# Rockchip HWC 问题排查

Rockchip Android 图形系统 HWC（硬件合成器）模块问题排查技能，基于本地经验库进行渐进式问题定位

## 适用场景

Android系统相关问题：

- 显示异常：花屏、黑屏、闪屏、卡顿
- 崩溃问题：应用 ANR、图形驱动崩溃
- 性能问题：帧率低、掉帧、刷新卡顿
- 功能问题：HDMI 热插拔、双屏显示异常

## 适用平台

- **芯片**：RK3528 / RK3562 / RK3566 / RK3568 / RK3576 / RK3588
- **系统**：Android 11 / 12 / 13 / 14 / 15

## 核心模块

| 模块 | 职责 | 常见问题 |
|------|------|----------|
| **SurfaceFlinger** | 显示合成器 | 镜像输出、梯形矫正 |
| **HWC** | 硬件合成器 | HDMI 热插拔、双屏显示 |
| **GPU** | 3D 渲染/2D 合成 | Fence Timeout、性能不足 |
| **RGA** | 2D 图形加速 | 颜色偏差、对齐问题 |
| **HWUI** | 硬件 UI 渲染 | APK 崩溃 |
| **Gralloc** | 图形内存分配 | 内存泄漏、横条纹 |

## 问题描述模板

```
设备型号：[XX]
芯片型号：[RK3588]
系统版本：Android [X.Y.Z]

问题现象：[花屏/黑屏/闪屏/卡顿]
发生场景：[看视频/录屏/启动时]
触发操作：[滑动/点击等]

预期结果：[显示正常]
实际结果：[实际异常现象]
```

## 参考文档

| 文件 | 说明 |
|------|------|
| `module_overview.md` | 图形模块架构概览 |
| `HwcDumpBuffer.md` | 送显图像数据抓取调试 |
| `HwcSideband2.md` | Sideband 2.0 调试功能 |
| `HwcDebug.md` | DRM HWC2 调试命令 |

## 文件结构

```
rk-hwc-troubleshooting/
├── SKILL.md              # Skill 定义文档
├── README.md             # 本文件
└── references/
    ├── module_overview.md    # 模块架构
    ├── HwcDumpBuffer.md      # 图像抓取
    ├── HwcSideband2.md       # 调试功能
    └── HwcDebug.md           # 调试命令
```

## 调用示例

- RK3588-Android12设备显示花屏，我应该怎么排查？
- HDMI 黑屏无显示，怎么处理？
- 如何抓取Android14上的 HWC 日志？