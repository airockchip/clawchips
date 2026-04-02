#!/usr/bin/env python3
"""
CVT_NV20: Convert NV20 (YUV 4:2:2 10-bit packed) format to 16-bit PNG

NV20: 10-bit packed YUV 4:2:2 format
Y plane is packed 10-bit, UV plane is packed 10-bit interleaved
UV is subsampled 2x horizontally but full resolution vertically
Output is 16-bit PNG (10-bit data scaled to 16-bit)
"""

import sys
import numpy as np
from PIL import Image


def unpack4x10(src: bytes, offset: int) -> tuple:
    """
    Unpack 4 10-bit values from 5 bytes.

    Args:
        src: Source byte array
        offset: Offset in src to start reading

    Returns:
        Tuple of 4 uint16 values (10-bit each)
    """
    s0 = src[offset + 0]
    s1 = src[offset + 1]
    s2 = src[offset + 2]
    s3 = src[offset + 3]
    s4 = src[offset + 4]

    dst0 = ((s0 >> 0) & 0xFF) | ((s1 & 0x03) << 8)
    dst1 = ((s1 >> 2) & 0x3F) | ((s2 & 0x0F) << 6)
    dst2 = ((s2 >> 4) & 0x0F) | ((s3 & 0x3F) << 4)
    dst3 = ((s3 >> 6) & 0x0F) | ((s4 & 0xFF) << 2)

    return dst0, dst1, dst2, dst3


def yuv10bit_packed_to_yuv422p16(
    src: bytes,
    width: int,
    height: int,
    y_stride_bytes: int,
    uv_stride_bytes: int,
    x_div2: bool,
    uv_swap: bool
) -> tuple:
    """
    Convert packed 10-bit YUV 4:2:2 to planar 16-bit YUV.

    Args:
        src: Source packed data
        width: Image width
        height: Image height
        y_stride_bytes: Bytes per row in Y plane
        uv_stride_bytes: Bytes per row in UV plane
        x_div2: Whether UV is subsampled horizontally
        uv_swap: Whether to swap U and V

    Returns:
        Tuple of (Y_plane, U_plane_sub, V_plane_sub) as numpy arrays
    """
    # Y plane - full resolution
    Y = np.zeros((height, width), dtype=np.uint16)

    for y in range(height):
        row_offset = y * y_stride_bytes
        out_row = Y[y, :]
        x = 0
        while x + 3 < width:
            # 4 pixels packed into 5 bytes
            pack_offset = row_offset + (x * 5) // 4
            if pack_offset + 4 < len(src):
                dst0, dst1, dst2, dst3 = unpack4x10(src, pack_offset)
                out_row[x] = dst0
                out_row[x + 1] = dst1
                out_row[x + 2] = dst2
                out_row[x + 3] = dst3
            x += 4

    # UV plane - subsampled horizontally only (4:2:2)
    src_uv_offset = y_stride_bytes * height
    cw = width // 2 if x_div2 else width
    ch = height

    U_sub = np.zeros((ch, cw), dtype=np.uint16)
    V_sub = np.zeros((ch, cw), dtype=np.uint16)

    for y in range(ch):
        row_offset = src_uv_offset + y * uv_stride_bytes
        x = 0
        while x + 1 < cw:
            # 4 UV values (2 U + 2 V) packed into 5 bytes
            pack_offset = row_offset + (x * 5) // 2
            if pack_offset + 4 < len(src):
                uv0, uv1, uv2, uv3 = unpack4x10(src, pack_offset)
                # uv[] = U0 V0 U1 V1
                if uv_swap:
                    V_sub[y, x] = uv0
                    U_sub[y, x] = uv1
                    V_sub[y, x + 1] = uv2
                    U_sub[y, x + 1] = uv3
                else:
                    U_sub[y, x] = uv0
                    V_sub[y, x] = uv1
                    U_sub[y, x + 1] = uv2
                    V_sub[y, x + 1] = uv3
            x += 2

    return Y, U_sub, V_sub


def upsample_uv_2x1(src: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Upsample 2x1 subsampled UV to full resolution.
    UV is subsampled horizontally only (4:2:2).

    Args:
        src: Source UV plane (subsampled)
        width: Target width (full resolution)
        height: Target height (full resolution)

    Returns:
        Upsampled UV plane
    """
    ch, cw = src.shape
    dst = np.zeros((height, width), dtype=np.uint16)

    for y in range(height):
        sy = y  # Same vertical resolution
        for x in range(width):
            sx = x // 2
            if sy < ch and sx < cw:
                dst[y, x] = src[sy, sx]

    return dst


def nv20_to_yuv_planes(file_data: bytes, width: int, height: int, stride: int) -> tuple:
    """
    Convert NV20 data to separate Y, U, V planes (10-bit).

    Args:
        file_data: Raw binary NV20 data
        width: Image width in pixels
        height: Image height in pixels
        stride: Bytes per row in packed Y and UV planes

    Returns:
        Tuple of (Y_plane, U_plane, V_plane) as numpy arrays (10-bit values)
    """
    # NV20: 10-bit packed, 4 pixels = 5 bytes, so min stride = ceil(width * 5 / 4)
    min_stride = (width * 5 + 3) // 4
    if stride < min_stride:
        stride = min_stride

    # NV20: Y plane is height * stride, UV plane is height * stride
    required_size = height * stride + height * stride

    # Resize file_data to required size, pad with zeros if too small
    if len(file_data) < required_size:
        file_data = file_data + bytes(required_size - len(file_data))
    elif len(file_data) > required_size:
        file_data = file_data[:required_size]

    # NV20 is 10-bit packed 4:2:2
    Y, U_sub, V_sub = yuv10bit_packed_to_yuv422p16(
        file_data, width, height, stride, stride, True, False
    )

    # Upsample U/V to full resolution
    U = upsample_uv_2x1(U_sub, width, height)
    V = upsample_uv_2x1(V_sub, width, height)

    return Y, U, V


def yuv_planes_to_rgb10(Y: np.ndarray, U: np.ndarray, V: np.ndarray) -> np.ndarray:
    """
    Convert 10-bit YUV planes to 16-bit RGB using BT.601 coefficients.

    Args:
        Y: Y plane as numpy array (height, width), values 0-1023 (10-bit)
        U: U plane as numpy array (height, width), values 0-1023 (10-bit)
        V: V plane as numpy array (height, width), values 0-1023 (10-bit)

    Returns:
        RGBA image as numpy array (height, width, 4), 16-bit values
    """
    height, width = Y.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint16)

    # 10-bit YUV to 10-bit RGB conversion using BT.601 coefficients
    C = Y.astype(np.int32) - 64
    D = U.astype(np.int32) - 512
    E = V.astype(np.int32) - 512

    R = (1192 * C + 1634 * E + 512) >> 10
    G = (1192 * C - 401 * D - 833 * E + 512) >> 10
    B = (1192 * C + 2066 * D + 512) >> 10

    # Clamp to 0-1023 (10-bit)
    R = np.clip(R, 0, 1023)
    G = np.clip(G, 0, 1023)
    B = np.clip(B, 0, 1023)

    # Scale to 16-bit (shift left by 6)
    rgba[:, :, 0] = (R << 6).astype(np.uint16)
    rgba[:, :, 1] = (G << 6).astype(np.uint16)
    rgba[:, :, 2] = (B << 6).astype(np.uint16)
    rgba[:, :, 3] = 0xFFFF  # Alpha

    return rgba


def write_png16(filename: str, rgba_data: np.ndarray) -> bool:
    """
    Write RGBA data to PNG file.
    Converts 16-bit data to 8-bit for compatibility.

    Args:
        filename: Output PNG file path
        rgba_data: RGBA data as numpy array (height, width, 4), 16-bit values

    Returns:
        True if successful
    """
    try:
        # Convert 16-bit to 8-bit by shifting right by 8 (keep high 8 bits)
        # The data was scaled to 16-bit by shifting left by 6, so we shift right by 6 to get back to 10-bit,
        # then right by 2 more to get to 8-bit
        rgba_8bit = (rgba_data >> 8).astype(np.uint8)
        img = Image.fromarray(rgba_8bit, mode='RGBA')
        img.save(filename)
        return True
    except Exception as e:
        print(f"Error writing PNG: {e}", file=sys.stderr)
        return False


def main():
    if len(sys.argv) != 6:
        print(f"Usage: {sys.argv[0]} input.nv20 width height stride output.png", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    width = int(sys.argv[2])
    height = int(sys.argv[3])
    stride = int(sys.argv[4])
    output_file = sys.argv[5]

    try:
        with open(input_file, 'rb') as f:
            file_data = f.read()
    except IOError as e:
        print(f"Failed to open input file: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        Y, U, V = nv20_to_yuv_planes(file_data, width, height, stride)
    except ValueError as e:
        print(f"Failed to read NV20 file: {e}", file=sys.stderr)
        sys.exit(2)

    rgba_data = yuv_planes_to_rgb10(Y, U, V)

    if not write_png16(output_file, rgba_data):
        sys.exit(3)

    print("Conversion successful.")


if __name__ == "__main__":
    main()
