#!/bin/bash

# 检查是否提供了文本
if [ $# -eq 0 ]; then
    echo "Usage: $0 <text>"
    echo "Example: $0 \"text to tts\""
    exit 1
fi

# 从命令行获取最后一个参数
TEXT=$(echo "$1" | sed -e 's/\\//g' -e 's/"//g')

cd /userdata/skills/rk-tts/rocktts_linux_aarch64_rk3588
sudo rm /userdata/output.wav
sudo -E sh -c 'export LD_LIBRARY_PATH=./lib/ && ./demo/rocktts_demo/rocktts_demo "$0"' "$TEXT"
