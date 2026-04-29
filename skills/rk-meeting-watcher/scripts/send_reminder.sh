#!/bin/bash

openid=$(grep -o '"openid": "[^"]*"' /home/linaro/.openclaw/qqbot/data/known-users.json | head -1 | sed 's/"openid": "//;s/"//')
openclaw message send --channel qqbot --target "qqbot:c2c:$openid" --message '您设置的关键词已触发，请关注会议！'
