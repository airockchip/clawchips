#!/bin/bash

# 检查是否提供了音频文件参数
if [ $# -eq 0 ]; then
    echo "Usage: $0 <audio_file>"
    echo "Example: $0 ../bin/asr_en.wav"
    exit 1
fi

# 从命令行获取最后一个参数
AUDIO_FILE=$(echo "$1" | sed -e 's/\\//g' -e 's/"//g')

if [ ! -f "$AUDIO_FILE" ]; then
    echo "【错误】音频文件不存在！"
    echo "检查路径：$AUDIO_FILE"
    exit 1
fi

cd /userdata/skills/rk-asr/rockasr_linux_aarch64_rk3588/demo/rockasr_demo

sudo ffmpeg -y -loglevel quiet -i "$AUDIO_FILE" -acodec pcm_s16le -ar 16000 -ac 1 tmp.wav
sudo -E sh -c 'export LD_LIBRARY_PATH=../../lib/ && ./rockasr_demo tmp.wav "$0"' "$AUDIO_FILE"
sudo rm tmp.wav
