#!/usr/bin/env python3
"""
CVT_NV24: Convert NV24 (YUV 4:4:4 8-bit) format to PNG

NV24: Y plane (width*height), followed by interleaved UV plane (width*height*2)
UV is full resolution (4:4:4), no subsampling.
"""

import sys
import numpy as np
from PIL import Image


def nv24_to_yuv_planes(file_data: bytes, width: int, height: int, stride: int) -> tuple:
    """
    Convert NV24 data to separate Y, U, V planes.

    Args:
        file_data: Raw binary NV24 data
        width: Image width in pixels
        height: Image height in pixels
        stride: Bytes per row in Y plane

    Returns:
        Tuple of (Y_plane, U_plane, V_plane) as numpy arrays
    """
    # Ensure stride is at least width (1 byte per pixel for Y plane)
    if stride < width:
        stride = width

    y_plane_size = height * stride
    # NV24 UV plane is 2x width per row (interleaved U and V)
    uv_plane_size = height * stride * 2

    # Resize file_data to required size, pad with zeros if too small
    expected_size = y_plane_size + uv_plane_size
    if len(file_data) < expected_size:
        file_data = file_data + bytes(expected_size - len(file_data))
    elif len(file_data) > expected_size:
        file_data = file_data[:expected_size]

    # Extract Y plane
    Y = np.zeros((height, width), dtype=np.uint16)
    for y in range(height):
        y_row = file_data[y * stride:y * stride + width]
        Y[y, :] = np.frombuffer(y_row, dtype=np.uint8)[:width]

    # Extract UV plane (full resolution)
    uv_data = file_data[y_plane_size:y_plane_size + uv_plane_size]
    U = np.zeros((height, width), dtype=np.uint16)
    V = np.zeros((height, width), dtype=np.uint16)

    # UV is full resolution: each pixel has its own U and V
    for y in range(height):
        uv_row_offset = y * stride * 2
        for x in range(width):
            u_val = uv_data[uv_row_offset + x * 2 + 0]
            v_val = uv_data[uv_row_offset + x * 2 + 1]
            U[y, x] = u_val
            V[y, x] = v_val

    return Y, U, V


def yuv_planes_to_rgb(Y: np.ndarray, U: np.ndarray, V: np.ndarray) -> np.ndarray:
    """
    Convert YUV planes to RGB using BT.601 coefficients.

    Args:
        Y: Y plane as numpy array (height, width), values 0-255
        U: U plane as numpy array (height, width), values 0-255
        V: V plane as numpy array (height, width), values 0-255

    Returns:
        RGBA image as numpy array (height, width, 4)
    """
    height, width = Y.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)

    # YUV to RGB conversion using BT.601 coefficients (8-bit)
    C = Y.astype(np.int32) - 16
    D = U.astype(np.int32) - 128
    E = V.astype(np.int32) - 128

    R = (298 * C + 409 * E + 128) >> 8
    G = (298 * C - 100 * D - 208 * E + 128) >> 8
    B = (298 * C + 516 * D + 128) >> 8

    # Clamp to 0-255
    R = np.clip(R, 0, 255).astype(np.uint8)
    G = np.clip(G, 0, 255).astype(np.uint8)
    B = np.clip(B, 0, 255).astype(np.uint8)

    rgba[:, :, 0] = R
    rgba[:, :, 1] = G
    rgba[:, :, 2] = B
    rgba[:, :, 3] = 255  # Alpha

    return rgba


def write_png(filename: str, rgba_data: np.ndarray) -> bool:
    """
    Write RGBA data to PNG file.

    Args:
        filename: Output PNG file path
        rgba_data: RGBA data as numpy array (height, width, 4)

    Returns:
        True if successful
    """
    try:
        img = Image.fromarray(rgba_data, mode='RGBA')
        img.save(filename)
        return True
    except Exception as e:
        print(f"Error writing PNG: {e}", file=sys.stderr)
        return False


def main():
    if len(sys.argv) != 6:
        print(f"Usage: {sys.argv[0]} input.nv24 width height stride output.png", file=sys.stderr)
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
        Y, U, V = nv24_to_yuv_planes(file_data, width, height, stride)
    except ValueError as e:
        print(f"Failed to read NV24 file: {e}", file=sys.stderr)
        sys.exit(2)

    rgba_data = yuv_planes_to_rgb(Y, U, V)

    if not write_png(output_file, rgba_data):
        sys.exit(3)

    print("Conversion successful.")


if __name__ == "__main__":
    main()
