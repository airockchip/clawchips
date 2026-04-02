# **HwcDebug功能说明**

文件标识：RK-PC-YF-xxxx

发布版本：V1.0.0

日期：2026-3-27

文件密级：□绝密   □秘密   □内部资料   ■公开

---

**免责声明**

本文档按“现状”提供，瑞芯微电子股份有限公司（“本公司”，下同）不对本文档的任何陈述、信息和内容的准确性、可靠性、完整性、适销性、特定目的性和非侵权性提供任何明示或暗示的声明或保证。本文档仅作为使用指导的参考。

由于产品版本升级或其他原因，本文档将可能在未经任何通知的情况下，不定期进行更新或修改。

**商标声明**

“Rockchip”、“瑞芯微”、“瑞芯”均为本公司的注册商标，归本公司所有。

本文档可能提及的其他所有注册商标或商标，由其各自拥有者所有。

**版权所有** **© 2025** **瑞芯微电子股份有限公司**

超越合理使用范畴，非经本公司书面许可，任何单位和个人不得擅自摘抄、复制本文档内容的部分或全部，并不得以任何形式传播。

瑞芯微电子股份有限公司

Rockchip Electronics Co., Ltd.

地址：     福建省福州市铜盘路软件园A区18号

网址：     [www.rock-chips.com](http://www.rock-chips.com)

客户服务电话： +86-4007-700-590

客户服务传真： +86-591-83951833

客户服务邮箱： [fae@rock-chips.com](mailto:fae@rock-chips.com)

---

**前言**

本文档主要介绍 DrmHwc2 版本的调试命令与相关案例分析，为相关开发者提供显示问题的调试手段。

**产品版本**

| **芯片名称** | **系统版本**     | **DrmHwc2版本**|
| ------------ | ---------------- |--|
| RK3588       | Android  12 / 13 / 14 / 15 / 16| v1.6.12|
| RK3576       | Android  9.0 / 14 / 15 / 16 | v1.6.12|
| RK3572       | Android  16 | v1.6.12|
| RK3568       | Android  11 / 12 / 13 / 14 / 15 / 16 | v1.6.12|
| RK3566       | Android  11 / 12 / 13 / 14 / 15 / 16 | v1.6.12|
| RK3562       | Android  13 / 14 / 15 / 16 | v1.6.12|
| RK3326       | Android  14 / 15 / 16 | v1.6.12|
| RK3399       | Android  14 / 15 / 16| v1.6.12|
| RK3528       | Android  9.0 / 14 / 15 | v1.6.12|
| RK3538       | Android  9.0 / 14 / 15 | v1.6.12|

**读者对象**

本文档主要适用于以下工程师：

- 技术支持工程师
- 软件开发工程师

**修订记录**

| **日期**   | **版本** | **作者**  | **修改说明** |
| ---------- | -------- | --------- | ------------ |
| 2026/03/27 | 1.0.0    | 李斌 | 初始版本     |

**目 录**

[TOC]

## 1 显示问题分析建议

### 1.1 版本升级

如果遇到明确是 DrmHwc2 内部的问题，优先建议升级DrmHwc2的版本，升级后再复测问题是否存在，升级方法如下：

DrmHwc2 最新版本可通过对外最新的SDK下载，或者通过Redmine补丁简报发布获取，地址：https://redmine.rock-chips.com/issues/450567

压缩包内容如下，请阅读README.md 进行版本验证与升级。
```shell
lb@lb-pc:~/hwc-release-1.5.178/hwc-release-master$ tree  -L 5  
.
├── CHANGELOG.md
├── LICENSE
├── README.md
└── v1.5.178
    ├── hwc_1.5.178.tar.xz  ## 压缩包解压内容如下，包含各平台的源码与预编译的so文件
    └── hwc_1.5.178
        ├── 1-readme.txt
        ├── 2-source_code
        │   ├── drmhwc2-1.5.178.tar.gz
        │   └── hwc3_aidl.tar.gz
        ├── 3-patch
        │   └── framework_and_interface_patchs.tar.gz
        └── 4-prebuilt
            ├── android11
            │   ├── rk3566_r
            │   └── rk3568_r
            ├── android12
            │   ├── rk3566_s
            │   ├── rk3568_s
            │   └── rk3588_s
            ├── android13
            │   ├── rk3528_box
            │   ├── rk3562_t
            │   ├── rk3566_t
            │   ├── rk3568_t
            │   └── rk3588_t
            ├── android14
            │   ├── rk3326_u
            │   ├── rk3399_u
            │   ├── rk3528_box
            │   ├── rk3562_u
            │   ├── rk3566_u
            │   ├── rk3568_u
            │   ├── rk3576_u
            │   ├── rk3588_u
            │   └── vendor
            └── android15
                ├── rk3326_u
                ├── rk3399_u
                ├── rk3562_u
                ├── rk3566_u
                ├── rk3568_u
                ├── rk3576_u
                ├── rk3588_u
                └── vendor
        
```

### 1.2 显示相关模块与调试方法说明
#### 1.2.1 SurfaceFlinger 相关

dumpsys SurfaceFlinger 是 Android 系统调试工具，用于查看 SurfaceFlinger 服务的状态和详细信息。SurfaceFlinger 是 Android 的图形合成器，负责将各个应用程序和系统 UI 的图层合成并显示到屏幕上。

**命令简介**
```shell
adb shell dumpsys SurfaceFlinger > sf.log
```
这是最基础的用法，用于打印 SurfaceFlinger 服务的当前状态，包括：

- 显示屏信息（例如分辨率、刷新率等）
- 当前图层（layer）列表
- 合成输出状态和帧信息
- 渲染器合成方式（如 GPU、硬件叠加等）

输出内容较多，通常用于调试屏幕显示、图形合成和性能分析。

**常见应用场景:**

✔ 观察当前图层堆栈和渲染情况  
✔ 分析 UI 图形渲染性能瓶颈  
✔ 判断图层合成是由 GPU 还是硬件叠加完成  
✔ 收集游戏或应用的实时 FPS 统计  
✔ 多屏或外接显示调试

#### 1.2.2 DrmHwc2 相关
DrmHwc2 是 Rockchip 平台上基于 DRM/KMS（Direct Rendering Manager / Kernel Mode Setting） 的 Hardware Composer 2.0（HWC2）实现。DrmHwc2 是 Android 到 DRM 的桥梁，负责把 SurfaceFlinger 的图层转化为 DRM atomic 提交，实现高性能硬件合成显示。

它是 Android 显示架构中的核心组件之一，负责：

- 管理图层（Layer）
- 决定合成策略（GPU 还是 硬件合成）
- 控制显示输出（主屏/副屏）
- 完成 DRM 驱动交互，并提交显示请求到底层驱动，实现屏幕显示；

Android 显示框架中主要位于如下位置：

App → SurfaceFlinger → HWC2(DrmHwc2) → DRM/KMS → 显示硬件

**命令简介**
```shell
## 查看 DrmHwc2 版本号
adb shell getprop vendor.ghwc.version  ## 典型输出为 HWC2-1.5.180
## 查看当前设备的连接状态, 与主副屏的配置情况
adb shell getprop | grep vendor.hwc.device
## 关闭DrmHwc2合成策略，采用GPU合成方式
adb shell setprop vendor.hwc.compose_policy 0
## 恢复DrmHwc2合成策略， 根据底层硬件支持情况使用 Device 合成
adb shell setprop vendor.hwc.compose_policy 1
## 打开 DrmHwc2 调试日志命令, debug等级日志
adb shell setprop vendor.hwc.log debug
## 打开 DrmHwc2 调试日志命令, all等级日志
adb shell setprop vendor.hwc.log all
```

**部分命令详解**

- 查看当前设备的连接状态, 与主副屏的配置情况
    ```shell
    adb root
    adb remount
    adb shell getprop | grep vendor.hwc.device
    ```
    RK3588-EVB1 Android 15 DSI单屏连接情况输出如下：
    ```shell
    $ adb shell getprop | grep vendor.hwc.device
    ## DSI 分配 DisplayId=0, DRM 设备名称为 DSI-1，CRTC 分配ID=146，连接状态为 connected
    [vendor.hwc.device.display-0]: [DSI-1:146:connected]  
    ## 副屏配置的DRM设备名称为 HDMI-A,eDP
    [vendor.hwc.device.extend]: [HDMI-A,eDP]
    ## 主屏配置的DRM设备名称为 DSI，实际系统注册DSI屏幕为主屏，符合预期
    [vendor.hwc.device.primary]: [DSI]
    ```

    RK3588-EVB1 Android 15 DSI/HDMI-A双屏屏连接情况输出如下：
    ```shell
    $ adb shell getprop | grep vendor.hwc.device
    ## DSI 分配 DisplayId=0, DRM 设备名称为 DSI-1，CRTC 分配ID=146，连接状态为 connected
    [vendor.hwc.device.display-0]: [DSI-1:146:connected]  
    ## DSI 分配 DisplayId=1, DRM 设备名称为 HDMI-A-1，CRTC 分配ID=73，连接状态为 connected
    [vendor.hwc.device.display-1]: [HDMI-A-1:73:connected]
    ## 副屏配置的DRM设备名称为 HDMI-A,eDP
    [vendor.hwc.device.extend]: [HDMI-A,eDP]
    ## 主屏配置的DRM设备名称为 DSI，实际系统注册DSI屏幕为主屏，符合预期
    [vendor.hwc.device.primary]: [DSI]
    ```

    若HDMI-A拔出，则信息更新为如下：
    ```shell
    $ adb shell getprop | grep vendor.hwc.device
    [vendor.hwc.device.display-0]: [DSI-1:146:connected]
    ## 连接状态为 更新为 disconnected
    [vendor.hwc.device.display-1]: [HDMI-A-1:73:disconnected]
    ```

    若屏幕进入休眠，则信息更新如下：

    ```shell
    $ adb shell getprop | grep vendor.hwc.device
    ## 连接状态更新为 off
    [vendor.hwc.device.display-0]: [DSI-1:146:off]
    [vendor.hwc.device.display-1]: [HDMI-A-1:73:disconnected]
    ```
- 关闭/打开DrmHwc2合成策略命令
    ```shell
    adb root
    adb remount
    adb shell setprop vendor.hwc.compose_policy 0 ## 关闭HWC
    adb shell setprop vendor.hwc.compose_policy 1 ## 打开HWC
    ```

    系统的策略合成方式可以通过以下命令打印：
    ```shell
    adb shell 'dumpsys SurfaceFlinger | grep -A 40 "h/w composer state"'
    ```
    可通过 sf-type  |  hwc-type 表格查看对应图层的合成方式，如下节选信息如下：
    ```shell
    ------+-----+-----------+-----------+------------
      id  |  z  |  sf-type  |  hwc-type | name       
    ------+-----+-----------+-----------+------------
     0005 | 000 |    Device |    Device | Wallpaper#3(BLAST Consumer)3 | 0x166000000a6
     0006 | 001 |    Device |    Device | VRI[QuickstepLauncher]#1(BLAST Consumer)1 | 0x166000000bc
     0004 | 002 |    Device |    Device | VRI[StatusBar]#2(BLAST Consumer)2 | 0x166000000a5
     0007 | 003 |    Device |    Device | VRI[NavigationBar0]#0(BLAST Consumer)0 | 0x16600000099
    ------+-----+-----------+-----------+------------
    ```
    其中 sf-type 表示SurfaceFlinger 要求 HWC 使用的合成策略，hwc-type 表示HWC最终选定的合成策略，上图信息为 vendor.hwc.compose_policy=1的输出情况。

    vendor.hwc.compose_policy=0 配置的话，HWC会将所有合成策略设置为Client，输出合成策略信息如下：
    ```shell
    ------+-----+-----------+-----------+------------
      id  |  z  |  sf-type  |  hwc-type | name       
    ------+-----+-----------+-----------+------------
     0005 | 000 |    Client |    Client | Wallpaper#3(BLAST Consumer)3 | 0x166000000a6
     0006 | 001 |    Client |    Client | VRI[QuickstepLauncher]#1(BLAST Consumer)1 | 0x166000000cb
     0004 | 002 |    Client |    Client | VRI[StatusBar]#2(BLAST Consumer)2 | 0x166000000a3
     0007 | 003 |    Client |    Client | VRI[NavigationBar0]#0(BLAST Consumer)0 | 0x1660000009a
    ------+-----+-----------+-----------+------------
    ```

    由于图层合成的处理后端不一样，我们通常可以利用合成方式的切换，来辅助验证另外一条通路的合成路径是否存在问题。比如说：
    - HWC关闭，问题复现，HWC使能，问题消失：说明Client合成通路大概率可能存在异常导致问题；
    - HWC使能，问题复现，HWC关闭，问题消失：说明Device合成通路大概率可能存在异常导致问题；


- DrmHwc2 调试日志命令
    ```shell
    ## 打开 DrmHwc2 调试日志命令, debug等级日志
    adb shell setprop vendor.hwc.log debug
    ## 打开 DrmHwc2 调试日志命令, all等级日志
    adb shell setprop vendor.hwc.log all
    ## 调试命令打开后，则可通过 logcat 抓打印HWC模块的调试日志
    adb shell logcat -c ;adb shell logcat > hwc.log
    ```

    DrmHwc2 调试日志需要结合源码分析，故建议客户遇到显示问题优先打印上述调试日志，并且详细说明问题复现方法，提供现象视频，供RK工程师分析即可。

#### 1.2.3 VOP 相关

Vop 是最终 DrmHwc2 通过 DRM 接口配置的硬件相关寄存器信息，当前最建议客户抓打印的命令如下：
```shell
## VOP 硬件图层与屏幕显示模式配置信息查询命令
adb shell cat /d/dri/0/summary
## VOP 硬件寄存器信息查询命令
adb shell cat /d/dri/0/regs
```

**命令详解**

```shell
## VOP 硬件图层与屏幕显示模式配置信息查询命令
adb shell cat /d/dri/0/summary
```
RK3588-EVB1 Android 15 DSI单屏连接情况输出如下：
```shell
$ adb shell cat /d/dri/0/summary               
Video Port0: DISABLED
Video Port1: DISABLED
Video Port2: DISABLED
Video Port3: ACTIVE  ## DSI 使能在 VP3 接口上
    Connector:DSI-1     Encoder: DSI-518
        bus_format[100a]: RGB888_1X24  ## OutputFormat格式类型，RGB888/YUV420
        overlay_mode[0] output_mode[0] SDR[0] color-encoding[BT.709] color-range[Full]  ## Connector输出的色域空间信息
    Display mode: 1080x1920p60 ## 分辨率模式
        dclk[132000 kHz] real_dclk[132000 kHz] aclk[850000 kHz] type[48] flag[a]
        H: 1080 1095 1099 1129
        V: 1920 1935 1937 1952
        Fixed H: 1080 1095 1099 1129
        Fixed V: 1920 1935 1937 1952
    Esmart3-win0: ACTIVE ## Esmart 不支持 FBCD 压缩格式
        win_id: 11
        format: AB24 little-endian (0x34324241) pixel_blend_mode[0] glb_alpha[0xff] ## 格式与 Alpha混合参数
        color: SDR[0] color-encoding[BT.709] color-range[Full] ## 色域相关信息
        rotate: xmirror: 0 ymirror: 0 rotate_90: 0 rotate_270: 0 ## Transform 配置信息
        csc: y2r[0] r2y[0] csc mode[0] ## csc 配置信息
        zpos: 2 ## z坐标
        src: pos[0, 0] rect[1080 x 48]  ## src crop 信息，依次是 x/y offset, width x height
        dst: pos[0, 0] rect[1080 x 48]  ## dst frame 信息，依次是 x/y offset, width x height
        buf[0]: addr: 0x0000000002752000 pitch: 4352 offset: 0 ## 格式各通道的首地址信息，pitch 是 bytestride 信息，offset为基于首地址的偏移
    Cluster3-win0: ACTIVE ## 支持 FBCD 压缩格式
        win_id: 6
        format: AB24 little-endian (0x34324241)_AFBC-16x16 pixel_blend_mode[0] glb_alpha[0xff]
        color: SDR[0] color-encoding[BT.709] color-range[Full]
        rotate: xmirror: 0 ymirror: 0 rotate_90: 0 rotate_270: 0
        csc: y2r[0] r2y[0] csc mode[0]
        zpos: 0
        src: pos[33, 58] rect[655 x 1164]
        dst: pos[0, 0] rect[1080 x 1920]
        buf[0]: addr: 0x000000000187b000 pitch: 5120 offset: 0
    Cluster3-win1: ACTIVE
        win_id: 7
        format: AB24 little-endian (0x34324241)_AFBC-16x16 pixel_blend_mode[0] glb_alpha[0xff]
        color: SDR[0] color-encoding[BT.709] color-range[Full]
        rotate: xmirror: 0 ymirror: 0 rotate_90: 0 rotate_270: 0
        csc: y2r[0] r2y[0] csc mode[0]
        zpos: 1
        src: pos[0, 0] rect[1080 x 1920]
        dst: pos[0, 0] rect[1080 x 1920]
        buf[0]: addr: 0x0000000001ed4000 pitch: 4352 offset: 0
    Esmart3-win1: ACTIVE
        win_id: 11
        format: AB24 little-endian (0x34324241) pixel_blend_mode[0] glb_alpha[0xff]
        color: SDR[0] color-encoding[BT.709] color-range[Full]
        rotate: xmirror: 0 ymirror: 0 rotate_90: 0 rotate_270: 0
        csc: y2r[0] r2y[0] csc mode[0]
        zpos: 2
        src: pos[0, 0] rect[1080 x 96]
        dst: pos[0, 1824] rect[1080 x 96]
        buf[0]: addr: 0x00000000026ec000 pitch: 4352 offset: 0
```

VOP寄存器信息结合VOP硬件寄存器信息，故建议客户遇到显示问题优先打印上述调试日志，并且详细说明问题复现方法，提供现象视频，供RK工程师分析即可。
```shell
## VOP 硬件寄存器信息查询命令
adb shell cat /d/dri/0/regs
```