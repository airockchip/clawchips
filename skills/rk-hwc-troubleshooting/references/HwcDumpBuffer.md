# **HwcDumpBuffer功能说明**

文件标识：RK-PC-YF-xxxx

发布版本：V1.0.0

日期：2025-1-21

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

本文档主要介绍 HwcDumpBuffer 调试功能，可用于确认送显图像是否正常。

**产品版本**

| **芯片名称** | **系统版本**     | **DrmHwc2版本**|
| ------------ | ---------------- |--|
| RK3588       | Android  12 / 13 / 14 / 15 |v1.5.177|
| RK3576       | Android  14 / 15 |v1.5.177|
| RK3568       | Android  11 / 12 / 13 / 14 / 15 |v1.5.177|
| RK3566       | Android  11 / 12 / 13 / 14 / 15 |v1.5.177|
| RK3562       | Android  13 / 14 / 15 |v1.5.177|
| RK3326       | Android  14 / 15 |v1.5.177|
| RK3399       | Android  14 / 15 | v1.5.177|
| RK3528       | Android  14 / 15 | v1.5.177|

**读者对象**

本文档主要适用于以下工程师：

- 技术支持工程师
- 软件开发工程师

**修订记录**

| **日期**   | **版本** | **作者**  | **修改说明** |
| ---------- | -------- | --------- | ------------ |
| 2025/01/21 | 1.0.0    | GPU图形组 | 初始版本     |

**目 录**

[TOC]

## 1 概述

常见的显示的显示异常问题，通常可以从以下三个流程的图像数据进行确认：
1. 应用原始图像数据：数据可来源于GPU渲染、VPU解码、Camera/HDMI-IN采集等；
2. 非VOP处理的图像数据：基于应用原始图像数据进行的非VOP图像处理，如GPU Alpha混合/旋转处理/SR增强等图像后处理；
3. VOP处理后的图像数据（屏上显示数据）：VOP读取 **应用原始送显数据** 或者 **非VOP处理的图像数据**，上屏过程的图像处理数据；

而 HwcDumpBuffer 方法，可以将上述三个流程的图像数据以二进制文件形式写回指定目录，开发者对图像数据进行分析，确认显示问题原因；


## 2 操作步骤：
1. 进入shell并切换到root用户
    ```shell
    $ adb root
    $ adb remount
    ```
2. 关闭SELinux，避免HWC进行无法访问/data/dump/目录
    ```shell
    $ adb shell setenforce 0
    ```
3. 复现问题，开启HwcDumpBuffer
    ```shell
    $ adb shell setprop vendor.dump true
    ```
4. 触发系统刷新（操作UI或者滑动鼠标触发系统刷新），检查/data/dump目录是否存在输出文件
    ```shell
    $ adb shell ls /data/dump/
    # 输出如下：
    M00352_Z0_DEVICE_1280x1280_5120_AB24_AFBC_@Wallpaper#0(BLAST_Consumer)0_id_0x1070000023b.bin
    M00352_Z1_DEVICE_1088x1920_4352_AB24_AFBC_@VRI[Launcher3QuickStepGo]#1(BLAST_C_id_0x10700000256.bin
    M00352_Z2_DEVICE_1088x48_4352_AB24_Raster_@VRI[StatusBar]#3(BLAST_Consumer)3_id_0x10700000237.bin
    M00352_Z3_DEVICE_1088x96_4352_AB24_Raster_@VRI[NavigationBar0]#1(BLAST_Consume_id_0x10700000225.bin
    ```
5. 关闭HwcDumpBuffer
    ```shell
    # 其他非 true 字符串即可
    $ adb shell setprop vendor.dump false
    ```
6. 导出文件，利用YUView/7YUV分析图像数据
    ```bash
    $ adb pull /data/dump/
    # 若文件过大可以先利用tar/zip压缩/data/dump目录后再尝试 adb pull 文件
    ```


## 3 文件命名规则：

Dump文件命名规则如下，根据数据来源的不同，其中部分参数可能存在缺失
```bash
[Tag][frame_no]_Z[zpos]_[Comp_type]_[stride]x[h_stride]_[byte_stride]_[drm_fourcc]_[compressed]_@[Name]_id_[buffer_id].bin
```
如：M00352_Z1_DEVICE_1088x1920_4352_AB24_AFBC_@VRI[Launcher3QuickStepGo]#1(BLAST_C_id_0x10700000256.bin

| 参数   | 说明 |
| ---------- | -------- |
|Tag | 流程标志，目前分为 M (main主送显流程)、S (Sideband送显流程)、W (WriteBack流程)，M占多数情况|
|frame_no | 帧序号|
|zpos | 图层z坐标|
|Comp_type| 合成方式, 通常存在 Client（GPU） / Device(VOP) / Device(RGA)合成方式，|
|width_stride | 水平方向像素步长（虚宽），通常用于设置 YUView/7yuv width 参数|
|height_stride | 垂直方向像素步长（虚高），通常用于设置 YUView/7yuv height 参数|
|byte_stride | 水平方向字节步长，通常等于 width_stride * bpp|
|drm_fourcc | drm_fourcc 格式，可参考：[drm_fourcc 格式定义](https://elixir.bootlin.com/linux/v6.13.2/source/include/uapi/drm/drm_fourcc.h)|
|compressed | 描述格式压缩情况，分为Raster/AFBC/RFBC，Raster为未压缩的格式，可使用使用YUView/7yuv解析，AFBC/RFBC为压缩格式无法通过YUView/7yuv解析，建议关闭压缩格式后再分析图像数据|
|Name | Layer/Buffer 名称，可用来确认图像具体来源 |
|buffer_id | Buffer ID，每一块内存的唯一标识码|

## 4 场景举例说明：

### 4.1 Luncher 界面显示异常问题（应用原始图像数据异常问题：GPU渲染异常问题）
1. 尝试复现 Luncher 界面显示异常问题，观察异常图像发生可能发生在哪个图层，图层信息可以通过以下命令获取：
    ```shell
    $ adb shell dumpsys SurfaceFlinger | grep -C 10 mFps
    # 输出如下（裁剪了部分信息）：
    DisplayId=0, Connector 417, Type = DSI-1, Connector state = DRM_MODE_CONNECTED
    NumHwLayers=4, activeModeId=3, 1080x1920p60.00, colorMode = -1, bStandardSwitchResolution=0
    ------+-----+-----------+-----------+--------------------------------+------------------------+--------+------------
    id  |  z  |  sf-type  |  hwc-type |     source crop (l,t,r,b)      |          frame         |  mFps  | name
    ------+-----+-----------+-----------+--------------------------------+------------------------+--------+------------
    0007 | 000 |    Device |    Device |   27.0,   48.0,  693.0, 1232.0 |    0,    0, 1080, 1920 |   0.0  | Wallpaper#0(BLAST Consumer)0 | 0x1070000023b
    0011 | 001 |    Device |    Device |    0.0,    0.0, 1080.0, 1920.0 |    0,    0, 1080, 1920 |  60.1  | VRI[Launcher3QuickStepGo]#2(BLAST Consumer)2 | 0x10700000268
    0005 | 002 |    Device |    Device |    0.0,    0.0, 1080.0,   48.0 |    0,    0, 1080,   48 |   0.2  | VRI[StatusBar]#3(BLAST Consumer)3 | 0x10700000237
    0012 | 003 |    Device |    Device |    0.0,    0.0, 1080.0,   96.0 |    0, 1824, 1080, 1920 |   0.2  | VRI[NavigationBar0]#1(BLAST Consumer)1 | 0x10700000226
    ------+-----+-----------+-----------+--------------------------------+------------------------+--------+------------
    ```
2. 初步认为是 Launcher3QuickStepGo 应用可能出现异常
3. 按照 **操作步骤** 抓打印原始图像数据，输出文件如下：
    ```shell
    M00352_Z0_DEVICE_1280x1280_5120_AB24_AFBC_@Wallpaper#0(BLAST_Consumer)0_id_0x1070000023b.bin
    M00352_Z1_DEVICE_1088x1920_4352_AB24_AFBC_@VRI[Launcher3QuickStepGo]#1(BLAST_C_id_0x10700000256.bin
    M00352_Z2_DEVICE_1088x48_4352_AB24_Raster_@VRI[StatusBar]#3(BLAST_Consumer)3_id_0x10700000237.bin
    M00352_Z3_DEVICE_1088x96_4352_AB24_Raster_@VRI[NavigationBar0]#1(BLAST_Consume_id_0x10700000225.bin
    ```
4. 由于 Launcher3QuickStepGo 图像数据存在AFBC压缩格式，故无法直接观看，所以需要先将AFBC压缩格式关闭
5. 按以下命令操作，然后重启设备关闭AFBC压缩格式
```shell
    # 关闭应用输出的afbc压缩格式
    $ adb shell "echo 'vendor.gralloc.no_afbc_for_sf_client_layer=1' >> /vendor/build.prop"
    # 关闭 GPU合成输出的压缩格式
    $ adb shell "echo 'vendor.gralloc.no_afbc_for_fb_target_layer=1' >> /vendor/build.prop"
    $ adb reboot
```
6. 重新按照**操作步骤** 抓打印原始图像数据，输出文件如下，可见所有的AFBC格式都变成Raster格式，可使用YUView/7YUV查看应用输出情况：
    ```shell
    M00482_Z0_DEVICE_1280x1280_5120_AB24_Raster_@Wallpaper#0(BLAST_Consumer)0_id_0x10700000024.bin
    M00482_Z0_INVALID_1088x1920_4352_AB24_Raster_@FramebufferSurface_id_0x10700000008.bin
    M00482_Z1_CLIENT_1088x1920_4352_AB24_Raster_@VRI[Launcher3QuickStepGo]#1(BLAST_C_id_0x1070000003f.bin
    M00482_Z2_CLIENT_1088x48_4352_AB24_Raster_@VRI[StatusBar]#3(BLAST_Consumer)3_id_0x10700000020.bin
    M00482_Z3_CLIENT_1088x96_4352_AB24_Raster_@VRI[NavigationBar0]#1(BLAST_Consume_id_0x1070000000e.bin
    ```
7. 利用YUView/7YUV查看应用输出图像，确认是否出现异常。最终确认 Launcher3QuickStepGo 图像异常导致显示问题；

### 4.2 视频旋转播放异常问题（非VOP处理的图像数据异常问题：RGA旋转输出异常）

1. 尝试复现 视频旋转场景画面显示异常问题，图层信息可以通过以下命令获取：
    ```shell
    $ adb shell dumpsys SurfaceFlinger | grep -C 10 mFps
    # 输出如下（裁剪了部分信息）：
    DisplayId=0, Connector 417, Type = DSI-1, Connector state = DRM_MODE_CONNECTED
    NumHwLayers=2, activeModeId=3, 1080x1920p60.00, colorMode = -1, bStandardSwitchResolution=0
    ------+-----+-----------+-----------+-------------+--------+------------
    id  |  z  |  sf-type  |  hwc-type |  transform  |  mFps  | name
    ------+-----+-----------+-----------+-------------+--------+------------
    0027 | 000 |    Device |    Device | Rotate90    |  23.8  | SurfaceView[android.rk.RockVideoPlayer | 0x10700000112
    0034 | 001 |    Device |    Device | None        |   3.5  | VRI[VideoPlayActivity]#1(BLAST Consumer)1 | 0x1070000009f
    ------+-----+-----------+-----------+-------------+--------+------------
    ```
2. 按照 **操作步骤** 抓打印VPU输出图像与RGA旋转后处理图像，输出文件如下，对应的文件说明我标注在注释上：
    ```shell
    # VPU输出图像
    M04879_Z0_DEVICE_1920x1088_1920_NV12_Raster_@SurfaceView[android.rk.RockVideoPla_id_0x1070000020f.bin
    # RGA 旋转后处理图像
    M04879_Z0_NONE_1088x1920_1088_NV12_Raster_@RGA-SurfaceView_id_0x107000000b0.bin
    # UI 图像
    M04879_Z1_DEVICE_1088x1920_4352_AB24_Raster_@VRI[VideoPlayActivity]#1(BLAST_Cons_id_0x107000000d1.bin
    ```
3. 利用 YUView/7YUV查看**VPU输出图像** 与 **RGA 旋转后处理图像**，确认是否出现异常。最终确认 RGA 旋转后处理图像异常导致显示问题；

### 4.3 利用WriteBack录屏结果输出异常（VOP处理后的图像数据：VOP合成结果异常）
1. 使能WriteBack录屏功能需要进行如下配置并重启生效：
    ```shell
    $ adb shell "echo 'debug.sf.enable_hwc_vds=true' >> /vendor/build.prop"
    $ adb reboot
    ```
2. 配置生效后利用screenrecord录制屏幕
    ```shell
    $ adb shell "screenrecord /sdcard/test.mp4"
    ```
3. 通过以下日志确认WriteBack功能使能：
    ```shell
    $ adb shell logcat | grep CreateVirtualDisplay
    # HWC 创建 WriteBack 屏幕成功
    I hwc-drm-two: CreateVirtualDisplay,line=252 Support VDS: w=1080,h=1920,f=1 display-id=3

    $ adb shell cat /d/dri/0/summary
    # Video Port1 挂载 Writeback-1 成功，则系统开始使用 Writeback 录制屏幕内容
    Video Port0: DISABLED
    Video Port1: ACTIVE
    Connector:Writeback-1       Encoder: Virtual-392
    Connector:DSI-1     Encoder: DSI-416
        bus_format[100a]: RGB888_1X24
        overlay_mode[0] output_mode[0] SDR[0] color-encoding[BT.709] color-range[Full]

    $ adb shell "dumpsys SurfaceFlinger| grep DisplayId"
    # HWC 创建Virtual-1进行录制屏幕图像
    DisplayId=0, Connector 417, Type = DSI-1, Connector state = DRM_MODE_CONNECTED
    DisplayId=1, Connector 399, Type = eDP-1, Connector state = DRM_MODE_DISCONNECTED
    DisplayId=2, Connector 401, Type = HDMI-A-1, Connector state = DRM_MODE_DISCONNECTED
    DisplayId=3, Connector 397, Type = Virtual-1, Connector state = DRM_MODE_CONNECTED
    ```
4. 按照 **操作步骤** 抓打印原始图像数据与WriteBack图像数据，输出文件如下，对应的文件说明我标注在注释上：
    ```shell
    # 应用原始图像数据，场景为闹钟计时场景,Mxx为主流程送显数据
    M01467_Z0_DEVICE_1088x1920_4352_AB24_Raster_@VRI[DeskClock]#0(BLAST_Consumer)0_id_0x107000000ac.bin
    M01467_Z1_DEVICE_1088x48_4352_AB24_Raster_@VRI[StatusBar]#3(BLAST_Consumer)3_id_0x10700000079.bin
    M01467_Z2_DEVICE_1088x96_4352_AB24_Raster_@VRI[NavigationBar0]#1(BLAST_Consume_id_0x10700000069.bin
    W01467_Z0_NONE_1072x1920_1072_NV12_Raster_@WriteBackBuffer_id_0x107000000b7.bin
    # WriteBack图像数据，Wxx 为 WriteBack 图像数据
    W18690_Z0_NONE_1072x1920_1072_NV12_Raster_@WriteBackBuffer_id_0x1070000004e.bin
    # HWC利用RGA将WriteBack图像数据转换为上层SurfaceFlinger 请求的图像数据，此例子中做了缩放与格式转换
    W00317_Z0_INVALID_1088x1920_4352_AB24_Raster_@GraphicBufferSource_id_0x107000000b5.bin
    ```
5. 利用 YUView/7YUV查看 原始图像数据与WriteBack图像数据即可定位录屏输出的图像数据是否异常问题，最终确认为 HWC利用RGA将WriteBack图像数据转换为上层SurfaceFlinger 请求的图像数据转换过程存在问题，导致录屏图像异常；

### 4.3 HWC 1.6.1版本后，利用WriteBack dump输出检查异常（VOP处理后的图像数据：VOP合成结果异常）
1. HWC 1.6.1 版本后，dump buffer会默认开启dump with WriteBack功能；与4.3相似，但不需要启用vds和录屏。

    WriteBack dump生效的屏幕可以通过vendor.hwc.virtual_display_write_back_id 配置。
    ```shell
    setprop vendor.hwc.virtual_display_write_back_id 0
    ```
2. 按照 **操作步骤** 抓打印原始图像数据与WriteBack图像数据，dump buffer功能输出文件如下：

    ```shell
    # 应用图层原始图像
    M08416_Z0_DEVICE_1920x1088_7680_AB24_AFBC_@VRI[DeskClock]#0(BLAST_Consumer)0_id_0x15d000000ae.bin
    # 状态栏原始图像
    M08416_Z1_DEVICE_1920x48_7680_AB24_Raster_@VRI[StatusBar]#2(BLAST_Consumer)2_id_0x15d00000028.bin
    # 导航栏原始图像
    M08416_Z2_DEVICE_1920x96_7680_AB24_Raster_@VRI[NavigationBar0]#1(BLAST_Consume_id_0x15d0000001b.bin
    # WriteBack输出
    M08416_Z0_NONE_1920x1080_1920_NV12_Raster_@WriteBackBuffer_id_0x15d00000079.bin
    ```
    **注意**: 由于RK3588/RK3568/RK3566等平台硬件限制，WriteBackBuffer宽度存在强制向下16像素对齐要求，如 1080 宽度，WriteBackBuffer宽度为```floor(1080/16)*16 = 1072```

3. 利用 YUView/7YUV 查看原始图像数据与WriteBack图像数据即可定位VP输出的图像数据是否异常问题.

4. 如果HWC版本>1.6.1且平台支持Writeback，但输出文件中没有发现WriteBack文件，可在```vendor.dump=true```开启dump时通过以下日志确认WriteBack功能使能：
    ```shell
    $ adb shell setprop vendor.dump true
    # 开启dump，检查summary

    $ adb shell cat /d/dri/0/summary
    # Video Port1 挂载 Writeback-1 成功，则HWC开始使用 Writeback dump buffer
    Video Port0: DISABLED
    Video Port1: ACTIVE
    Connector:Writeback-1       Encoder: Virtual-392
    Connector:DSI-1     Encoder: DSI-416
        bus_format[100a]: RGB888_1X24
        overlay_mode[0] output_mode[0] SDR[0] color-encoding[BT.709] color-range[Full]

    # 检查完毕关闭dump
    $ adb shell setprop vendor.dump false
    ```

5. 如果需要禁用此功能，配置：
    ```shell
    setprop vendor.dump.disable_wb true
    ```

## 工具推荐

### YUView
可以解析 Raster非压缩格式的多种 YUV/RGB 格式

官方主页：https://ient.github.io/YUView/

源码地址：https://github.com/IENT/YUView

发布版本下载地址： https://github.com/IENT/YUView/releases