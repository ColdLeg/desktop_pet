# MoFox 桌面宠物

一款用于 MoFox 聊天机器人系统的桌面宠物插件。显示一个可以响应聊天消息、监控系统资源、感知屏幕画面并与剪贴板活动交互的互动宠物角色。

## 功能特性

- **透明、无边框、置顶窗口** — 宠物在你工作时始终可见；窗口不出现在任务栏，关闭操作改为隐藏而非销毁。
- **可拖拽 / 可缩放** — 点击并拖拽宠物窗口到屏幕任意位置；双击进入等比缩放模式（右下角手柄拉伸，尺寸持久化）。
- **图组轮播** — 从 `assets/default/` 与 `assets/sleep/` 目录轮播图片（支持 PNG/JPG/GIF/SVG），间隔可配置；昼夜切换与主动休眠自动切换图组。
- **独立聊天窗口** — 单击宠物切换聊天窗口（MD3 暗色风格），支持消息气泡、表情包渲染、语音气泡、回复引用标记。
- **消息路由** — 聊天窗口可见时回复进 chat 历史；不可见时以打字机气泡显示在桌宠旁。
- **表情包渲染** — 解析 `message_segment` 中的 `emoji` 段（base64 GIF/PNG），在 chat 窗口或桌宠气泡渲染为图片。
- **系统监控** — CPU/内存使用率跟踪，超阈值时注入 system reminder。
- **剪贴板监控** — 检测剪贴板变化，内容注入 system reminder。
- **日/夜循环** — 根据时间切换昼夜模式，状态注入 system reminder。
- **屏幕监控（多帧拼图）** — 定时连拍多张截图，按画面变化量筛选后拼成一张网格图交给 VLM 识别，画面静止时自动跳过以省 token。
- **多 bot 隔离** — 通过 `user_qq_id` 配置隔离不同用户的对话上下文。
- **系统托盘 / 右键菜单** — 主题化菜单：显示/隐藏、透明度、配色方案、字号、位置模式、主动休眠、B站弹幕、语音输入、退出。
- **TTS 语音合成** — 桌宠回复文字的同时，异步调用 TTS HTTP Server 合成语音并**顺位播放**（多段语音依次播完，不互相截断）。
- **语音输入（ASR）** — 菜单开启后录音，自动断句并调用模型转文字，静音几秒后以用户名义发送。
- **B 站直播弹幕** — 内置弹幕接入，可菜单开关，弹幕形成独立群聊流并显示在聊天窗口。

## 安装

1.  确保插件目录 `desktop_pet/` 放置在 MoFox 插件目录中。
2.  在 MoFox 虚拟环境中安装依赖：

```bash
pip install "PySide6>=6.6.0" "psutil>=5.9.0" "pyperclip>=1.8.2" "Pillow>=9.0.0"
# B 站弹幕功能需要（未启用可跳过）
pip install "websockets>=12.0" "httpx>=0.27.0" "brotli>=1.1.0"
```

3.  在 MoFox 插件管理界面中启用该插件。

### 功能与依赖对照

| 功能 | 依赖 | 说明 |
|------|------|------|
| GUI / 录音 / TTS 播放 | `PySide6>=6.6.0` | 含 `QtMultimedia`（`QAudioSource` 录音、`QMediaPlayer` 播放） |
| 系统监控 | `psutil>=5.9.0` | CPU/内存读数 |
| 剪贴板监听 | `pyperclip>=1.8.2` | 剪贴板读取 |
| 截图缩放 / 多帧拼图 / 变化打分 | `Pillow>=9.0.0` | **缺失时自动退回单张截图模式** |
| B 站弹幕 | `websockets` / `httpx` / `brotli` | 二进制协议长连；不启用该功能则无需安装 |
| 语音输入（ASR） | **无额外依赖** | 复用框架 `MediaManager` 的 ASR 链路（httpx） |
| TTS 合成 | **无额外依赖** | 走 HTTP 调用 `tts_http_server` |

> **语音输入（ASR）额外配置**：需要在 `config/model.toml` 的 `[model_tasks.voice]` 中指定一个支持
> `/audio/transcriptions` 接口的模型（如 SiliconFlow 的 `FunAudioLLM/SenseVoiceSmall`），
> 并在对应的 `[[api_providers]]` 中配置 base_url 与 api_key。未配置时语音输入会记录
> `ASR returned empty result` 并静默跳过该段。

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
| `default_image_dir` | str | `assets/default` | 白天/默认状态图片目录（按文件名自然排序轮播） |
| `sleep_image_dir` | str | `assets/sleep` | 睡眠状态图片目录（定时休眠或主动休眠时使用） |
| `image_switch_interval` | float | 3.0 | 图片轮播间隔（秒，0.5~60.0） |
| `normal1_image` | str | `""` | 【兼容】目录为空时的兜底单图 |
| `normal2_image` | str | `""` | 【兼容】保留字段，当前不读取 |
| `sleep_image` | str | `""` | 【兼容】保留字段，当前不读取 |
| `default_image` | str | `assets/default_pet.svg` | 最终兜底单图（目录为空时使用），也用作托盘图标 |
| `pet_width` | int | 200 | 窗口宽度（像素；用户自定义后不再被屏幕比例覆盖） |
| `pet_height` | int | 200 | 窗口高度（像素；与宽度强制 1:1） |
| `position_x` | int | -1 | 窗口 X 坐标（-1 表示未设置，启动时居中） |
| `position_y` | int | -1 | 窗口 Y 坐标（-1 表示未设置，启动时居中） |

> **注**：目录内支持 `.png` / `.jpg` / `.jpeg` / `.gif` / `.svg` / `.bmp` / `.webp`。
> GIF 会以动画方式播放（`QMovie`），SVG 矢量渲染，位图自动缩放。
> 首次启动按主屏可用面积 1% 计算窗口边长；用户双击缩放过之后以保存的尺寸为准。

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
| `show_emoji_in_chat` | bool | true | 表情包优先显示在聊天窗口（即使消息气泡关闭），避免桌宠气泡一闪而过 |
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
| `interval` | int | 300 | 截图间隔（秒，最小 5） |
| `vlm_prompt` | str | （见下） | VLM 识别提示词 |
| `snapshot_dir` | str | `data/desktop_pet/snapshots` | 截图/拼图保存目录 |
| `max_snapshots_before_purge` | int | 50 | 累计张数上限，达到后批量删除全部并重新累计 |
| `group_id` | str | `desktop_pet_screenshot` | 截图消息伪装的群聊 ID（同 ID 复用同一对话流） |
| `vlm_max_retries` | int | 3 | 识图请求失败后的最大重试次数（0 不重试） |
| `vlm_retry_delay` | float | 3.0 | 首次重试等待（秒），后续指数退避 |
| `vlm_retry_backoff` | float | 2.0 | 退避倍数 |
| `burst_enabled` | bool | true | 启用多帧连拍 + 变化筛选 + 拼图（**关闭则回到单张模式**） |
| `burst_frames` | int | 6 | 每次 tick 连拍张数（2–12） |
| `burst_interval` | float | 2.0 | 连拍间隔（秒，0.5–10） |
| `change_threshold` | float | 12.0 | 留帧的变化分阈值（0–255） |
| `still_threshold` | float | 1.5 | 低于此值判定画面静止，跳过识图 |
| `min_frames` | int | 2 | 拼图最少帧数（不足按分数补齐） |
| `max_frames` | int | 6 | 拼图最多帧数 |
| `sheet_cell_width` | int | 640 | 拼图单格宽度（像素） |
| `skip_still` | bool | true | 静止时跳过 VLM 调用（省 token） |

**多帧拼图流程**（默认模式）：
1. 每次 tick 连拍 `burst_frames` 张截图（间隔 `burst_interval` 秒）
2. 每帧降采样为 64×36 HSV 网格，与上一帧计算加权绝对差（0–255）
   - 色相按环形处理，低饱和像素的色相降权
   - 暗场按亮度自动放大（参考亮度 96，上限 1.85×），亮场不变
3. 按 `change_threshold` 筛选留帧：首帧必留，不足 `min_frames` 按分数补齐，超过 `max_frames` 保留变化最大的
4. 所有帧分数都低于 `still_threshold` → 判定画面静止，**跳过本次 VLM**
5. 否则把选中的帧拼成网格图（每格左上角烧「序号 + 时间偏移」），交给 VLM
6. 识别结果注入 `desktop_pet_screen_vision` reminder，并作为群聊消息进入核心

> 变化打分算法移植自 [dsh-see-now](plugins/dsh-see-now) 的 `src/cuts.js`（AGPL-3.0）。

**单张流程**（`burst_enabled = false`）：
1. 定时截取桌宠中心所在屏的当前画面
2. 等比缩放到长边 1080p
3. 保存到 `snapshot_dir`，计数 +1，达上限批量删除
4. 调用 `MediaManager.recognize_media` 走 VLM 识别
5. 识别结果注入 reminder 并投递消息

> **注**：VLM 需在主程序配置 `vlm` 模型集；未配置时识别返回空，跳过本次。

### TTS 语音合成 `[tts]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | true | 启用 TTS 语音合成（需 tts_http_server 插件运行中） |
| `endpoint` | str | `http://127.0.0.1:8000/router/tts_http_server/api/tts/v1/synthesize` | TTS HTTP 合成接口地址 |
| `timeout` | float | 30.0 | TTS HTTP 请求超时时间（秒，1.0~120.0） |
| `mime_type` | str | `audio/wav` | TTS 音频 MIME 类型 |
| `provider` | str | `""` | TTS provider 名称（留空使用服务端默认 provider） |
| `volume` | float | 0.8 | TTS 语音播放音量（0.0~1.0） |
| `queue_max_items` | int | 8 | 待播语音队列上限（超出丢弃最早的） |
| `interrupt_on_new_reply` | bool | false | 收到新回复时清空未播完的队列（开启即打断播报） |

**TTS 流程**：
1. 桌宠回复文字消息时，文字立即显示到 GUI
2. 异步调用 `tts_http_server` 的 HTTP 端点合成语音
3. 合成成功后音频进入**顺位播放队列**，通过 `QMediaPlayer` 内存播放（无临时文件）
4. 一条播完（`EndOfMedia`）才播下一条，**不会互相截断**
5. 合成失败或音频损坏时自动跳过该条，继续播放后续
6. TTS Server 未运行或合成失败时，文字正常显示，不影响聊天

**顺位播放机制**：
- 每条回复在**消息到达时**分配递增序号（`seq`），因此播放顺序 = 消息顺序，不受各条合成耗时差异影响
- GUI 侧按序号串行消费：`seq == next_seq` 就绪入队，`seq > next_seq` 暂存等待，`seq < next_seq` 丢弃
- 合成失败时仍发送空音频占位，推进序号，避免队列永久卡住
- 乱序缓冲 5 秒未推进时触发看门狗，强制跳到最小序号恢复播放

> **注**：需启用 `tts_http_server` 插件，并至少安装一个 TTS provider 插件（如 `tts_voice_plugin-neo`）。

### 语音输入 `[voice_input]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | false | 启动时自动开启（也可托盘菜单随时开关） |
| `device_name` | str | `""` | 麦克风设备名（留空用系统默认） |
| `sample_rate` | int | 16000 | 采样率（ASR 推荐 16000） |
| `channels` | int | 1 | 声道数 |
| `silence_threshold` | int | 500 | 静音判定阈值（RMS，0–32767） |
| `silence_sec` | float | 1.5 | 连续静音多久判定一句结束 |
| `min_speech_sec` | float | 0.5 | 短于此长度的片段丢弃（防噪声） |
| `max_record_sec` | float | 60.0 | 单句最长录音，超时强制断句 |
| `silence_timeout` | float | 3.0 | 识别完成后等待多久无新内容才投递 |
| `send_as_user` | bool | true | 以用户名义发送（关闭则作为系统提醒） |
| `asr_max_retries` | int | 2 | ASR 请求失败重试次数 |

**语音输入流程**：
1. 菜单勾选「语音输入」→ `QAudioSource` 开始采集 16kHz 单声道 PCM
2. 每 100ms 计算 RMS：连续静音 ≥ `silence_sec` 且有效语音 ≥ `min_speech_sec` → 切出一句
3. PCM 封装为 WAV → `MediaManager.recognize_media(media_type="voice")` 走 ASR
4. 识别文本进入缓冲，等待 `silence_timeout` 秒
   - 期间又有新句子识别完成 → 拼接到同一段并重置计时
   - 期间用户手动打字发送 → **取消语音缓冲**，避免重复
5. 超时无新内容 → 以用户名义投递到聊天流（与手打消息同源）

**边界处理**：
- TTS 正在播报时录到的片段会被丢弃（防自激）
- 中途勾选「主动休眠」不影响录音；关闭语音输入立即停止采集
- ASR 失败按 `asr_max_retries` 重试，全失败则丢弃该段并记日志

> **注**：需在 `config/model.toml` 的 `[model_tasks.voice]` 配置支持 `/audio/transcriptions` 的模型。
> **无额外 Python 依赖**（复用 `PySide6.QtMultimedia` + 标准库 `wave` + 框架 httpx）。

### B站直播 `[bilibili]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | false | 启用 B 站直播弹幕接入 |
| `access_key_id` | str | `""` | 开放平台 AccessKey ID |
| `access_key_secret` | str | `""` | 开放平台 AccessKey Secret |
| `app_id` | int | 0 | 应用 ID |
| `id_code` | str | `""` | 主播身份码 |
| `host` | str | `https://live-open.biliapi.com` | API 主机 |
| `stream_name` | str | `""` | 聊天流显示名（留空用 `B站直播间{room_id}`） |

**弹幕流程**：WS 长连 → 鉴权 → 双心跳 → `op=5` 业务包 → `dispatcher` 转 `MessageEnvelope` → `core_sink.send` → 独立群聊流（`platform="live"`, `group_id="live_room"`）+ 聊天窗口显示。

> **注**：需安装 `websockets` / `httpx` / `brotli`。B 站开放平台不允许第三方 bot 发弹幕，本适配器**只入站不出站**。

## 消息路由规则

| 场景 | chat_window 可见 | chat_window 不可见 |
|------|------------------|-------------------|
| 用户发送消息后 bot 回复 | 进 chat 历史 | 进 pet 气泡 |
| bot 回复带表情包 | 进 chat 历史（即使消息气泡关闭，见 `show_emoji_in_chat`） | pet 气泡显示图片 |
| bot 回复带语音 | 进 chat 历史（语音气泡可播放） | pet 气泡显示 `[语音]` 占位 |
| 截图触发 bot 回复 | 进 chat 历史 + 写回私聊 stream | 进 pet 气泡 + 写回私聊 stream |
| 语音输入（ASR） | 以用户消息形式进入聊天流 | 同上（不影响路由） |
| 错误反馈 | 进 chat 历史 + 写日志 | 进 pet 气泡 + 写日志 |

## 上下文注入

桌宠通过 **流私有** system reminder 向 actor 注入以下上下文。
bucket 形如 `stream:{stream_id}:actor`，**仅桌宠自己的聊天流可见**，不会干扰 QQ 等其他聊天流。

| 名称 | Bucket | Consume | 来源 |
|------|--------|---------|------|
| `desktop_pet_identity` | `stream:{sid}:actor` | FOREVER | `chat.system_prompt` |
| `desktop_pet_user_qq` | `stream:{sid}:actor` | FOREVER | `chat.user_qq_id`（非空时） |
| `desktop_pet_tts_hint` | `stream:{sid}:actor` | FOREVER | TTS 启用时，提示优先使用语音 |
| `desktop_pet_day_night` | `stream:{sid}:actor` | ONCE | DayNightService 状态切换时 |
| `desktop_pet_system_status` | `stream:{sid}:actor` | ONCE | SystemMonitorService 超阈值时 |
| `desktop_pet_clipboard` | `stream:{sid}:actor` | ONCE | ClipboardWatcherService 检测到变化时 |
| `desktop_pet_screen_vision` | `stream:{sid}:actor` | ONCE | ScreenWatcherService VLM 识别后 |

> `sid` = `ChatStream.generate_stream_id("desktop_pet", user_id=user_qq_id or "local_user")`。
> 卸载时通过 `clear_by_prefix("stream:{sid}:")` 一次性清理。

## 项目结构

```
desktop_pet/
├── __init__.py          # 包初始化，导出 __version__
├── manifest.json        # 插件清单（入口点、依赖）
├── plugin.py            # DesktopPetAdapter + DesktopPetPlugin
├── config.py            # DesktopPetConfig，含 11 个配置分区
├── tts.py               # TTS HTTP 客户端（异步合成语音）
├── README.md            # 本文件
├── bilibili/            # 内置 B 站直播弹幕（从 bilibili_live_adapter 移植）
│   ├── __init__.py
│   ├── api.py           # 开放平台 HTTP（HMAC 签名）
│   ├── client.py        # WS 长连（鉴权 + 双心跳 + recv）
│   ├── dispatcher.py    # cmd → MessageEnvelope
│   └── proto.py         # 二进制协议打包/解包
├── gui/
│   ├── __init__.py      # 导出 PetWindow、ChatWindow、TrayManager、DialogBox
│   ├── theme.py         # MD3 配色 token 系统 + 字体方案（预设/自定义）
│   ├── svg_assets.py    # 内置 SVG 资源（桌宠形象 + 托盘图标）
│   ├── pet_window.py    # 透明、无边框、置顶窗口；图组轮播；双击缩放
│   ├── chat_window.py   # MD3 聊天窗口；表情/语音气泡；主题配色
│   ├── dialog_box.py    # MD3 打字机气泡（支持表情图片）
│   ├── tray_menu.py     # 系统托盘 + 主题化右键菜单
│   └── voice_recorder.py # GUI 线程录音器（QAudioSource + RMS 断句）
└── services/
    ├── __init__.py
    ├── day_night.py           # 日/夜循环（定时检查 + reminder 注入）
    ├── system_monitor.py      # CPU/内存监控
    ├── clipboard_watcher.py   # 剪贴板检测（被动上下文）
    ├── screen_watcher.py      # 截图监控（多帧拼图 / 单张）
    ├── frame_diff.py          # 画面变化打分（移植自 dsh-see-now）
    ├── sheet_builder.py       # 多帧拼图
    ├── voice_input_service.py # 语音输入编排（录音→ASR→投递）
    └── bilibili_service.py    # B 站弹幕连接生命周期
```

## 开发

- Python 3.11+
- PySide6 用于 GUI 与音频（`QtMultimedia` 的 `QAudioSource` / `QMediaPlayer`）
- psutil 用于系统监控
- Pillow 用于截图缩放、多帧拼图、画面变化打分
- websockets / httpx / brotli 用于 B 站弹幕（可选功能）
- MoFox 插件系统（BaseAdapter、BasePlugin、BaseService）
- 主程序复用：`MediaManager`（VLM/ASR 识别）、`StreamManager`（消息持久化）、`get_system_reminder_store`（上下文注入）

## 更新日志

### 2026-11-16

#### 语音输入（ASR）
- 新增 `services/voice_input_service.py`：录音编排 → ASR → 以用户名义投递
- 新增 `gui/voice_recorder.py`：GUI 线程 `QAudioSource` 采集 + RMS 静音断句
- `config.py` 新增 `[voice_input]` 配置节（11 个字段）
- `tray_menu.py` 新增「语音输入」开关
- 手动打字时自动取消待投递的语音缓冲

#### 屏幕监控：多帧拼图
- 新增 `services/frame_diff.py`：64×36 HSV 网格变化打分（移植自 dsh-see-now）
- 新增 `services/sheet_builder.py`：多帧网格拼图 + 序号标签
- `screen_watcher.py` 新增 `_tick_burst()`：连拍 → 筛选 → 拼图 → VLM
- 画面静止时跳过 VLM 调用；`burst_enabled=false` 可回退单张模式

#### 表情包适配
- 修复 `base64|` 前缀解码（此前表情包静默失败）
- `DialogBox` 支持显示表情图片（`show_content`）
- `chat_window` 输入栏模式下也能显示表情/语音气泡
- 新增 `chat.show_emoji_in_chat` 配置
- 声明适配器 `accept_format`，使 `send_emoji_meme` 等 action 正确保留

#### TTS 顺位播放
- 改为序号驱动的串行队列，多段语音不再互相截断
- 新增 `tts.queue_max_items` / `tts.interrupt_on_new_reply` 配置
- 合成失败发送占位推进序号 + 5 秒看门狗防卡死

#### 主动休眠
- 菜单新增「主动休眠」，与定时休眠并行；任一生效即暂停截图并切换 sleep 图组

#### 图组轮播
- `assets/default` / `assets/sleep` 目录轮播，支持 GIF 动画
- 新增 `pet.default_image_dir` / `pet.sleep_image_dir` / `pet.image_switch_interval`

#### 其他
- 右键菜单改为主题化子菜单（与托盘一致）
- 单击桌宠切换聊天窗口，双击进入等比缩放模式
- system reminder 全部改为**流私有 bucket**，不再干扰其他聊天流
- 修复清屏后重开聊天窗口旧消息复现
- 识图请求失败自动重试（`vlm_max_retries` / `vlm_retry_delay` / `vlm_retry_backoff`）

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

### 2026-07-10

#### Voice 消息处理
- 新增哔哩哔哩直播系统（参考言柒的bilibili_live_adapter https://github.com/tt-P607/bilibili_live_adapter）

## 📄 开源协议

本项目采用 [AGPL-v3.0](LICENSE) 协议。
