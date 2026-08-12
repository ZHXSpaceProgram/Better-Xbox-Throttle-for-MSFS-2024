# MSFS 2024 手柄油门控制器

## 功能介绍

- 基于 XInput + SimConnect 的轻量 Python 工具，可使用 Xbox 兼容手柄控制 Microsoft Flight Simulator 2024 油门。
- 支持普通增减油门、LB 组合键微调以及长按连续变化，并可自动处理非法组合键和手柄断线重连。
- 支持自定义短按次数、长按延迟、变化速率、微调模式等参数。

## 使用方法

- 从[这里](https://github.com/ZHXSpaceProgram/Better-Xbox-Throttle-for-MSFS-2024/releases)下载最新版本。
- 先打开MSFS 2024，然后运行这个程序。
- 取消MSFS 2024中的A/B键油门绑定。如果需要微调，取消LB+A/B的按键绑定。
- 可以通过配置文件 `config.ini` 调整控制参数，修改后重启程序生效。

## 操作说明

- A：加大油门
- B：减小油门
- LB+A / LB+B：微调油门（可通过配置文件开关，默认开）
- Ctrl+C：退出

## 运行源码

```
pip install SimConnect
python main.py
```
