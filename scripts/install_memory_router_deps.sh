#!/usr/bin/env bash

wget https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/claw_agent/embedding/embedding_rknn_server.tgz
tar zxvf embedding_rknn_server.tgz
cd embedding_rknn_server && sudo ./install.sh