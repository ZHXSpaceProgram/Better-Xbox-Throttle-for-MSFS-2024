# MSFS 2024 手柄油门控制器

## 功能介绍

- 基于 XInput + SimConnect 的轻量 Python 工具，可使用 Xbox 手柄控制 Microsoft Flight Simulator 2024 油门。
- 支持普通增减油门、LB 组合键微调以及长按连续变化，并可自动处理非法组合键和手柄断线重连。
- 支持自定义短按次数、长按延迟、变化速率、微调模式等参数。

## 使用方法

- 从[这里](https://github.com/ZHXSpaceProgram/Better-Xbox-Throttle-for-MSFS-2024/releases)下载最新版本。
- 先打开MSFS 2024，然后运行这个程序。
- 取消MSFS 2024中的A/B键油门绑定。如果需要微调功能，取消LB+A/B按键绑定；如果需要刹车功能，取消X键刹车绑定。

## 操作说明

- A：加大油门
- B：减小油门
- LB+A / LB+B：微调油门（可通过配置文件开关，默认开）
- X：刹车（可通过配置文件开关，默认开，短按增大刹车力度，长按保持，松开衰减）
- Ctrl+C：退出

## 高级设置

- 可以通过配置文件 `config.ini` 调整控制参数，修改后重启程序生效。
- 建议配合 [Input Viewer](https://github.com/spitice/msfs-input-viewer/releases) 插件使用。
- 运行源码：
    ```
    pip install SimConnect
    python main.py
    ```
