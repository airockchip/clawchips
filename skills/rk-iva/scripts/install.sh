#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/userdata/skills/rk-iva/"
if [[ ! -d "$INSTALL_DIR" ]]; then
  mkdir -p "$INSTALL_DIR"
fi
cd $SCRIPT_DIR

if [[ -f rockx_rk3588_linux_aarch64.tgz ]]; then
  rm -f rockx_rk3588_linux_aarch64.tgz
fi

wget https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/claw_agent/skills/rk-iva/rockx_rk3588_linux_aarch64.tgz
tar zxvf rockx_rk3588_linux_aarch64.tgz -C $INSTALL_DIR

cd -

echo "install finished“
echo "install directory: $INSTALL_DIR"