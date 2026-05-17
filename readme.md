# Linux PC 上的互传联盟

### 基于 **Python** 的互传联盟实现，使用国产安卓手机内置的互传功能直接传输文件。

## 功能

- 设备发现
- 文件接收
- 文件发送（待实现）

## 系统要求

- Linux 系统（Ubuntu/Debian）
- BlueZ >= 5.84-4
- Python 3.14 
- AX210（其他蓝牙模块请自行测试）


#### 安装 BlueZ（Ubuntu/Debian）

```bash
sudo apt update
sudo apt install bluez
sudo systemctl stop bluetooth
sudo bluetoothd -n -E
```

#### Python 依赖
```bash
dbus-python
websockets
```

## 启动

```bash
python3 receiver.py
```

## 演示
<img src="demo.jpg" alt="描述" width="300">


## 参考项目

- [CatShare](https://github.com/kmod-midori/CatShare) 
