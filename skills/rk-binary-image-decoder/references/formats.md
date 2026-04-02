# Binary Image Decoder - Supported Formats

This document describes common binary formats that can be decoded to images.

## Raw Binary Formats

### 1. Raw Pixel Data

**Variants:**
- **AB24** : single plane, stride=width*4
- **BG24** : single plane, stride=width*3
- **NV12** : two plane, uv is 2x2 subsampled, stride=width*1
- **NV16** : two plane, uv is 2x1 subsampled, stride=width*1
- **NV24** : two plane, stride is width*1
- **NV15** : two plane, uv is 2x2 subsampled, stride=width*1.25, width should align to 4
- **NV20** : two plane, uv is 2x1 subsampled, stride=width*1.25, width should align to 4
- And other format supported by helper

**Parameters needed:**
- Width (pixels per row)
- Height (rows)
- Stride (bytes pre row)

## Error Handling

**Common issues:**
1. **Unknown format:** Request user clarification
2. **Wrong Size:** Inform user, but helper program can still process it
3. **Wrong Stride:** Fix it to minimum stride, and inform user

**Best practices:**
- Always inform user if size or stride is not correct, and process it anyway; helper program can handle size error.
- Provide meaningful error messages
