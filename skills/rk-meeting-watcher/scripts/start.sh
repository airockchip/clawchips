#!/bin/bash

# python3 asr_meeting_watcher.py --langex-mode Chinese "$1" 
nohup python3 asr_meeting_watcher.py --langex-mode Chinese "$1" > /dev/null 2>&1 &

