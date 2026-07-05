"""桌宠插件配置"""

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class DesktopPetConfig(BaseConfig):
    """桌宠插件配置"""
    config_name: ClassVar[str] = "config"
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
        normal1_image: str = Field(default="", description="闭嘴/静止状态图片路径", label="闭嘴状态图", tag="file")
        normal2_image: str = Field(default="", description="张嘴状态图片路径", label="张嘴状态图", tag="file")
        sleep_image: str = Field(default="", description="睡眠状态图片路径", label="睡眠状态图", tag="file")
        pet_width: int = Field(default=200, description="桌宠宽度基准值（实际按屏幕面积 1% 比例自动缩放，此值仅作 fallback）", label="宽度", tag="performance", ge=64, le=1024)
        pet_height: int = Field(default=200, description="桌宠高度基准值（实际按屏幕面积 1% 比例自动缩放，此值仅作 fallback）", label="高度", tag="performance", ge=64, le=1024)
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
            default=30,
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

    plugin: PluginSection = Field(default_factory=PluginSection)
    pet: PetSection = Field(default_factory=PetSection)
    sleep: SleepSection = Field(default_factory=SleepSection)
    system_monitor: SystemMonitorSection = Field(default_factory=SystemMonitorSection)
    clipboard: ClipboardSection = Field(default_factory=ClipboardSection)
    chat: ChatSection = Field(default_factory=ChatSection)
    screen_watcher: ScreenWatcherSection = Field(default_factory=ScreenWatcherSection)
