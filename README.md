# MSFS 2024 手柄油门控制器

一个基于 **XInput + SimConnect** 的轻量 Python 工具，用 Xbox 兼容手柄控制《Microsoft Flight Simulator 2024》油门。程序支持普通增减油门、LB 组合微调、短按/长按，并会自动处理非法组合键与手柄断线重连。支持自定义短按次数、长按延迟、变化速率、微调模式等参数。

## 使用方法

- 先打开MSFS 2024，然后运行程序。
- 可以通过配置文件 `config.ini` 调整控制参数。

## 操作说明

- A：加大油门
- B：减小油门
- LB+A / LB+B：微调油门
- Ctrl+C：退出

## 运行源码

```
pip install SimConnect
python main.py
```