# MoFox 桌面宠物

一款用于 MoFox 聊天机器人系统的桌面宠物插件。显示一个可以响应聊天消息、监控系统资源并与剪贴板活动交互的互动宠物角色。

## 功能特性

- **透明、无边框、置顶窗口** — 宠物在你工作时始终可见。
- **可拖拽** — 点击并拖拽宠物窗口到屏幕上的任意位置。
- **对话气泡** — 收到的聊天消息以打字机风格的对话气泡显示。
- **系统监控** — 可选的 CPU/内存使用率跟踪，支持可配置阈值。
- **剪贴板监控** — 可选的剪贴板变化检测。
- **日/夜循环** — 宠物根据一天中的时间调整行为。
- **系统托盘** — 右键点击托盘图标进行快捷操作（显示/隐藏、切换日/夜模式、退出）。

## 安装

1.  确保插件目录 `desktop_pet/` 放置在 MoFox 插件目录中。
2.  确保在 MoFox 虚拟环境中已安装 `PySide6>=6.6.0` 和 `psutil>=5.9.0`。
3.  在 MoFox 插件管理界面中启用该插件。

## 配置

| 配置分区 | 字段 | 类型 | 默认值 | 说明 |
|---------|------|------|--------|------|
| `plugin` | `enabled` | bool | true | 启用桌面宠物插件 |
| `pet` | `normal1_image` | str | `""` | 正常/静态状态图片路径 |
| `pet` | `normal2_image` | str | `""` | 活跃/张嘴状态图片路径 |
| `pet` | `sleep_image` | str | `""` | 睡眠状态图片路径 |
| `pet` | `default_image` | str | `""` | 默认/后备图片路径 |
| `pet` | `pet_width` | int | 200 | 窗口/宠物宽度（像素） |
| `pet` | `pet_height` | int | 200 | 窗口/宠物高度（像素） |
| `sleep` | `enabled` | bool | true | 启用日/夜休息行为 |
| `sleep` | `sleep_start_hour` | int | 23 | 就寝时间（0-23） |
| `sleep` | `wake_start_hour` | int | 7 | 起床时间（0-23） |
| `system_monitor` | `enabled` | bool | true | 启用系统监控 |
| `system_monitor` | `cpu_threshold` | float | 85.0 | CPU 告警阈值（%） |
| `system_monitor` | `memory_threshold` | float | 90.0 | 内存告警阈值（%） |
| `system_monitor` | `check_interval` | int | 10 | 检查间隔（秒） |
| `clipboard` | `enabled` | bool | true | 启用剪贴板监控 |
| `clipboard` | `max_content_length` | int | 500 | 剪贴板内容最大长度 |

## 项目结构

```
desktop_pet/
├── __init__.py          # 包初始化，导出 __version__
├── manifest.json        # 插件清单（入口点、依赖）
├── plugin.py            # DesktopPetAdapter + DesktopPetPlugin
├── config.py            # DesktopPetConfig，含 5 个配置分区
├── README.md            # 本文件
├── gui/
│   ├── __init__.py      # 导出 PetWindow、TrayManager、DialogBox
│   ├── pet_window.py    # 透明、无边框、置顶窗口
│   ├── dialog_box.py    # 打字机风格的对话气泡
│   └── tray_menu.py     # 系统托盘图标和右键菜单
└── services/
    ├── __init__.py      # 导出所有服务
    ├── day_night.py     # 日/夜循环服务
    ├── system_monitor.py # CPU/内存监控服务
    └── clipboard_watcher.py # 剪贴板变化检测服务
```

## 开发

- Python 3.11+
- PySide6 用于 GUI
- psutil 用于系统监控
- MoFox 插件系统（BaseAdapter、BasePlugin、BaseService）


## 📄 开源协议

本项目采用 [AGPL-v3.0](LICENSE) 协议。
