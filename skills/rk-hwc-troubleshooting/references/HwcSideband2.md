# **Sideband2调试说明**

文件标识：RK-PC-YF-xxxx

发布版本：V1.0.0

日期：2025-4-18

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

本文档主要介绍 Sideband 2.0 基础功能与调试手段，用于日常功能调试。

**产品版本**

| **芯片名称** | **系统版本**     | **DrmHwc2版本**|
| ------------ | ---------------- |--|
| RK3588       | Android  12 / 13 / 14 / 15 |v1.5.180|
| RK3576       | Android  14 / 15 |v1.5.180|
| RK3568       | Android  11 / 12 / 13 / 14 / 15 |v1.5.180|
| RK3566       | Android  11 / 12 / 13 / 14 / 15 |v1.5.180|
| RK3562       | Android  13 / 14 / 15 |v1.5.180|
| RK3326       | Android  14 / 15 |v1.5.180|
| RK3399       | Android  14 / 15 | v1.5.180|
| RK3528       | Android  14 / 15 | v1.5.180|

**读者对象**

本文档主要适用于以下工程师：

- 技术支持工程师
- 软件开发工程师

**修订记录**

| **日期**   | **版本** | **作者**  | **修改说明** |
| ---------- | -------- | --------- | ------------ |
| 2025/04/18 | 1.0.0    | GPU图形组 | 初始版本     |

**目 录**

[TOC]

## 1 概述

Sideband 2.0 主要靠建立图像生产者与屏端消费者的RK私有送显通道，实现图像高效、低延迟送显功能，主要体现在以下方面：

- 视频播放场景：利用RK私有送显通道，简化送显通路上的跨进程传输通路，减少系统显示组件的逻辑执行从而降低系统综合负载与功耗；
- HDMI-IN场景：可根据HDMI-IN/VOP读写数据特性实现低延迟送显，延迟最低可做到 0.5 Vsync 时间；

本文主要介绍Sideband 2.0 运行过程可能会用到的调试方法。

## 2 Sideband运行状态查询：
1. 使能Sideband功能后，利用以下命令查看状态：
    ```shell
    $ adb shell dumpsys SurfaceFlinger > sf.log
    ```

### 2.1 单屏Sideband日志举例：
截取单屏Sideband相关日志如下：
```
Sideband-2.0 Info Dump:
InitSuccess TunnelFd=18 CtxSize=1 ActiveCtxSize=1
SidebandCtxInfo: Id=2 CacheBufferSize=9 TransformSize=1 ProducerFps=60.00 refDpyConnection=2 DataSpace=0x8010000 ReleaseFailedBufferSize=0 NeedReleaseCtxSize=0
	SidebandCtxTransformInfo:
	[DisplayId, Transform]: [0: R0],
	[R0] TunnelId=2 VpCompositionSize=2 Fps=60.13 Latency=10.339 ms FrameNo=26254
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	    FN    |  HndId  |    BufferId     | FbId  | Fcc  | stride x height | Ready | Present |  Q->A ms |  Q->P ms |    name
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	    26253 |  B9     |  0x141000004b1  |   545 | NV16 |   1920 x   1080 |   Yes |     Yes |     0.11 |    10.28 | VP-R0-T2-B9-FN26253
	    26254 |  B1     |  0x141000004a9  |   534 | NV16 |   1920 x   1080 |    No |      No |     0.07 |      NoP | VP-R0-T2-B1-FN26254
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
```

依次说明日志相关参数：
- TunnelFd：Tunnel 设备节点的文件描述符
- CtxSize： SidebandCtx创建数目统计，数值与Sideband通路相等，一路则Ctx数量为1，通常为1
- ActiveCtxSize：激活状态的 SidebandCtx 数目统计，切换过程可能会大于1，即同时存在两个 SidebandCtx ，其中一个正在进行销毁；
- SidebandCtxInfo： 创建Sideband送显通道就会对应Sideband Context
    - Id： SidebandCtx ID 为单调递增数值
    - CacheBufferSize：HWC内部缓存的Buffer数量，缓存内存来源于 sideband producer 传递，可通过此参数查询HWC引用的内存数目；
    - TransformSize：统计当前Sideband后级Transform数目，可能类型举例：
        - R0（未做任何处理）
        - R90 旋转90度
        - SR_R90,后级执行SR处理，并且旋转90度
        - MM_R90,后级执行MEMC处理，并且旋转90度
    - ProducerFps：前级的帧率信息，视频就是解码帧率，HDMI-IN就是HDMI-IN输入帧率
    - refDpyConnection：引用的实例数量，实例包括后级物理屏幕，以及内部的逻辑引用，refDpyConnection 等于则进入销毁逻辑
    - DataSpace：Producer 设置的色域信息，类型为android_dataspace_t，定义参考[Android Dataspace文档](https://developer.android.com/reference/android/hardware/DataSpace)
    - ReleaseFailedBufferSize: Release失败的Buffer数目，大于0可能出现内存泄漏或者DMA-Buffer泄漏；
    - NeedReleaseCtxSize： 需要执行Release TransformCtx 数目，比如前级旋转由90度变成180度，则90度进入待销毁的列表，等所有的后级引用解除后进入销毁逻辑；
- SidebandCtxTransformInfo：Sideband 通路后端可能存在 Transform，比如屏幕旋转需求/SR/MEMC后处理需求等，每一个Transform则创建对应SidebandCtxTransform
    - [DisplayId, Transform]：列举后级Display与Transform设置情况
    - [R0]：表示无Transform，ByPass模式
    - TunnelId：表示 SidebandCtx 对应的ID
    - VpCompositionSize：对应 SidebandCtxTransform 内部存在的待处理/待送显 VpComposition 数量；
    - Fps：实际 SidebandCtxTransform 执行的帧率信息，统计为过去10帧的平均帧率
    - Latency：Sideband框架实际送显的显示延迟，即Producer请求刷新到Comsumer提交上屏的耗时。实际的显示延迟还需要加上 Producer 前级的处理时间；
    - FrameNo: 当前 SidebandCtxTransform 执行的 Frame 统计
    - VpComposition：Sideband内部获取的帧信息：
        - FN：对应FrameNo，为当前SidebandCtxTransform执行计数
        - HndId：由 tunnel driver 获取的 vt_buffer_handle 内部的BufferId，可标识当前 VpComposition 处理的来自Producer原始图像帧；
        - BufferId：内存唯一标识，区别于 HndId，HndId是由 Vtunnel 驱动赋予，BufferId是由内存分配模块的Gralloc赋予，可作为内存标识，区分送显图像；
        - FbId：DRM GemHandle Id 用于提交送显；
        - Fcc: DRM Fourcc格式，用于说明图像格式
        - stride x height： 内存申请的stride/height信息
        - Ready：标识图像是否渲染完成，部分低延迟模式允许未完成图像送显到屏幕
        - Present：标识图像是否上屏，即是否显示到屏幕上；
        - Q->A：Producer QueueBuffer 到 Consumer AcquireBuffer 之间的耗时间隔;
        - Q->P: Producer QueueBuffer 到 Consumer Present 之间的耗时间隔，通常用于说明 Consumer 上屏之前的处理延迟；
        - Name：VpComposition 唯一命名，命名规则为：VP-Transform-TunnelId-HndId-FrameNo

### 2.2 多屏Sideband日志举例：
截取多屏Sideband相关日志如下，双屏同显，使用ByPass模式（R0）：
```
Sideband-2.0 Info Dump:
InitSuccess TunnelFd=18 CtxSize=1 ActiveCtxSize=1
SidebandCtxInfo: Id=2 CacheBufferSize=9 TransformSize=1 ProducerFps=60.00 refDpyConnection=3 DataSpace=0x8010000 ReleaseFailedBufferSize=0 NeedReleaseCtxSize=0
	SidebandCtxTransformInfo:
	[DisplayId, Transform]: [0: R0], [1: R0],
	[R0] TunnelId=2 VpCompositionSize=2 Fps=60.09 Latency=11.881 ms FrameNo=57906
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	    FN    |  HndId  |    BufferId     | FbId  | Fcc  | stride x height | Ready | Present |  Q->A ms |  Q->P ms |    name
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	    57905 |  B8     |  0x141000004b0  |   544 | NV16 |   1920 x   1080 |   Yes |     Yes |     0.12 |    11.85 | VP-R0-T2-B8-FN57905
	    57906 |  B9     |  0x141000004b1  |   545 | NV16 |   1920 x   1080 |    No |      No |     0.16 |      NoP | VP-R0-T2-B9-FN57906
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
```

对比单屏，[DisplayId, Transform]:  增加 [1: R0] 的信息，其余与单屏一致；

### 2.2 多屏Sideband+Rotate_Transform日志举例：
截取多屏Sideband相关日志如下，双屏同显，主屏旋转90度，副屏ByPass：
```
Sideband-2.0 Info Dump:
InitSuccess TunnelFd=18 CtxSize=1 ActiveCtxSize=1
SidebandCtxInfo: Id=2 CacheBufferSize=9 TransformSize=2 ProducerFps=60.00 refDpyConnection=3 DataSpace=0x8010000 ReleaseFailedBufferSize=0 NeedReleaseCtxSize=0
	SidebandCtxTransformInfo:
	[DisplayId, Transform]: [0: R90], [1: R0],
	[R0] TunnelId=2 VpCompositionSize=3 Fps=60.00 Latency=15.054 ms FrameNo=395
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	    FN    |  HndId  |    BufferId     | FbId  | Fcc  | stride x height | Ready | Present |  Q->A ms |  Q->P ms |    name
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	      393 |  B2     |  0x141000004aa  |   539 | NV16 |   1920 x   1080 |   Yes |     Yes |     0.33 |    16.27 | VP-R0-T2-B2-FN393
	      394 |  B3     |  0x141000004ab  |   540 | NV16 |   1920 x   1080 |   Yes |     Yes |     0.15 |    16.41 | VP-R0-T2-B3-FN394
	      395 |  B4     |  0x141000004ac  |   542 | NV16 |   1920 x   1080 |    No |      No |     0.06 |      NoP | VP-R0-T2-B4-FN395
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	[R90] TunnelId=2 VpCompositionSize=2 Fps=60.17 Latency=30.441 ms FrameNo=381
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	    FN    |  HndId  |    BufferId     | FbId  | Fcc  | stride x height | Ready | Present |  Q->A ms |  Q->P ms |    name
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	      380 |  B2     |  0x141000004cd  |   545 | NV16 |   1088 x   1920 |   Yes |     Yes |    14.62 |    30.32 | VP-R90-T2-B2-FN380
	      381 |  B3     |  0x141000004ca  |   536 | NV16 |   1088 x   1920 |   Yes |      No |    14.65 |      NoP | VP-R90-T2-B3-FN381
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
```
下面仅列举差异部分：
- [DisplayId, Transform]: [0: R90], [1: R0] , 主屏请求 R90 旋转，副屏请求 R0 ByPass
- Latency：
    - [R0] 显示平均延迟 15.054 ms
    - [R90] 显示平均延迟 30.44 ms，主要区别在于旋转还需要额外调用RGA进行图像的后处理，故延迟增加；

### 2.3 多屏Sideband+SR_Transform日志举例：

由于SR后处理仅支持HDMI-IN 30帧输入，故Producer帧率切换到30帧；

```
Sideband-2.0 Info Dump:
InitSuccess TunnelFd=9 CtxSize=1 ActiveCtxSize=1
SidebandCtxInfo: Id=2 CacheBufferSize=6 TransformSize=2 ProducerFps=30.00 refDpyConnection=3 DataSpace=0x8000000 ReleaseFailedBufferSize=0 NeedReleaseCtxSize=0
	SidebandCtxTransformInfo:
	[DisplayId, Transform]: [0: R0_SR], [2: R0],
	[R0] TunnelId=2 VpCompositionSize=2 Fps=30.00 Latency=19.618 ms FrameNo=101
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	    FN    |  HndId  |    BufferId     | FbId  | Fcc  | stride x height | Ready | Present |  Q->A ms |  Q->P ms |    name
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	      100 |  B4     |  0x15d000000cd  |   560 | RG24 |   1920 x   1080 |   Yes |     Yes |     0.06 |    19.49 | VP-R0-T2-B4-FN100
	      101 |  B5     |  0x15d000000ce  |   561 | RG24 |   1920 x   1080 |   Yes |      No |     0.03 |      NoP | VP-R0-T2-B5-FN101
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	[R0_SR] TunnelId=2 VpCompositionSize=2 Fps=29.96 Latency=90.675 ms FrameNo=100
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	    FN    |  HndId  |    BufferId     | FbId  | Fcc  | stride x height | Ready | Present |  Q->A ms |  Q->P ms |    name
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
	       99 |  B3     |  0x15d000000d3  |   568 | NV12 |   1920 x   1080 |   Yes |     Yes |    10.36 |    65.78 | VP-R0_SR-T2-B3-FN99
	      100 |  B4     |  0x15d000000d4  |   570 | NV12 |   1920 x   1080 |    No |      No |    11.32 |      NoP | VP-R0_SR-T2-B4-FN100
	----------+---------+-----------------+-------+------+-----------------+-------+---------+----------+----------+----------------------
```
下面仅列举差异部分：
- [DisplayId, Transform]:
    - [0: R90_SR]：主屏执行旋转90度，SR后处理；
    - [1: R0] : 副屏ByPass处理；
- Latency：
    - [0: R90_SR]：19.618 ms, 由于输入帧率30，输出帧率为60，故需要等待Producer渲染完成后送显，显示延迟会增加；
    - [1: R0] : 90.675 ms, 由于后级增加SR后处理，延迟增加50ms左右，符合预期；



## 3 Sideband图像文件dump方法：

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
    S40473_Z0_NONE_1920x1080_1920_NV12_Raster_@SR-SidebandStream_id_0x15d00000276.bin
    S40473_Z0_NONE_1920x1080_5760_RG24_Raster_@videotunnel_id_0x15d00000267.bin
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

其中：
- S40473_Z0_NONE_1920x1080_5760_RG24_Raster_@videotunnel_id_0x15d00000267.bin 为 前级Producer提交的图像数据
- S40473_Z0_NONE_1920x1080_1920_NV12_Raster_@SR-SidebandStream_id_0x15d00000276.bin 为 后级SR处理后的图像数据

可利用7YUV 或者 YUView 对图像数据进行查看，分析问题；

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

具体方法可参考 ![HwcDumpBuffer.md](./HwcDumpBuffer.md)

### YUView 工具推荐
可以解析 Raster非压缩格式的多种 YUV/RGB 格式

官方主页：https://ient.github.io/YUView/

源码地址：https://github.com/IENT/YUView

发布版本下载地址： https://github.com/IENT/YUView/releases


