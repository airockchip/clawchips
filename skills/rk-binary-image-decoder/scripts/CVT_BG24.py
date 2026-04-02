#!/usr/bin/env python3
"""
CVT_BG24: Convert BG24 (BGR 8-bit raw) format to PNG

BG24: 3 bytes per pixel, BGR order
"""

import sys
import numpy as np
from PIL import Image


def read_bg24(file_data: bytes, width: int, height: int, stride: int) -> np.ndarray:
    """
    Read BG24 data and convert to RGBA numpy array.

    Args:
        file_data: Raw binary data
        width: Image width in pixels
        height: Image height in pixels
        stride: Bytes per row in input file

    Returns:
        RGBA image as numpy array (height, width, 4)
    """
    # Ensure stride is at least width * 3 (3 bytes per pixel for BG24)
    min_stride = width * 3
    if stride < min_stride:
        stride = min_stride

    required_size = height * stride
    # Resize file_data to required size, pad with zeros if too small
    if len(file_data) < required_size:
        file_data = file_data + bytes(required_size - len(file_data))
    elif len(file_data) > required_size:
        file_data = file_data[:required_size]

    # Create output array (RGBA)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)

    # Convert BGR to RGBA
    for y in range(height):
        row_offset = y * stride
        for x in range(width):
            b = file_data[row_offset + x * 3 + 0]
            g = file_data[row_offset + x * 3 + 1]
            r = file_data[row_offset + x * 3 + 2]
            rgba[y, x] = [r, g, b, 255]

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
        # Convert to PIL Image
        img = Image.fromarray(rgba_data, mode='RGBA')
        img.save(filename)
        return True
    except Exception as e:
        print(f"Error writing PNG: {e}", file=sys.stderr)
        return False


def main():
    if len(sys.argv) != 6:
        print(f"Usage: {sys.argv[0]} input.bg24 width height stride output.png", file=sys.stderr)
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
        rgba_data = read_bg24(file_data, width, height, stride)
    except ValueError as e:
        print(f"Failed to read BG24 file: {e}", file=sys.stderr)
        sys.exit(2)

    if not write_png(output_file, rgba_data):
        sys.exit(3)

    print("Conversion successful.")


if __name__ == "__main__":
    main()
