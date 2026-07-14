# MoFox 桌面宠物

一款用于 MoFox 聊天机器人系统的桌面宠物插件。显示一个可以响应聊天消息、监控系统资源、感知屏幕画面并与剪贴板活动交互的互动宠物角色。

## 功能特性

- **透明、无边框、置顶窗口** — 宠物在你工作时始终可见；窗口不出现在任务栏，关闭操作改为隐藏而非销毁。
- **可拖拽** — 点击并拖拽宠物窗口到屏幕任意位置；多屏环境下自动钳制到所在屏幕。
- **独立聊天窗口** — 双击宠物打开 MD3 暗色风格的聊天窗口，支持消息气泡、表情包渲染、回复引用标记。
- **消息路由** — 聊天窗口可见时回复进 chat 历史；不可见时以打字机气泡显示在桌宠旁。
- **表情包渲染** — 解析 `message_segment` 中的 `emoji` 段（base64 GIF/PNG），在 chat 窗口渲染为图片气泡。
- **系统监控** — CPU/内存使用率跟踪，超阈值时注入 actor system reminder。
- **剪贴板监控** — 检测剪贴板变化，内容注入 actor system reminder。
- **日/夜循环** — 根据时间切换昼夜模式，状态注入 actor system reminder。
- **屏幕监控** — 定时截图桌宠所在屏，VLM 识别后走群聊 DFC 完整链路（sub→actor），主动搭话。
- **多 bot 隔离** — 通过 `user_qq_id` 配置隔离不同用户的对话上下文。
- **系统托盘** — 右键托盘图标或桌宠本身，进行快捷操作（显示/隐藏、透明度、位置模式、退出）。
- **TTS 语音合成** — 桌宠回复文字的同时，异步调用 TTS HTTP Server 合成语音并播放（需启用 `tts_http_server` 插件）。

## 安装

1.  确保插件目录 `desktop_pet/` 放置在 MoFox 插件目录中。
2.  确保在 MoFox 虚拟环境中已安装 `PySide6>=6.6.0` 和 `psutil>=5.9.0`。
3.  截图监控功能需要 `Pillow` 用于图片缩放。
4.  在 MoFox 插件管理界面中启用该插件。

## 配置

配置文件位于 `config/plugins/desktop_pet/config.toml`，首次启动自动生成。

### 通用配置 `[plugin]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | true | 启用桌宠插件 |
| `print_all_logs` | bool | false | 打印 DEBUG 级别日志，并以淡蓝色边框包裹输出 |

### 桌宠外观 `[pet]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `normal1_image` | str | `""` | 闭嘴/静止状态图片路径 |
| `normal2_image` | str | `""` | 张嘴状态图片路径 |
| `sleep_image` | str | `""` | 睡眠状态图片路径 |
| `default_image` | str | `assets/default_pet.svg` | 默认/后备图片，支持 SVG |
| `pet_width` | int | 200 | 窗口宽度（像素，会被屏幕面积比例覆盖） |
| `pet_height` | int | 200 | 窗口高度（像素，会被屏幕面积比例覆盖） |

> **注**：实际窗口尺寸按主屏可用面积 1% 自动计算（PetWindow）或 1.8%（ChatWindow），跨分辨率保持观感一致，覆盖 `pet_width`/`pet_height` 配置值。

### 昼夜作息 `[sleep]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | true | 启用昼夜作息 |
| `sleep_start_hour` | int | 23 | 入睡时间（0-23） |
| `wake_start_hour` | int | 7 | 醒来时间（0-23） |

### 系统监控 `[system_monitor]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | true | 启用系统监控 |
| `cpu_threshold` | float | 85.0 | CPU 告警阈值（%） |
| `memory_threshold` | float | 90.0 | 内存告警阈值（%） |
| `check_interval` | int | 10 | 检查间隔（秒） |

超阈值时注入 `desktop_pet_system_status` system reminder 到 actor 上下文。

### 剪贴板互动 `[clipboard]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | true | 启用剪贴板监听 |
| `max_content_length` | int | 500 | 剪贴板内容最大长度（超出截断） |

剪贴板变化时注入 `desktop_pet_clipboard` system reminder。

### 聊天配置 `[chat]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_name` | str | `用户` | 用户显示名称 |
| `pet_name` | str | `桌宠` | 桌宠显示名称 |
| `system_prompt` | str | （见下） | 桌宠身份提示词，注入 actor system reminder |
| `notification_sound` | str | `""` | 消息提示音路径（WAV） |
| `dialog_auto_hide_sec` | float | 10.0 | 桌宠气泡自动隐藏时间（秒，0 不隐藏） |
| `show_chat_messages` | bool | false | 聊天窗口是否默认显示消息气泡区（关闭时仅显示输入栏，收到 bot 消息会自动展开） |
| `user_qq_id` | str | `""` | 用户 QQ 号，用作 stream user_id 隔离上下文；不填用 `local_user` |
| `chat_position_mode` | str | `independent` | 聊天窗口位置模式：`independent`=独立不跟随；`follow`=拖动桌宠时按偏移跟随 |
| `persist_chat_offset` | bool | false | 是否持久化聊天窗口相对桌宠的偏移（重启后恢复） |
| `chat_offset_x` | int | 0 | 聊天窗口相对桌宠的 X 偏移 |
| `chat_offset_y` | int | 0 | 聊天窗口相对桌宠的 Y 偏移 |

### 配色与字体 `[theme]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `preset` | str | `mofox_blue` | 配色预设：`mofox_blue`（淡蓝#9EF6FF+纯白+深色背景）/ `mofox_blue_light`（淡蓝浅色版）/ `ocean`（海洋深蓝）/ `forest`（森林绿）/ `sunset`（日落橙）/ `custom`（自定义） |
| `custom_primary` | str | `#9EF6FF` | 自定义主色 #RRGGBB（仅 preset=custom 时生效） |
| `custom_surface` | str | `#0F1416` | 自定义背景色 #RRGGBB（仅 preset=custom 时生效） |
| `font_family_mono` | str | `""` | 英文等宽编程字体族（默认 JetBrains Mono，GitHub 高 star 开源等宽字体，支持连字）。留空用默认链 |
| `font_family_cjk` | str | `""` | 中文字体族（默认 Ubuntu，即 Ubuntu 终端默认字体）。留空用默认链 |

**配色方案**：
- 默认 `mofox_blue` 采用 `print_all_logs` 边框色 `#9EF6FF` 淡蓝 + 纯白文字 + 深色背景，与调试日志视觉统一。
- `custom` 模式：用户提供 primary/surface 两个关键色，自动推导完整 MD3 token 集（容器色阶、前景对比色、错误色等）。
- 运行时可通过托盘菜单「配色方案」切换，实时刷新所有 GUI 窗口并持久化。

**字体方案**：
- 英文等宽：JetBrains Mono（GitHub star 数高的开源等宽编程字体，支持连字），回退链 Cascadia Code → Fira Code → Consolas。
- 中文：Ubuntu（用户指定，Ubuntu 终端默认字体），回退链 Microsoft YaHei → Noto Sans CJK SC。
- 可在配置中自定义字体族名。

### 屏幕监控 `[screen_watcher]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | false | 启用定时截图主动监控 |
| `interval` | int | 30 | 截图间隔（秒，最小 5） |
| `vlm_prompt` | str | （见下） | VLM 识别提示词 |
| `snapshot_dir` | str | `data/desktop_pet/snapshots` | 截图保存目录 |
| `max_snapshots_before_purge` | int | 50 | 累计张数上限，达到后批量删除全部并重新累计 |
| `group_id` | str | `desktop_pet_screenshot` | 截图消息伪装的群聊 ID（同 ID 复用同一对话流） |

**截图流程**：
1. 定时截取桌宠中心所在屏的当前画面
2. 等比缩放到长边 1080p
3. 保存到 `snapshot_dir`，计数 +1，达上限批量删除
4. 调用主程序 `MediaManager.recognize_media` 走 VLM 识别
5. 识别结果注入 `desktop_pet_screen_vision` system reminder
6. 识别文本作为群聊消息进入核心，走完整 DFC 链路（sub 判断 → actor 判断 → 回复）
7. 截图触发的 bot 回复会写回用户私聊 stream 历史，chat 重开时可见

> **注**：VLM 需在主程序配置 `vlm` 模型集；未配置时识别返回空，跳过本帧。

### TTS 语音合成 `[tts]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | true | 启用 TTS 语音合成（需 tts_http_server 插件运行中） |
| `endpoint` | str | `http://127.0.0.1:8000/router/tts_http_server/api/tts/v1/synthesize` | TTS HTTP 合成接口地址 |
| `timeout` | float | 30.0 | TTS HTTP 请求超时时间（秒，1.0~120.0） |
| `mime_type` | str | `audio/wav` | TTS 音频 MIME 类型 |
| `provider` | str | `""` | TTS provider 名称（留空使用服务端默认 provider） |
| `volume` | float | 0.8 | TTS 语音播放音量（0.0~1.0） |

**TTS 流程**：
1. 桌宠回复文字消息时，文字立即显示到 GUI
2. 异步调用 `tts_http_server` 的 HTTP 端点合成语音
3. 合成成功后音频通过 `QMediaPlayer` 内存播放（无临时文件）
4. TTS Server 未运行或合成失败时，文字正常显示，不影响聊天

> **注**：需启用 `tts_http_server` 插件，并至少安装一个 TTS provider 插件（如 `tts_voice_plugin-neo`）。

## 消息路由规则

| 场景 | chat_window 可见 | chat_window 不可见 |
|------|------------------|-------------------|
| 用户发送消息后 bot 回复 | 进 chat 历史 | 进 pet 气泡 |
| 截图触发 bot 回复 | 进 chat 历史 + 写回私聊 stream | 进 pet 气泡 + 写回私聊 stream |
| 错误反馈 | 进 chat 历史 + 写日志 | 进 pet 气泡 + 写日志 |

## 上下文注入

桌宠通过 system reminder 向 actor 注入以下上下文：

| 名称 | Bucket | Consume | 来源 |
|------|--------|---------|------|
| `desktop_pet_identity` | ACTOR | FOREVER | `chat.system_prompt` |
| `desktop_pet_tool_restrictions` | ACTOR | FOREVER | `DesktopPetAdapter.start()` 注入，告知 AI 不要使用表情包相关工具 |
| `desktop_pet_user_qq` | ACTOR | FOREVER | `chat.user_qq_id`（非空时） |
| `desktop_pet_day_night` | ACTOR | ONCE | DayNightService 状态切换时 |
| `desktop_pet_system_status` | ACTOR | ONCE | SystemMonitorService 超阈值时 |
| `desktop_pet_clipboard` | ACTOR | ONCE | ClipboardWatcherService 检测到变化时 |
| `desktop_pet_screen_vision` | ACTOR | ONCE | ScreenWatcherService VLM 识别后 |

## 项目结构

```
desktop_pet/
├── __init__.py          # 包初始化，导出 __version__
├── manifest.json        # 插件清单（入口点、依赖）
├── plugin.py            # DesktopPetAdapter + DesktopPetPlugin
├── config.py            # DesktopPetConfig，含 9 个配置分区（含 theme、tts）
├── tts.py               # TTS HTTP 客户端（异步合成语音）
├── README.md            # 本文件
├── gui/
│   ├── __init__.py      # 导出 PetWindow、ChatWindow、TrayManager、DialogBox
│   ├── theme.py         # MD3 配色 token 系统 + 字体方案（预设/自定义）
│   ├── svg_assets.py    # 内置 SVG 资源（桌宠形象 + 托盘图标）
│   ├── pet_window.py    # 透明、无边框、置顶窗口；SVG 矢量；智能上下/左右定位
│   ├── chat_window.py   # MD3 暗色聊天窗口；主题配色；等宽/Ubuntu 字体
│   ├── dialog_box.py    # MD3 打字机风格对话气泡；主题配色
│   └── tray_menu.py     # 系统托盘（内置 SVG 图标）+ 配色方案切换
└── services/
    ├── __init__.py      # 导出所有服务
    ├── day_night.py     # 日/夜循环服务（定时检查 + reminder 注入）
    ├── system_monitor.py # CPU/内存监控服务（非阻塞 to_thread）
    ├── clipboard_watcher.py # 剪贴板检测（source 区分 + 主动消息投递）
    └── screen_watcher.py # 定时截图 VLM 识别服务
```

## 开发

- Python 3.11+
- PySide6 用于 GUI
- psutil 用于系统监控
- Pillow 用于截图缩放（screen_watcher 服务）
- MoFox 插件系统（BaseAdapter、BasePlugin、BaseService）
- 主程序复用：`MediaManager`（VLM 识别）、`StreamManager`（消息持久化）、`get_system_reminder_store`（上下文注入）

## 更新日志

### 2026-07-09

#### TTS 语音合成接入
- 新增 `tts.py`：轻量异步 TTS HTTP 客户端，复用 `tts_http_server` 端点合成语音
- `config.py` 新增 `TTSSection` 配置段（enabled / endpoint / timeout / mime_type / provider / volume）
- `plugin.py` 新增 TTS 播放链路：
  - `__init__` 新增 `_tts_player` / `_tts_audio_output` / `_tts_buffer` 实例变量
  - `_gui_main` 初始化 `QMediaPlayer` + `QAudioOutput`（含 ImportError / Exception 处理）
  - `_poll_out_queue` 新增 `play_tts` action，提取为独立 `_play_tts_audio` 方法（内存播放，无临时文件）
  - `_send_platform_message` 异步触发 `_synthesize_and_queue_tts`（非阻塞，不延迟文字显示）
  - `stop` 清理 TTS 播放器引用
- `manifest.json` 声明对 `tts_http_server` 插件的依赖
- `provider` 默认值改为空字符串（使用服务端默认 provider，而非不存在的 `qwen_tts`）

### 2026-07-10

#### Voice 消息处理
- `_send_platform_message` 新增 `voice` 类型 message_segment 解码与播放
- 新增 `_decode_voice_data` 静态方法：base64 解码音频数据（逻辑同 `_decode_emoji_data`，不支持 data URL 格式）
- voice 音频解码后通过 `out_queue` 以 `play_tts` action 发送到 GUI 线程播放，复用已有 `_play_tts_audio` 方法
- 当消息同时包含 text 和 voice 时，跳过桌宠自己的 TTS 合成（`not voice_bytes` 条件），避免双重音频
- 修复 `_send_platform_message` 入口日志打印完整 envelope（含 base64 音频数据）导致日志膨胀
- 修复 `_write_back_to_private_stream` 中 `add_sent_message_to_history` 返回协程时未 await

## 📄 开源协议

本项目采用 [AGPL-v3.0](LICENSE) 协议。
