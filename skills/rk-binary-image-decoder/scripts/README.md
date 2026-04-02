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

安装依赖:
```bash
pip install numpy Pillow
```

## 使用方法

所有脚本使用相同的命令行参数格式:

```bash
python3 CVT_BG24.py input.bg24 width height stride output.png
python3 CVT_AB24.py input.ab24 width height stride output.png
python3 CVT_NV12.py input.nv12 width height stride output.png
python3 CVT_NV16.py input.nv16 width height stride output.png
python3 CVT_NV24.py input.nv24 width height stride output.png
python3 CVT_NV15.py input.nv15 width height stride output.png
python3 CVT_NV20.py input.nv20 width height stride output.png
```

参数说明:
- `input.*`: 输入的二进制原始图像文件
- `width`: 图像宽度（像素）
- `height`: 图像高度（像素）
- `stride`: 每行的字节数（步幅）
- `output.png`: 输出的 PNG 文件路径

## 注意事项

- 所有脚本输出均为 8-bit RGBA PNG（NV15/NV20 内部处理 10-bit 数据，最终转换为 8-bit 输出）
- stride 参数如果小于格式要求的最小值，会自动调整为最小值
- 所有 YUV 转换使用 BT.601 标准系数
- 10-bit packed 格式（NV15/NV20）使用特殊的 4像素/5字节打包方式
