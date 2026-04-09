## RK-IVA

### 环境要求

RK3588开发板内置摄像头或外接USB摄像头

### 依赖安装

下载安装依赖包，确保`<workspace>/skills/rk-iva/scripts/rockx_rk3588_linux_aarch64`目录存在

一键下载安装
```
bash scripts/install.sh
```

也可以手动下载后解压到/userdata/skills/rk-iva/目录下
```
https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/claw_agent/skills/rk-iva/rockx_rk3588_linux_aarch64.tgz
tar zxvf rockx_rk3588_linux_aarch64.tgz -C /userdata/skills/rk-iva/
```

### 调用示例

- 对摄像头做一帧目标检测

- 抓拍一张摄像头图像做目标检测