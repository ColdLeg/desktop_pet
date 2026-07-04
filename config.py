"""桌宠插件配置"""

from typing import Any, ClassVar

from pydantic import ConfigDict, model_validator

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section

class PluginSection(SectionBase):
    """插件通用配置"""
    enabled: bool = Field(default=True, description="启用桌宠插件")
    print_all_logs: bool = Field(default=False, description="打印所有日志（DEBUG 级别），默认关闭")

class PetSection(SectionBase):
    """桌宠外观配置"""
    normal1_image: str = Field(default="", description="闭嘴/静止状态图片路径")
    normal2_image: str = Field(default="", description="张嘴状态图片路径")
    sleep_image: str = Field(default="", description="睡眠状态图片路径")
    pet_width: int = Field(default=200, description="桌宠宽度")
    pet_height: int = Field(default=200, description="桌宠高度")
    default_image: str = Field(default="assets/default_pet.png", description="默认图片路径（兜底，相对于插件目录）")

class SleepSection(SectionBase):
    """昼夜作息配置"""
    enabled: bool = Field(default=True, description="启用昼夜作息")
    sleep_start_hour: int = Field(default=23, description="入睡时间（小时, 0-23）")
    wake_start_hour: int = Field(default=7, description="醒来时间（小时, 0-23）")

class SystemMonitorSection(SectionBase):
    """系统状态监控配置"""
    enabled: bool = Field(default=True, description="启用系统监控")
    cpu_threshold: float = Field(default=85.0, description="CPU 告警阈值（百分比）")
    memory_threshold: float = Field(default=90.0, description="内存告警阈值（百分比）")
    check_interval: int = Field(default=10, description="检查间隔（秒）")

class ClipboardSection(SectionBase):
    """剪贴板互动配置"""
    enabled: bool = Field(default=True, description="启用剪贴板监听")
    max_content_length: int = Field(default=500, description="剪贴板内容最大长度")

class ChatSection(SectionBase):
    """聊天配置"""
    user_name: str = Field(default="用户", description="用户显示名称")
    pet_name: str = Field(default="桌宠", description="桌宠显示名称")
    system_prompt: str = Field(
        default=(
            "你当前正在以\u201c桌面宠物\u201d的形式运行在用户的电脑桌面上。"
            "你可以感知系统状态（CPU/内存使用率）、监听剪贴板内容，"
            "未来还可以通过工具插件获取屏幕截图等能力。"
            "你与用户的关系是桌面陪伴者，用户可以在桌面上直接与你对话。"
            "请以桌宠的身份与用户互动，保持自然、亲近的交流风格。"
        ),
        description="桌宠身份分析提示词，注入到 AI 的 system reminder",
    )
    notification_sound: str = Field(
        default="",
        description="消息提示音音频文件路径（为空则无提示音，推荐 WAV 格式）",
    )
    dialog_auto_hide_sec: float = Field(
        default=10.0,
        description="桌宠对话气泡自动隐藏时间（秒，0 表示不自动隐藏（怎么看0这也是个bug对吧））",
    )
    show_chat_messages: bool = Field(
        default=False,
        description="在聊天窗口中显示消息气泡（关闭则仅显示输入栏）",
    )

class ProactiveSection(SectionBase):
    """主动聊天配置"""
    enabled: bool = Field(default=True, description="启用定时主动聊天")
    interval: int = Field(default=1800, description="主动聊天间隔时间（秒）")
    prompt: str = Field(
        default="（系统提醒：已经有一段时间没有和用户交流了，请根据当前时间和上下文，主动寻找一个合适的话题与用户聊天。注意不要过于刻意，保持自然。）",
        description="主动聊天提示词",
    )

class DesktopPetConfig(BaseConfig):
    """桌宠插件配置"""
    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "MoFox 桌宠插件配置"

    # 容忍 webui 偶发的扁平化提交（plugin 节字段未包裹到 plugin 下），
    # 由 _normalize_flat_inputs 在校验前归并到对应子节。
    model_config = ConfigDict(extra="ignore")

    @config_section("plugin")
    class _PluginSection(PluginSection):
        pass

    @config_section("pet")
    class _PetSection(PetSection):
        pass

    @config_section("sleep")
    class _SleepSection(SleepSection):
        pass

    @config_section("system_monitor")
    class _SystemMonitorSection(SystemMonitorSection):
        pass

    @config_section("clipboard")
    class _ClipboardSection(ClipboardSection):
        pass

    @config_section("chat")
    class _ChatSection(ChatSection):
        pass

    @config_section("proactive")
    class _ProactiveSection(ProactiveSection):
        pass

    plugin: PluginSection = Field(default_factory=PluginSection)
    pet: PetSection = Field(default_factory=PetSection)
    sleep: SleepSection = Field(default_factory=SleepSection)
    system_monitor: SystemMonitorSection = Field(default_factory=SystemMonitorSection)
    clipboard: ClipboardSection = Field(default_factory=ClipboardSection)
    chat: ChatSection = Field(default_factory=ChatSection)
    proactive: ProactiveSection = Field(default_factory=ProactiveSection)

    @model_validator(mode="before")
    @classmethod
    def _normalize_flat_inputs(cls, data: Any) -> Any:
        """把顶层扁平出现的 section 字段归并到对应子节字典。

        webui 在某些场景下会以扁平结构提交（如 {"enabled": True, "print_all_logs": True}），
        这里将其归并到 {"plugin": {...}} 等结构，避免 extra="forbid" 拒绝。
        """
        if not isinstance(data, dict):
            return data

        result = dict(data)
        # 遍历每个 section 字段，收集属于它的扁平键
        for section_name, field_info in cls.model_fields.items():
            section_type = getattr(field_info, "annotation", None)
            section_fields = getattr(section_type, "model_fields", None)
            if not section_fields:
                continue
            target = result.get(section_name)
            if not isinstance(target, dict):
                target = {}
            moved = False
            for fname in section_fields:
                if fname in result:
                    # 顶层扁平出现了属于本节的字段，归并到子节字典
                    target[fname] = result.pop(fname)
                    moved = True
            if moved:
                result[section_name] = target
        return result
