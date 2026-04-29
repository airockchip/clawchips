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

TEMP_WAV="temp_$(basename "$AUDIO_FILE" | sed 's/\.[^.]*$//').wav"
sudo ffmpeg -y -loglevel quiet -i "$AUDIO_FILE" -acodec pcm_s16le -ar 16000 -ac 1 "$TEMP_WAV"
python3 asr_file.py "$TEMP_WAV"
sudo rm -f "$TEMP_WAV"

