# Rockchip Binary Image Decoder

将二进制图像数据文件转换为可视化的PNG图像。

## 功能

- 支持多种DRM FourCC格式：AB24, BG24, NV12, NV15, NV16, NV20, NV24
- 自动从文件名解析图像参数（宽度、高度、格式、Stride）
- 不验证文件大小，直接转换
- QQBot通道自动转换为8-bit PNG

## 使用方法

直接发文件，或者和claw说"使用binary-image-decoder解码文件"

### 1. 文件名包含参数

上传文件名包含图像参数的文件，例如：
```
M119636_Z0_NONE_1920x1080_1920_NV12_Raster_@WriteBackBuffer_id_0x1490000023a.bin
```

会自动解析出：
- 宽度: 1920
- 高度: 1080
- Stride: 1920
- 格式: NV12

### 2. 文件名缺少参数

如果文件名不包含足够信息，会询问你提供：
- 宽度
- 高度
- 格式
- Stride

## 支持的格式

| 格式 | 说明 | Stride计算 |
|------|------|-----------|
| AB24 | 32位 BGRA | width × 4 |
| BG24 | 24位 BGR | width × 3 |
| NV12 | YUV 4:2:0 | width |
| NV16 | YUV 4:2:2 | width |
| NV24 | YUV 4:4:4 | width |
| NV15 | 10位 YUV 4:2:0 | width / 4 × 5 (width需4对齐) |
| NV20 | 10位 YUV 4:2:2 | width / 4 × 5 (width需4对齐) |

## 安装依赖

### 使用 pip
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

## 工作原理

1. **解析参数** - 从文件名提取宽度、高度、格式、Stride
2. **运行转换器** - 调用对应的 `CVT_[format]` helper
3. **发送结果** - 直接发送生成的PNG（不验证图像质量）

## 注意事项

- 不检查文件大小是否匹配声明的尺寸
- 不验证输出图像是否正确
- QQBot通道会自动将PNG转换为8-bit深度

## 文件结构

```
rk-binary-image-decoder/
├── SKILL.md              # Skill定义文档
├── README.md             # 本文件
├── references/
│   └── formats.md        # 支持的格式详细说明
├── scripts/              # Python转换脚本
```
