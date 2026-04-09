# Python 图像格式转换工具

这是 `src` 目录中 C++ 转换工具的 Python 重写版本。

## 文件列表

| 文件名 | 功能描述 | 输入格式 | 输出格式 |
|--------|---------|---------|---------|
| CVT_BG24.py | BGR 8-bit 原始像素转 PNG | BG24 (每像素3字节 BGR) | 8-bit RGBA PNG |
| CVT_AB24.py | BGRA 8-bit 原始像素转 PNG | AB24 (每像素4字节 BGRA) | 8-bit RGBA PNG |
| CVT_NV12.py | YUV 4:2:0 8-bit 转 PNG | NV12 (Y平面 + UV交错平面) | 8-bit RGBA PNG |
| CVT_NV16.py | YUV 4:2:2 8-bit 转 PNG | NV16 (Y平面 + UV交错平面) | 8-bit RGBA PNG |
| CVT_NV24.py | YUV 4:4:4 8-bit 转 PNG | NV24 (Y平面 + UV交错平面) | 8-bit RGBA PNG |
| CVT_NV15.py | YUV 4:2:0 10-bit packed 转 PNG | NV15 (10-bit packed) | 8-bit RGBA PNG |
| CVT_NV20.py | YUV 4:2:2 10-bit packed 转 PNG | NV20 (10-bit packed) | 8-bit RGBA PNG |

## 依赖

- Python 3.6+
- numpy
- Pillow (PIL)

### 使用 pip 安装
```bash
pip install numpy Pillow
```

### 使用系统包管理器

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install python3-numpy python3-pil
```

**Fedora/RHEL/CentOS:**
```bash
sudo dnf install python3-numpy python3-pillow
```

**Arch Linux:**
```bash
sudo pacman -S python-numpy python-pillow
```

## 使用方法

所有脚本使用相同的命令行参数格式:

```bash
# 基本用法（自动计算stride）
python3 CVT_BG24.py input.bg24 width height output.png

# 指定stride
python3 CVT_BG24.py input.bg24 width height stride output.png
```

### 使用示例

```bash
# BG24: 每像素3字节，stride = width * 3
python3 CVT_BG24.py input.bg24 1920 1080 5760 output.png

# AB24: 每像素4字节，stride = width * 4
python3 CVT_AB24.py input.ab24 1920 1080 7680 output.png

# NV12: YUV 4:2:0, stride = width
python3 CVT_NV12.py input.nv12 1920 1080 1920 output.png

# NV16: YUV 4:2:2, stride = width
python3 CVT_NV16.py input.nv16 1920 1080 1920 output.png

# NV24: YUV 4:4:4, stride = width
python3 CVT_NV24.py input.nv24 1920 1080 1920 output.png

# NV15: 10-bit packed 4:2:0, width 需4对齐，stride = width / 4 × 5
python3 CVT_NV15.py input.nv15 1920 1080 2400 output.png

# NV20: 10-bit packed 4:2:2, width 需4对齐，stride = width / 4 × 5
python3 CVT_NV20.py input.nv20 1920 1080 2400 output.png
```

参数说明:
- `input.*`: 输入的二进制原始图像文件
- `width`: 图像宽度（像素）
- `height`: 图像高度（像素）
- `stride`: 每行的字节数（步幅，可选）
- `output.png`: 输出的 PNG 文件路径

## 格式详细说明

### RGB 格式

**BG24**: 24位 BGR
- 每像素3字节（B, G, R 顺序）
- 最小 stride = width × 3

**AB24**: 32位 BGRA
- 每像素4字节（B, G, R, A 顺序）
- 最小 stride = width × 4

### YUV 8-bit 格式

**NV12**: YUV 4:2:0
- Y平面: height × stride 字节
- UV平面: (height/2) × stride 字节，交错存储（U, V 交错）
- 最小 stride = width

**NV16**: YUV 4:2:2
- Y平面: height × stride 字节
- UV平面: height × stride 字节，交错存储（U, V 交错）
- 最小 stride = width

**NV24**: YUV 4:4:4
- Y平面: height × stride 字节
- UV平面: height × stride × 2 字节，交错存储（U, V 交错）
- 最小 stride = width

### YUV 10-bit Packed 格式

**NV15**: YUV 4:2:0 10-bit packed
- 4个像素打包到5字节（每像素10位）
- width 需4对齐，最小 stride = width / 4 × 5

**NV20**: YUV 4:2:2 10-bit packed
- 4个像素打包到5字节（每像素10位）
- width 需4对齐，最小 stride = width / 4 × 5

## 注意事项

- 所有脚本输出均为 8-bit RGBA PNG
- NV15/NV20 内部处理 10-bit 数据，最终转换为 8-bit 输出
- stride 参数如果小于格式要求的最小值，会自动调整为最小值
- 所有 YUV 转换使用 BT.601 标准系数
- 10-bit packed 格式（NV15/NV20）使用特殊的 4像素/5字节打包方式
- 如果输入文件小于期望大小，会自动用零填充
- 如果输入文件大于期望大小，会截取所需部分
