"""桌宠插件配置"""

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class DesktopPetConfig(BaseConfig):
    """桌宠插件配置"""
    name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "MoFox 桌宠插件配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件通用配置"""
        enabled: bool = Field(default=True, description="启用桌宠插件", label="启用插件", tag="plugin")
        print_all_logs: bool = Field(
            default=False,
            description="打印所有日志（DEBUG 级别），默认关闭",
            label="打印全部日志",
            tag="debug",
            hint="开启后插件日志级别降为 DEBUG，并以淡蓝色边框包裹输出",
        )

    @config_section("pet", title="桌宠外观", tag="general")
    class PetSection(SectionBase):
        """桌宠外观配置"""
        default_image_dir: str = Field(
            default="assets/default",
            description="默认/白天状态图片文件夹路径（相对于插件目录，目录内图片按文件名排序自动轮播）",
            label="默认图片目录",
            tag="file",
        )
        sleep_image_dir: str = Field(
            default="assets/sleep",
            description="睡眠状态图片文件夹路径（相对于插件目录，进入夜晚模式后自动轮播此目录图片）",
            label="睡眠图片目录",
            tag="file",
        )
        image_switch_interval: float = Field(
            default=3.0,
            description="图片轮播切换间隔（秒）",
            label="图片切换间隔",
            tag="timer",
            ge=0.5,
            le=60.0,
        )
        normal1_image: str = Field(default="", description="闭嘴/静止状态图片路径", label="闭嘴状态图", tag="file")
        normal2_image: str = Field(default="", description="张嘴状态图片路径", label="张嘴状态图", tag="file")
        sleep_image: str = Field(default="", description="睡眠状态图片路径", label="睡眠状态图", tag="file")
        pet_width: int = Field(default=200, description="桌宠宽度基准值（实际按屏幕面积 1% 比例自动缩放，此值仅作 fallback）", label="宽度", tag="performance", ge=64, le=1024)
        pet_height: int = Field(default=200, description="桌宠高度基准值（实际按屏幕面积 1% 比例自动缩放，此值仅作 fallback）", label="高度", tag="performance", ge=64, le=1024)
        position_x: int = Field(default=-1, description="桌宠窗口 X 坐标（-1 表示未设置，启动时居中或上次位置）", label="位置 X", tag="general", ge=-1, le=65535)
        position_y: int = Field(default=-1, description="桌宠窗口 Y 坐标（-1 表示未设置，启动时居中或上次位置）", label="位置 Y", tag="general", ge=-1, le=65535)
        default_image: str = Field(
            default="assets/default_pet.svg",
            description="默认图片路径（兜底，相对于插件目录；支持 SVG 矢量图，推荐使用 SVG 以获得高 DPI 清晰度）",
            label="默认图片",
            tag="file",
        )

    @config_section("sleep", title="昼夜作息", tag="timer")
    class SleepSection(SectionBase):
        """昼夜作息配置"""
        enabled: bool = Field(default=True, description="启用昼夜作息", label="启用昼夜作息", tag="timer")
        sleep_start_hour: int = Field(default=23, description="入睡时间（小时, 0-23）", label="入睡时间", tag="timer", ge=0, le=23)
        wake_start_hour: int = Field(default=7, description="醒来时间（小时, 0-23）", label="醒来时间", tag="timer", ge=0, le=23)

    @config_section("system_monitor", title="系统监控", tag="performance")
    class SystemMonitorSection(SectionBase):
        """系统状态监控配置"""
        enabled: bool = Field(default=True, description="启用系统监控", label="启用系统监控", tag="performance")
        cpu_threshold: float = Field(default=85.0, description="CPU 告警阈值（百分比）", label="CPU 阈值", tag="performance", ge=0.0, le=100.0)
        memory_threshold: float = Field(default=90.0, description="内存告警阈值（百分比）", label="内存阈值", tag="performance", ge=0.0, le=100.0)
        check_interval: int = Field(default=10, description="检查间隔（秒）", label="检查间隔", tag="timer", ge=1)

    @config_section("clipboard", title="剪贴板互动", tag="general")
    class ClipboardSection(SectionBase):
        """剪贴板互动配置"""
        enabled: bool = Field(default=True, description="启用剪贴板监听", label="启用剪贴板监听", tag="general")
        max_content_length: int = Field(default=500, description="剪贴板内容最大长度", label="内容最大长度", tag="performance", ge=1)

    @config_section("chat", title="聊天配置", tag="ai")
    class ChatSection(SectionBase):
        """聊天配置"""
        user_name: str = Field(default="用户", description="用户显示名称", label="用户名称", tag="user")
        pet_name: str = Field(default="桌宠", description="桌宠显示名称", label="桌宠名称", tag="user")
        system_prompt: str = Field(
            default=(
                "你当前正在以\u201c桌面宠物\u201d的形式运行在用户的电脑桌面上。"
                "你可以感知系统状态（CPU/内存使用率）、监听剪贴板内容，"
                "未来还可以通过工具插件获取屏幕截图等能力。"
                "你与用户的关系是桌面陪伴者，用户可以在桌面上直接与你对话。"
                "请以桌宠的身份与用户互动，保持自然、亲近的交流风格。"
            ),
            description="桌宠身份分析提示词，注入到 AI 的 system reminder",
            label="桌宠身份提示词",
            tag="ai",
            input_type="textarea",
            rows=6,
        )
        notification_sound: str = Field(
            default="",
            description="消息提示音音频文件路径（为空则无提示音，推荐 WAV 格式）",
            label="消息提示音",
            tag="notification",
        )
        dialog_auto_hide_sec: float = Field(
            default=10.0,
            description="桌宠对话气泡自动隐藏时间（秒，0 表示不自动隐藏）",
            label="气泡自动隐藏",
            tag="timer",
            ge=0.0,
        )
        show_chat_messages: bool = Field(
            default=False,
            description="在聊天窗口中显示消息气泡（关闭则仅显示输入栏）",
            label="显示消息气泡",
            tag="general",
        )
        show_emoji_in_chat: bool = Field(
            default=True,
            description="表情包优先显示在聊天窗口（即使「显示消息气泡」关闭），避免桌宠气泡一闪而过",
            label="表情包显示在聊天窗口",
            tag="general",
        )
        user_qq_id: str = Field(
            default="",
            description="用户 QQ 号（填了用此 QQ 号作 user_id 隔离上下文，不填用 local_user）",
            label="用户 QQ 号",
            tag="user",
        )
        chat_position_mode: str = Field(
            default="independent",
            description="聊天窗口位置模式：independent=独立不跟随桌宠；follow=拖动桌宠时按偏移跟随",
            label="位置模式",
            tag="general",
        )
        persist_chat_offset: bool = Field(
            default=False,
            description="是否持久化聊天窗口相对桌宠的偏移（重启后恢复）",
            label="持久化偏移",
            tag="general",
        )
        chat_offset_x: int = Field(
            default=0,
            description="聊天窗口相对桌宠的 X 偏移",
            label="偏移 X",
            tag="general",
        )
        chat_offset_y: int = Field(
            default=0,
            description="聊天窗口相对桌宠的 Y 偏移",
            label="偏移 Y",
            tag="general",
        )

    @config_section("theme", title="配色与字体", tag="general")
    class ThemeSection(SectionBase):
        """配色方案与字体配置"""
        preset: str = Field(
            default="mofox_blue",
            description=(
                "配色预设：mofox_blue（淡蓝#9EF6FF+纯白+深色背景，默认） / "
                "mofox_blue_light（淡蓝浅色版） / ocean（海洋深蓝） / "
                "forest（森林绿） / sunset（日落橙） / "
                "aurora（星空极光） / cyber_neon（赛博霓虹） / "
                "amethyst（紫晶幻境） / amber（琥珀暮光） / "
                "emerald（翡翠琉璃） / rose_dawn（玫瑰晨曦-浅色） / "
                "custom（自定义，用下方两项）"
            ),
            label="配色预设",
            tag="general",
        )
        custom_primary: str = Field(
            default="#9EF6FF",
            description="自定义主色 #RRGGBB（仅 preset=custom 时生效）",
            label="自定义主色",
            tag="general",
        )
        custom_surface: str = Field(
            default="#0F1416",
            description="自定义背景色 #RRGGBB（仅 preset=custom 时生效）",
            label="自定义背景色",
            tag="general",
        )
        font_family_mono: str = Field(
            default="",
            description=(
                "英文等宽编程字体族（默认 JetBrains Mono，GitHub 高 star 开源等宽字体，支持连字）。"
                "留空使用默认链：JetBrains Mono → Cascadia Code → Fira Code → Consolas"
            ),
            label="英文等宽字体",
            tag="general",
        )
        font_family_cjk: str = Field(
            default="",
            description=(
                "中文字体族（默认 Ubuntu，即 Ubuntu 终端默认字体）。"
                "留空使用默认链：Ubuntu → Microsoft YaHei → Noto Sans CJK SC"
            ),
            label="中文字体",
            tag="general",
        )
        font_size_scale: float = Field(
            default=1.0,
            description="字号缩放因子（1.0=默认 12px；0.8=偏小；1.25=偏大；1.5=大）。可在托盘菜单热切换，也可在此滑块精细调节。",
            label="字号缩放",
            tag="general",
            ge=0.5,
            le=2.0,
            input_type="slider",
            step=0.05,
        )
        opacity: float = Field(
            default=1.0,
            description="窗口透明度（0.1~1.0）。托盘菜单切换透明度时自动记忆。",
            label="窗口透明度",
            tag="general",
            ge=0.1,
            le=1.0,
            input_type="slider",
            step=0.05,
        )

    @config_section("screen_watcher", title="屏幕监控", tag="ai")
    class ScreenWatcherSection(SectionBase):
        """屏幕监控配置"""
        enabled: bool = Field(
            default=False,
            description="启用定时截图主动监控",
            label="启用截图监控",
            tag="ai",
        )
        interval: int = Field(
            default=300,
            description="截图间隔（秒，最小 5）",
            label="截图间隔",
            tag="timer",
            ge=5,
        )
        vlm_prompt: str = Field(
            default="请描述当前屏幕画面中用户可能在做什么，以便桌宠决定是否主动搭话。",
            description="VLM 识别提示词",
            label="VLM 提示词",
            tag="ai",
            input_type="textarea",
            rows=3,
        )
        snapshot_dir: str = Field(
            default="data/desktop_pet/snapshots",
            description="截图保存目录",
            label="截图目录",
            tag="file",
        )
        max_snapshots_before_purge: int = Field(
            default=50,
            description="累计截图张数上限，达到后批量删除全部并重新累计",
            label="清理阈值",
            tag="performance",
            ge=1,
        )
        group_id: str = Field(
            default="desktop_pet_screenshot",
            description="截图消息伪装的群聊 ID（同一 ID 复用同一对话流）",
            label="群聊 ID",
            tag="ai",
        )
        vlm_max_retries: int = Field(
            default=3,
            description="识图模型请求失败后的最大重试次数（0 表示不重试）",
            label="识图重试次数",
            tag="performance",
            ge=0,
            le=10,
        )
        vlm_retry_delay: float = Field(
            default=3.0,
            description="识图重试的初始等待时间（秒），后续按指数退避递增",
            label="识图重试间隔",
            tag="timer",
            ge=0.0,
            le=60.0,
        )
        vlm_retry_backoff: float = Field(
            default=2.0,
            description="识图重试退避倍数（第 n 次重试等待 delay × backoff^(n-1)）",
            label="退避倍数",
            tag="performance",
            ge=1.0,
            le=5.0,
        )
        # ── 多帧连拍 + 变化筛选 + 拼图 ──
        burst_enabled: bool = Field(
            default=True,
            description="启用多帧连拍 + 变化筛选 + 拼图识别（关闭则回到单张截图模式）",
            label="启用多帧拼图",
            tag="ai",
        )
        burst_frames: int = Field(
            default=6,
            description="每次 tick 连拍张数（2-12）",
            label="连拍张数",
            tag="performance",
            ge=2,
            le=12,
        )
        burst_interval: float = Field(
            default=2.0,
            description="连拍间隔（秒，0.5-10）",
            label="连拍间隔",
            tag="timer",
            ge=0.5,
            le=10.0,
        )
        change_threshold: float = Field(
            default=12.0,
            description="留帧的变化分阈值（0-255，与 dsh-see-now 同口径）；越高只留越大的变化",
            label="变化阈值",
            tag="performance",
            ge=0.0,
            le=255.0,
        )
        still_threshold: float = Field(
            default=1.5,
            description="低于此值判定画面静止，跳过本次识图（0 表示不跳过）",
            label="静止阈值",
            tag="performance",
            ge=0.0,
            le=255.0,
        )
        min_frames: int = Field(
            default=2,
            description="拼图最少帧数（变化帧不足时按分数从高到低补齐）",
            label="最少帧数",
            tag="performance",
            ge=1,
            le=12,
        )
        max_frames: int = Field(
            default=6,
            description="拼图最多帧数（超出时保留变化最大的若干帧，按时间顺序排列）",
            label="最多帧数",
            tag="performance",
            ge=1,
            le=12,
        )
        sheet_cell_width: int = Field(
            default=640,
            description="拼图单格目标宽度（像素）；高度按截图宽高比推导",
            label="拼图单格宽度",
            tag="performance",
            ge=160,
            le=1280,
        )
        skip_still: bool = Field(
            default=True,
            description="画面静止时跳过 VLM 调用（省 token）",
            label="静止时跳过识图",
            tag="performance",
        )

    @config_section("tts", title="TTS 语音", tag="ai")
    class TTSSection(SectionBase):
        """TTS 语音合成配置"""
        enabled: bool = Field(default=True, description="启用 TTS 语音合成（需要 TTS HTTP Server 运行中）", label="启用 TTS", tag="ai")
        endpoint: str = Field(
            default="http://127.0.0.1:8000/router/tts_http_server/api/tts/v1/synthesize",
            description="TTS HTTP 合成接口地址",
            label="TTS 端点",
            tag="ai",
        )
        timeout: float = Field(default=30.0, description="TTS HTTP 请求超时时间（秒）", label="超时时间", tag="timer", ge=1.0, le=120.0)
        mime_type: str = Field(default="audio/wav", description="TTS 音频 MIME 类型", label="音频格式", tag="ai")
        provider: str = Field(default="", description="TTS provider 名称（留空使用服务端默认 provider）", label="TTS Provider", tag="ai")
        volume: float = Field(default=0.8, description="TTS 语音播放音量（0.0~1.0）", label="音量", tag="general", ge=0.0, le=1.0, input_type="slider", step=0.05)
        queue_max_items: int = Field(
            default=8,
            description="语音播放队列上限（超出时丢弃最早的待播语音，防止积压后连续轰炸）",
            label="语音队列上限",
            tag="performance",
            ge=1,
            le=50,
        )
        interrupt_on_new_reply: bool = Field(
            default=False,
            description="收到新的 bot 回复时是否清空未播完的语音队列（开启即打断当前播报）",
            label="新回复打断播报",
            tag="ai",
        )

    @config_section("voice_input", title="语音输入", tag="ai")
    class VoiceInputSection(SectionBase):
        """语音输入配置（录音 → ASR → 以用户名义发送）"""
        enabled: bool = Field(
            default=False,
            description="启动时自动开启语音输入（也可通过托盘菜单随时开关）",
            label="启用语音输入",
            tag="ai",
        )
        device_name: str = Field(
            default="",
            description="麦克风设备名（留空使用系统默认输入设备）",
            label="麦克风设备",
            tag="general",
        )
        sample_rate: int = Field(
            default=16000,
            description="采样率（ASR 推荐 16000）",
            label="采样率",
            tag="performance",
            ge=8000,
            le=48000,
        )
        channels: int = Field(
            default=1,
            description="声道数（单声道即可）",
            label="声道数",
            tag="performance",
            ge=1,
            le=2,
        )
        silence_threshold: int = Field(
            default=500,
            description="静音判定阈值（RMS，0-32767）；环境噪声大时调高",
            label="静音阈值",
            tag="performance",
            ge=0,
            le=32767,
        )
        silence_sec: float = Field(
            default=1.5,
            description="连续静音多久判定一句话结束（秒）",
            label="断句静音时长",
            tag="timer",
            ge=0.3,
            le=10.0,
        )
        min_speech_sec: float = Field(
            default=0.5,
            description="短于此长度的语音片段直接丢弃（防噪声误触）",
            label="最短语音时长",
            tag="timer",
            ge=0.1,
            le=5.0,
        )
        max_record_sec: float = Field(
            default=60.0,
            description="单句最长录音时长（秒），超时强制断句",
            label="单句最长录音",
            tag="timer",
            ge=5.0,
            le=300.0,
        )
        silence_timeout: float = Field(
            default=3.0,
            description="识别完成后等待多久无新内容才投递（秒）",
            label="投递等待时间",
            tag="timer",
            ge=0.5,
            le=30.0,
        )
        send_as_user: bool = Field(
            default=True,
            description="以用户名义发送（关闭则作为系统提醒消息）",
            label="以用户名义发送",
            tag="ai",
        )
        asr_max_retries: int = Field(
            default=2,
            description="ASR 请求失败后的重试次数",
            label="ASR 重试次数",
            tag="performance",
            ge=0,
            le=5,
        )

    @config_section("bilibili", title="B站直播", tag="ai")
    class BilibiliSection(SectionBase):
        """B 站直播弹幕接入配置"""
        enabled: bool = Field(default=False, description="启用 B 站直播弹幕接入", label="启用B站弹幕", tag="ai")
        access_key_id: str = Field(default="", description="AccessKey ID（开放平台 → 应用管理）", label="AccessKey ID", tag="ai")
        access_key_secret: str = Field(default="", description="AccessKey Secret（HMAC-SHA256 签名密钥）", label="AccessKey Secret", tag="ai")
        app_id: int = Field(default=0, description="应用 ID（开放平台创建应用后生成）", label="App ID", tag="ai")
        id_code: str = Field(default="", description="主播身份码（直播姬'主播专用'页面查看）", label="身份码", tag="ai")
        host: str = Field(default="https://live-open.biliapi.com", description="API 主机域名", label="API 主机", tag="ai")
        stream_name: str = Field(default="", description="聊天流显示名（留空用 B站直播间{room_id}）", label="流名称", tag="ai")

    plugin: PluginSection = Field(default_factory=PluginSection)
    pet: PetSection = Field(default_factory=PetSection)
    sleep: SleepSection = Field(default_factory=SleepSection)
    system_monitor: SystemMonitorSection = Field(default_factory=SystemMonitorSection)
    clipboard: ClipboardSection = Field(default_factory=ClipboardSection)
    chat: ChatSection = Field(default_factory=ChatSection)
    theme: ThemeSection = Field(default_factory=ThemeSection)
    screen_watcher: ScreenWatcherSection = Field(default_factory=ScreenWatcherSection)
    tts: TTSSection = Field(default_factory=TTSSection)
    voice_input: VoiceInputSection = Field(default_factory=VoiceInputSection)
    bilibili: BilibiliSection = Field(default_factory=BilibiliSection)
