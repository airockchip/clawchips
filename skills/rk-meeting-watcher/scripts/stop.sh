#!/bin/bash

# 查找进程PID
PID=$(pgrep -f "asr_meeting_watcher")

if [ -n "$PID" ]; then
    echo "正在停止进程 PID: $PID"
    
    # 先尝试 SIGTERM (更常用)
    kill -TERM $PID
    
    # 等待2秒
    sleep 2
    
    # 如果还在运行，强制杀死
    if ps -p $PID > /dev/null 2>&1; then
        echo "进程未响应，强制终止..."
        kill -KILL $PID
    fi
    
    echo "进程已停止"
else
    echo "未找到 asr_meeting_watcher 进程"
fi
