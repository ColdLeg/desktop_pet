# -*- coding: utf-8 -*-
"""桌宠独立聊天窗口 — Material Design 3 风格。

配色与字体均从 config.theme 读取（见 gui/theme.py），支持运行时切换。
提供输入框和发送按钮，用户输入通过 signal 传递给适配器。
show_chat_messages 开启时同时显示消息气泡；关闭时收到 bot 消息临时展开。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QPropertyAnimation,
    QEasingCurve,
    QPoint,
    QSize,
    Qt,
    Signal,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import QSoundEffect
    _HAS_QSOUND = True
except ImportError:
    _HAS_QSOUND = False

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtCore import QUrl as _QUrl, QBuffer as _QBuffer, QIODevice as _QIODevice
    _HAS_MEDIA = True
except ImportError:
    _HAS_MEDIA = False

from .theme import get_font_family, get_font_size_scale, get_theme, ColorTokens

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


# ============================================================================
# 语音气泡控件（QQ 风格）
# ============================================================================

class VoiceBubbleWidget(QFrame):
    """QQ 风格语音消息气泡。

    包含播放/暂停按钮、可拖动进度条、时长标签。
    使用 QMediaPlayer + QAudioOutput 播放在内存中解码的音频数据。
    """

    def __init__(
        self,
        audio_bytes: bytes,
        theme_tokens,
        scale: float = 1.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._audio_bytes = audio_bytes
        self._tokens = theme_tokens
        self._scale = scale
        self._playing = False
        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._progress_timer: QTimer | None = None
        self._duration_ms: int = 0
        self._seeking: bool = False
        self._temp_file: str = ""

        self._build_ui()
        self._init_player()

    def _build_ui(self) -> None:
        t = self._tokens
        s = self._scale

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 10, 4)
        layout.setSpacing(6)

        # 播放/暂停按钮
        self._play_btn = QPushButton("\u25b6")
        self._play_btn.setObjectName("voice_play_btn")
        self._play_btn.setFixedSize(int(30 * s), int(30 * s))
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {t.primary}; color: {t.on_primary};"
            f"  border: none; border-radius: {int(15 * s)}px;"
            f"  font-size: {max(8, int(10 * s))}px;"
            f"}}"
            f"QPushButton:hover {{ background-color: {t.on_primary_container}; }}"
        )
        self._play_btn.clicked.connect(self._toggle_play)
        layout.addWidget(self._play_btn)

        # 进度条
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setObjectName("voice_slider")
        self._slider.setRange(0, 1000)
        self._slider.setValue(0)
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._slider.setStyleSheet(
            f"QSlider::groove:horizontal {{"
            f"  background: {t.outline_variant}; height: {max(2, int(3 * s))}px;"
            f"  border-radius: 2px;"
            f"}}"
            f"QSlider::handle:horizontal {{"
            f"  background: {t.primary}; width: {max(6, int(10 * s))}px;"
            f"  margin: {max(-3, int(-4 * s))}px 0;"
            f"  border-radius: {max(3, int(5 * s))}px;"
            f"}}"
            f"QSlider::sub-page:horizontal {{"
            f"  background: {t.primary}; border-radius: 2px;"
            f"}}"
        )
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._slider, stretch=1)

        # 时长标签
        self._time_label = QLabel("00:00")
        self._time_label.setObjectName("voice_time")
        self._time_label.setStyleSheet(
            f"color: {t.on_surface_variant}; background: transparent;"
            f" font-size: {max(7, int(9 * s))}px;"
        )
        layout.addWidget(self._time_label)

        self.setFixedWidth(int(220 * s))
        self.setStyleSheet(
            f"background-color: {t.bubble_bot_bg};"
            f" border-radius: {int(12 * s)}px;"
        )

    # ---- 音频格式检测与转码 ----

    # 常见 AI 生成语音格式的 magic bytes 匹配
    _AUDIO_MAGIC: dict[bytes, str] = {
        b"#!SILK": "silk",       # SILK V3 (微信/QQ 语音)
        b"#!AMR": "amr",         # AMR-NB/AMR-WB
        b"RIFF": "wav",          # WAV
        b"OggS": "ogg",          # OGG / OPUS
        b"ID3": "mp3",           # MP3 (ID3 tag)
        b"\xff\xfb": "mp3",      # MP3 (MPEG1 Layer3)
        b"\xff\xf3": "mp3",      # MP3 (MPEG2 Layer3)
        b"\xff\xf2": "mp3",      # MP3 (MPEG2.5 Layer3)
        b"fLaC": "flac",         # FLAC
        b"\x00\x00\x01\xba": "mpg",  # MPEG-PS
        b"\x00\x00\x01\xb3": "mpg",  # MPEG-ES
    }

    @staticmethod
    def _detect_audio_format(data: bytes) -> str:
        """根据文件头 magic bytes 检测音频格式。"""
        for magic, fmt in VoiceBubbleWidget._AUDIO_MAGIC.items():
            if data.startswith(magic):
                return fmt
        return "bin"  # 未知格式

    @staticmethod
    def _find_ffmpeg() -> str | None:
        """在系统 PATH 中查找 ffmpeg。"""
        import shutil
        return shutil.which("ffmpeg")

    @staticmethod
    def _convert_to_wav(input_path: str, output_path: str, fmt_hint: str = "bin") -> str | None:
        """用 ffmpeg 将任意音频转为 16kHz mono WAV。"""
        ffmpeg = VoiceBubbleWidget._find_ffmpeg()
        if not ffmpeg:
            return None
        import subprocess
        import os
        # SILK/AMR 等非标准格式需指定输入格式或让 ffmpeg 自动探测
        try:
            # 先尝试自动探测（-f 不指定，ffmpeg 按扩展名/内容探测）
            subprocess.run(
                [ffmpeg, "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-f", "wav", output_path],
                capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                return output_path
        except Exception:
            pass
        # 回退：尝试指定常见输入格式
        try:
            for try_fmt in (fmt_hint, "silk", "amr", "ogg", "mp3", "wav"):
                if try_fmt == "bin":
                    continue
                subprocess.run(
                    [ffmpeg, "-y", "-f", try_fmt, "-i", input_path, "-ar", "16000", "-ac", "1", "-f", "wav", output_path],
                    capture_output=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                    return output_path
        except Exception:
            pass
        return None

    def _init_player(self) -> None:
        """初始化音频播放器。

        策略：
        1. 检测音频格式（magic bytes）
        2. 有 ffmpeg → 转为 16kHz mono WAV 再播放
        3. 无 ffmpeg → 直接存为已知扩展名让 QMediaPlayer 尝试解码
        4. 未知格式 / 解码失败 → 禁用播放按钮
        """
        if not _HAS_MEDIA or not self._audio_bytes:
            self._play_btn.setEnabled(False)
            return
        try:
            import tempfile
            import os

            fmt = self._detect_audio_format(self._audio_bytes)
            suffix_map = {
                "silk": ".silk", "amr": ".amr", "wav": ".wav",
                "ogg": ".ogg", "mp3": ".mp3", "flac": ".flac",
                "mpg": ".mpg", "bin": ".bin",
            }
            suffix = suffix_map.get(fmt, ".bin")

            # 写入原始数据到临时文件
            fd, raw_path = tempfile.mkstemp(suffix=suffix, prefix="mofox_voice_")
            os.write(fd, self._audio_bytes)
            os.close(fd)

            play_path = raw_path

            # 尝试用 ffmpeg 转为 WAV（Windows 上 QMediaPlayer 解码有限）
            ffmpeg = self._find_ffmpeg()
            if ffmpeg and fmt != "wav":
                fd2, wav_path = tempfile.mkstemp(suffix=".wav", prefix="mofox_voice_conv_")
                os.close(fd2)
                converted = self._convert_to_wav(raw_path, wav_path, fmt_hint=fmt)
                if converted:
                    play_path = converted
                    # 清理原始文件
                    try:
                        os.unlink(raw_path)
                    except Exception:
                        pass
                    self._temp_file = wav_path
                else:
                    self._temp_file = raw_path
            else:
                self._temp_file = raw_path

            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._audio_output.setVolume(1.0)
            self._player.setAudioOutput(self._audio_output)
            self._player.setSource(QUrl.fromLocalFile(play_path))

            # 获取时长
            self._player.durationChanged.connect(self._on_duration_changed)
            self._player.playbackStateChanged.connect(self._on_state_changed)

            # 监听错误（格式不支持等），回退显示
            self._player.errorOccurred.connect(self._on_playback_error)

            # 进度更新定时器
            self._progress_timer = QTimer(self)
            self._progress_timer.timeout.connect(self._update_slider)
            self._progress_timer.start(200)
        except Exception:
            self._play_btn.setEnabled(False)

    def _on_playback_error(self, error, error_string: str) -> None:
        """QMediaPlayer 解码失败 → 禁用播放按钮并在标签显示错误。"""
        self._play_btn.setEnabled(False)
        self._time_label.setText("解码失败")
        self._time_label.setStyleSheet(
            f"color: {self._tokens.error}; background: transparent;"
            f" font-size: {max(7, int(9 * self._scale))}px;"
        )

    def _on_duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0:
            self._duration_ms = duration_ms
            self._time_label.setText(self._format_time(duration_ms))

    def _on_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._playing = False
            self._play_btn.setText("\u25b6")
            self._slider.setValue(0)

    def _toggle_play(self) -> None:
        if not self._player:
            return
        if self._playing:
            self._player.pause()
            self._playing = False
            self._play_btn.setText("\u25b6")
        else:
            self._player.play()
            self._playing = True
            self._play_btn.setText("\u23f8")

    def _update_slider(self) -> None:
        if self._seeking or not self._player or self._duration_ms <= 0:
            return
        pos = self._player.position()
        val = int(pos / self._duration_ms * 1000) if self._duration_ms > 0 else 0
        self._slider.setValue(min(val, 1000))
        self._time_label.setText(self._format_time(pos))

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_released(self) -> None:
        self._seeking = False
        if self._player and self._duration_ms > 0:
            ratio = self._slider.value() / 1000.0
            target_ms = int(self._duration_ms * ratio)
            self._player.setPosition(target_ms)

    @staticmethod
    def _format_time(ms: int) -> str:
        sec = max(0, ms // 1000)
        m = sec // 60
        s = sec % 60
        return f"{m:02d}:{s:02d}"

    def stop(self) -> None:
        """停止播放并清理临时文件。"""
        if self._player:
            self._player.stop()
        if self._progress_timer:
            self._progress_timer.stop()
        if self._temp_file:
            try:
                import os
                os.unlink(self._temp_file)
            except Exception:
                pass
            self._temp_file = ""

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)


class ChatWindow(QWidget):
    """独立聊天窗口 — MD3 风格。

    无框半透明窗口，圆角暗色容器，自定义标题栏可拖拽。
    show_chat_messages=True 时显示消息气泡，False 时仅输入栏（收到 bot 消息临时展开）。
    """

    message_sent = Signal(str)
    offset_changed = Signal(QPoint)
    visibility_changed = Signal(bool)
    clear_context_requested = Signal()

    WIN_TITLE = "MoFox 桌宠"
    WIN_WIDTH = 380
    WIN_HEIGHT = 88
    WIN_HEIGHT_FULL = 480

    def __init__(
        self,
        config: DesktopPetConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """初始化聊天窗口。"""
        super().__init__(parent)
        self._config = config
        self._drag_position: QPoint | None = None

        # 主题
        self._theme: ColorTokens = get_theme(config)
        self._font_ui = get_font_family(config, kind="ui")
        self._font_mono = get_font_family(config, kind="mono")
        # 字号缩放（用户可配置，热切换），与屏幕比例缩放因子 s 叠加
        self._font_scale = get_font_size_scale(config)

        self._show_messages: bool = bool(
            getattr(config.chat, "show_chat_messages", False)
        ) if config else False

        self._messages_built: bool = self._show_messages
        self._messages_collapsed: bool = False
        self._size_anim: QPropertyAnimation | None = None

        self.setWindowTitle(self.WIN_TITLE)
        self._scale = self._compute_scale(0.018)
        win_h_base = self.WIN_HEIGHT_FULL if self._show_messages else self.WIN_HEIGHT
        self._win_w = int(self.WIN_WIDTH * self._scale)
        self._win_h = int(win_h_base * self._scale)
        self.setMinimumSize(0, 0)
        self.resize(self._win_w, self._win_h)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._build_ui()

        self._sound_effect = None
        if _HAS_QSOUND and self._config:
            sound_path = getattr(self._config.chat, "notification_sound", "")
            if sound_path and os.path.isfile(sound_path):
                self._sound_effect = QSoundEffect(self)
                self._sound_effect.setSource(QUrl.fromLocalFile(sound_path))
                self._sound_effect.setVolume(0.8)
                self._sound_effect.setLoopCount(1)

    # ---- 屏幕比例尺寸 ----

    BASE_PET_SIDE = 200  # 与 PetWindow 默认边长一致，用作缩放基准

    @classmethod
    def _compute_scale(cls, area_ratio: float) -> float:
        """按主屏可用面积比例计算缩放因子。"""
        from PySide6.QtWidgets import QApplication
        import math
        screen = QApplication.primaryScreen()
        if screen is None:
            return 1.0
        geo = screen.availableGeometry()
        if geo.width() <= 0 or geo.height() <= 0:
            return 1.0
        area = geo.width() * geo.height() * area_ratio
        if area <= 0:
            return 1.0
        side = math.sqrt(area)
        return max(0.5, side / cls.BASE_PET_SIDE)

    # ---- 主题 ----

    def _build_qss(self) -> str:
        """根据当前 theme token 生成 QSS（字号/padding 按缩放因子调整）。"""
        t = self._theme
        s = self._scale
        fs = s * self._font_scale  # 字号专用缩放（屏幕比例 × 用户字号因子）
        ff_ui = self._font_ui
        ff_mono = self._font_mono
        # 渐变/双色强调：新预设有值时启用，旧预设回退纯色
        if t.gradient_from and t.gradient_to:
            title_bg = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.gradient_from}, stop:1 {t.gradient_to})"
            send_btn_bg = f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {t.gradient_from}, stop:1 {t.gradient_to})"
        else:
            title_bg = f"rgba({_hex_to_rgb_tuple(t.surface_container_low)}, 0.8)"
            send_btn_bg = t.primary
        focus_border = t.accent_secondary if t.accent_secondary else t.primary
        scrollbar_hover = t.accent_secondary if t.accent_secondary else t.outline
        # 用 {f} 占位，便于整体替换字体族
        qss = f"""
        * {{
            font-family: {ff_ui};
        }}
        #chat_container {{
            background-color: rgba({_hex_to_rgb_tuple(t.surface)}, 0.96);
            border-radius: {int(16 * s)}px;
            border: 1px solid {t.surface_container_highest};
        }}
        #title_bar {{
            background-color: {title_bg};
            border-top-left-radius: {int(16 * s)}px;
            border-top-right-radius: {int(16 * s)}px;
            border-bottom: 1px solid {t.surface_container_highest};
        }}
        #title_label {{
            color: {t.on_surface};
            font-size: {max(8, int(12 * fs))}px;
            font-weight: 600;
            background: transparent;
        }}
        #close_btn {{
            background: transparent;
            border: none;
            color: {t.outline};
            font-size: {max(9, int(13 * fs))}px;
            border-radius: {int(6 * s)}px;
        }}
        #close_btn:hover {{
            background-color: {t.outline_variant};
            color: {t.error};
        }}
        #clear_btn {{
            background: transparent;
            border: none;
            color: {t.outline};
            font-size: {max(9, int(13 * fs))}px;
            border-radius: {int(6 * s)}px;
        }}
        #clear_btn:hover {{
            background-color: {t.outline_variant};
            color: {t.on_surface};
        }}
        #message_scroll {{
            background: transparent;
            border: none;
        }}
        #scroll_content {{
            background: transparent;
        }}
        /* 气泡内 label 字号统一由 QSS 控制（切换字号时全局刷新） */
        #scroll_content QLabel#bubble_reply {{
            font-size: {max(7, int(9 * fs))}px;
        }}
        #scroll_content QLabel#bubble_sender {{
            font-size: {max(7, int(10 * fs))}px;
            font-weight: 600;
        }}
        #scroll_content QLabel#bubble_system {{
            font-size: {max(8, int(11 * fs))}px;
            font-style: italic;
        }}
        #scroll_content QLabel#bubble_text {{
            font-size: {max(8, int(12 * fs))}px;
            font-family: {ff_mono};
        }}
        #input_bar {{
            background-color: rgba({_hex_to_rgb_tuple(t.surface_container_low)}, 0.8);
            border-bottom-left-radius: {int(16 * s)}px;
            border-bottom-right-radius: {int(16 * s)}px;
            border-top: 1px solid {t.surface_container_highest};
        }}
        #input_field {{
            background-color: {t.surface_container};
            border: 1px solid {t.outline_variant};
            border-radius: {int(12 * s)}px;
            color: {t.on_surface};
            padding: {int(8 * s)}px {int(12 * s)}px;
            font-size: {max(8, int(12 * fs))}px;
            font-family: {ff_mono};
        }}
        #input_field:focus {{
            border: 1px solid {focus_border};
        }}
        #send_btn {{
            background-color: {send_btn_bg};
            color: {t.on_primary};
            border: none;
            border-radius: {int(18 * s)}px;
            font-size: {max(8, int(12 * fs))}px;
            font-weight: 600;
        }}
        #send_btn:hover {{
            background-color: {t.on_primary_container};
        }}
        #send_btn:pressed {{
            background-color: {t.primary_container};
            color: {t.on_primary_container};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 6px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {t.outline_variant};
            border-radius: 3px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {scrollbar_hover};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        """
        return qss

    def apply_theme(self, config: "DesktopPetConfig | None") -> None:
        """运行时切换主题：重新读取 token/字体并重绘 QSS。"""
        self._config = config
        self._theme = get_theme(config)
        self._font_ui = get_font_family(config, kind="ui")
        self._font_mono = get_font_family(config, kind="mono")
        self._font_scale = get_font_size_scale(config)
        # 重应用 QSS
        self.setStyleSheet(self._build_qss())
        # 标题字体
        if hasattr(self, "_title_label") and self._title_label:
            f = QFont()
            f.setFamilies(self._font_ui.split(","))
            f.setPixelSize(max(8, int(12 * self._scale * self._font_scale)))
            f.setBold(True)
            self._title_label.setFont(f)
        if hasattr(self, "_close_btn") and self._close_btn:
            f = QFont()
            f.setFamilies(self._font_ui.split(","))
            f.setPixelSize(max(9, int(13 * self._scale * self._font_scale)))
            self._close_btn.setFont(f)
        if hasattr(self, "_clear_btn") and self._clear_btn:
            f = QFont()
            f.setFamilies(self._font_ui.split(","))
            f.setPixelSize(max(9, int(13 * self._scale * self._font_scale)))
            self._clear_btn.setFont(f)
        self.update()

    # ---- 构建 UI ----

    def _build_ui(self) -> None:
        """构建聊天界面布局。"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        container = QFrame()
        container.setObjectName("chat_container")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # --- 标题栏 ---
        self._title_bar = QFrame()
        self._title_bar.setObjectName("title_bar")
        self._title_bar.setFixedHeight(int(36 * self._scale))
        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(int(12 * self._scale), 0, int(8 * self._scale), 0)
        title_layout.setSpacing(int(8 * self._scale))

        title_text = (
            getattr(self._config.chat, "pet_name", "MoFox 桌宠")
            if self._config else "MoFox 桌宠"
        )
        self._title_label = QLabel(title_text)
        self._title_label.setObjectName("title_label")
        title_font = QFont()
        title_font.setFamilies(self._font_ui.split(","))
        title_font.setPixelSize(max(8, int(12 * self._scale * self._font_scale)))
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        title_layout.addWidget(self._title_label)
        title_layout.addStretch()

        self._clear_btn = QPushButton("清屏")
        self._clear_btn.setObjectName("clear_btn")
        self._clear_btn.setFixedSize(int(28 * self._scale), int(28 * self._scale))
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_font = QFont()
        clear_font.setFamilies(self._font_ui.split(","))
        clear_font.setPixelSize(max(9, int(13 * self._scale * self._font_scale)))
        self._clear_btn.setFont(clear_font)
        self._clear_btn.clicked.connect(self._on_clear)
        title_layout.addWidget(self._clear_btn)

        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("close_btn")
        self._close_btn.setFixedSize(int(28 * self._scale), int(28 * self._scale))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_font = QFont()
        close_font.setFamilies(self._font_ui.split(","))
        close_font.setPixelSize(max(9, int(13 * self._scale * self._font_scale)))
        self._close_btn.setFont(close_font)
        self._close_btn.clicked.connect(self.hide)
        title_layout.addWidget(self._close_btn)

        container_layout.addWidget(self._title_bar)

        self._container_layout = container_layout

        # --- 消息滚动区（仅在开启消息显示时构建）---
        if self._show_messages:
            self._build_message_scroll(container_layout)

        # --- 输入区 ---
        input_bar = QFrame()
        input_bar.setObjectName("input_bar")
        input_bar.setFixedHeight(int(52 * self._scale))
        input_layout = QHBoxLayout(input_bar)
        m2 = int(10 * self._scale)
        input_layout.setContentsMargins(m2, 0, m2, 0)
        input_layout.setSpacing(int(8 * self._scale))

        self._input = QLineEdit()
        self._input.setObjectName("input_field")
        self._input.setPlaceholderText("输入消息...")
        input_font = QFont()
        input_font.setFamilies(self._font_mono.split(","))
        input_font.setPixelSize(max(8, int(12 * self._scale * self._font_scale)))
        self._input.setFont(input_font)
        input_layout.addWidget(self._input, stretch=1)

        self._send_button = QPushButton("发送")
        self._send_button.setObjectName("send_btn")
        self._send_button.setFixedSize(int(64 * self._scale), int(36 * self._scale))
        self._send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        input_layout.addWidget(self._send_button)

        container_layout.addWidget(input_bar)

        main_layout.addWidget(container)

        self._input.returnPressed.connect(self._on_send)
        self._send_button.clicked.connect(self._on_send)

        self.setStyleSheet(self._build_qss())

    def _build_message_scroll(self, container_layout: QVBoxLayout) -> None:
        """构建消息滚动区并添加到 container_layout。"""
        self._message_scroll = QScrollArea()
        self._message_scroll.setObjectName("message_scroll")
        self._message_scroll.setWidgetResizable(True)
        self._message_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._message_scroll.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        scroll_content.setObjectName("scroll_content")
        self._message_layout = QVBoxLayout(scroll_content)
        m = int(10 * self._scale)
        self._message_layout.setContentsMargins(m, m, m, m)
        self._message_layout.setSpacing(int(8 * self._scale))
        self._message_layout.addStretch()

        self._message_scroll.setWidget(scroll_content)
        container_layout.insertWidget(1, self._message_scroll, stretch=1)

    def _ensure_messages_built(self) -> None:
        """确保消息区已构建。若已构建但处于折叠状态则展开。"""
        if self._messages_built:
            if self._messages_collapsed:
                self._expand_messages_area()
            return
        self._messages_built = True
        self._build_message_scroll(self._container_layout)
        self._messages_collapsed = False

    def _expand_messages_area(self) -> None:
        """展开消息区（动画过渡到完整高度）。"""
        if not self._messages_collapsed or not self._messages_built:
            return
        self._messages_collapsed = False
        # 保持消息区显示完整
        if hasattr(self, "_message_scroll") and self._message_scroll:
            self._message_scroll.show()
        target_h = int(self.WIN_HEIGHT_FULL * self._scale)
        self._animate_size(target_h)

    def _collapse_messages_area(self) -> None:
        """折叠消息区（动画收缩到仅输入框）。"""
        if self._messages_collapsed or not self._messages_built:
            return
        self._messages_collapsed = True
        if hasattr(self, "_message_scroll") and self._message_scroll:
            self._message_scroll.hide()
        base_h = int(self.WIN_HEIGHT * self._scale)
        self._animate_size(base_h)

    def _toggle_messages_area(self) -> None:
        """双击标题栏：切换消息区折叠/展开。"""
        if not self._messages_built or not self._show_messages:
            return
        if self._messages_collapsed:
            self._expand_messages_area()
        else:
            self._collapse_messages_area()

    def _animate_size(self, target_h: int) -> None:
        """播放窗口高度动画到目标值。"""
        if self._size_anim and self._size_anim.state() == QPropertyAnimation.State.Running:
            self._size_anim.stop()
        cur_h = self.height()
        self._size_anim = QPropertyAnimation(self, b"size")
        self._size_anim.setDuration(220)
        self._size_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._size_anim.setStartValue(QSize(self._win_w, cur_h))
        self._size_anim.setEndValue(QSize(self._win_w, target_h))
        self._size_anim.start()
        self._win_h = target_h

    def _on_send(self) -> None:
        """处理发送动作：发射消息信号并清空输入框。
        若消息区已构建且处于折叠状态则自动展开。"""
        text = self._input.text()
        if text:
            self.message_sent.emit(text)
            self._input.clear()
            if self._messages_built and self._messages_collapsed and self._show_messages:
                self._expand_messages_area()

    def _on_clear(self) -> None:
        """处理清屏动作：清空消息气泡并发射清上下文信号。"""
        self.clear_messages()
        self.clear_context_requested.emit()

    def clear_messages(self) -> None:
        """清空消息区所有气泡，保留末尾 stretch。"""
        if not self._messages_built or not hasattr(self, "_message_layout"):
            return
        # 逆序遍历移除所有 widget（保留末尾 stretch item）
        while self._message_layout.count() > 1:
            item = self._message_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def set_show_messages(self, show: bool) -> None:
        """运行时切换消息显示模式。

        show=True  → 构建消息区（如未构建）并展开到完整高度
        show=False → 清空气泡并折叠到仅输入栏高度
        """
        self._show_messages = show
        if show:
            if not self._messages_built:
                self._ensure_messages_built()
                # _ensure_messages_built 从零构建时不触发动画，手动展开
                target_h = int(self.WIN_HEIGHT_FULL * self._scale)
                self._animate_size(target_h)
            elif self._messages_collapsed:
                self._expand_messages_area()
        else:
            self.clear_messages()
            if self._messages_built and not self._messages_collapsed:
                self._collapse_messages_area()

    def append_message(
        self,
        role: str,
        text: str,
        reply_to: str = "",
        emoji_bytes: bytes = b"",
        voice_bytes: bytes = b"",
    ) -> None:
        """向消息历史追加一条消息气泡。

        当 show_chat_messages=False 时：
        - 直接返回，不触发消息区构建（避免窗口展开）
        当 show_chat_messages=True 时正常追加气泡。
        """
        if not self._show_messages:
            return
        if not self._messages_built or self._messages_collapsed:
            if role == "system":
                return
            self._ensure_messages_built()

        if role == "user":
            label = (
                getattr(self._config.chat, "user_name", "用户")
                if self._config else "用户"
            )
        elif role == "system":
            label = ""
        else:
            label = (
                getattr(self._config.chat, "pet_name", "桌宠")
                if self._config else "桌宠"
            )

        bubble = self._create_bubble(role, label, text, reply_to=reply_to, emoji_bytes=emoji_bytes, voice_bytes=voice_bytes)

        if role not in ("user", "system") and self._sound_effect:
            self._sound_effect.play()

        if role == "user":
            self._message_layout.insertWidget(
                self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignRight
            )
        elif role == "system":
            self._message_layout.insertWidget(
                self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignCenter
            )
        else:
            self._message_layout.insertWidget(
                self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignLeft
            )

        def _deferred_scroll() -> None:
            self._message_layout.invalidate()
            self._message_layout.activate()
            self._scroll_to_bottom()

        QTimer.singleShot(0, _deferred_scroll)

    def _create_bubble(
        self,
        role: str,
        label: str,
        text: str,
        reply_to: str = "",
        emoji_bytes: bytes = b"",
        voice_bytes: bytes = b"",
    ) -> QFrame:
        """创建单条消息气泡 widget（MD3 风格，主题配色 + 等宽/Ubuntu 字体）。"""
        t = self._theme
        s = self._scale
        bubble = QFrame()
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        # 回复标记（仅 bot 气泡且带 reply_to 时显示）
        if reply_to and role not in ("user", "system"):
            reply_label = QLabel(f"↩ 回复消息 {reply_to[:8]}")
            reply_label.setObjectName("bubble_reply")
            reply_label.setStyleSheet(
                f"color: {t.accent}; background: transparent;"
            )
            layout.addWidget(reply_label)

        if role == "system":
            msg = QLabel(text)
            msg.setObjectName("bubble_system")
            msg.setWordWrap(True)
            msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
            msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            msg.setTextFormat(Qt.TextFormat.PlainText)
            msg.setStyleSheet(
                f"color: {t.bubble_system_fg}; background: transparent;"
            )
            layout.addWidget(msg)
            bubble.setStyleSheet(
                f"background-color: {t.bubble_system_bg};"
                f" border-radius: {int(8 * s)}px;"
            )
            bubble.setFixedWidth(int(340 * s))
        else:
            sender = QLabel(label)
            sender.setObjectName("bubble_sender")
            sender.setStyleSheet(
                "background: transparent;"
            )
            layout.addWidget(sender)

            # 文本（若有）
            if text:
                msg = QLabel(text)
                msg.setObjectName("bubble_text")
                msg.setWordWrap(True)
                msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
                msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                msg.setTextFormat(Qt.TextFormat.PlainText)
                msg.setStyleSheet("background: transparent;")
                layout.addWidget(msg)

            # emoji 图片（若有）
            if emoji_bytes:
                emoji_label = QLabel()
                emoji_label.setStyleSheet("background: transparent;")
                pm = QPixmap()
                pm.loadFromData(emoji_bytes)
                if not pm.isNull():
                    max_side = int(120 * s)
                    scaled = pm.scaled(
                        max_side, max_side,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    emoji_label.setPixmap(scaled)
                    layout.addWidget(emoji_label)

            # 语音控件（仅在非 user 气泡中显示）
            if voice_bytes and role != "user":
                voice_widget = VoiceBubbleWidget(
                    audio_bytes=voice_bytes,
                    theme_tokens=t,
                    scale=s,
                    parent=bubble,
                )
                layout.addWidget(voice_widget)

            if role == "user":
                bubble.setStyleSheet(
                    f"background-color: {t.bubble_user_bg};"
                    f" border-radius: {int(12 * s)}px;"
                )
                sender.setStyleSheet(
                    f"color: {t.primary}; background: transparent;"
                )
                if text:
                    msg.setStyleSheet(
                        f"color: {t.bubble_user_fg}; background: transparent;"
                    )
            else:
                bubble.setStyleSheet(
                    f"background-color: {t.bubble_bot_bg};"
                    f" border-radius: {int(12 * s)}px;"
                )
                sender.setStyleSheet(
                    f"color: {t.on_surface_variant}; background: transparent;"
                )
                if text:
                    msg.setStyleSheet(
                        f"color: {t.bubble_bot_fg}; background: transparent;"
                    )
            bubble.setFixedWidth(int(300 * s))

        return bubble

    def _scroll_to_bottom(self) -> None:
        """滚动消息区到底部。"""
        scrollbar = self._message_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ---- 窗口拖拽 ----

    def mouseDoubleClickEvent(self, event) -> None:
        """双击标题栏切换消息区折叠/展开。"""
        child = self.childAt(event.position().toPoint())
        if child and self._is_in_title_bar(child):
            self._toggle_messages_area()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        """点击标题栏区域时开始拖拽窗口。"""
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child and self._is_in_title_bar(child):
                self._drag_position = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft()
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """拖拽窗口移动。"""
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_position is not None
        ):
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """释放鼠标时清除拖拽状态并 emit 偏移变化。"""
        if self._drag_position is not None:
            self.offset_changed.emit(self.pos())
            self._drag_position = None
        super().mouseReleaseEvent(event)

    def showEvent(self, event) -> None:
        """窗口显示时通知可见性变化。"""
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:
        """窗口隐藏时通知可见性变化。"""
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def load_history(self, messages: list) -> None:
        """批量加载历史消息并渲染。

        show_chat_messages=False 时直接返回，不加载历史（避免展开消息区）。

        Args:
            messages: 历史消息列表，每项为 dict {"role": ..., "text": ...}。
        """
        if not messages:
            return
        if not self._show_messages:
            return
        # 确保消息区已构建并展开
        if not self._messages_built or self._messages_collapsed:
            self._ensure_messages_built()
        # 清空已有消息气泡（保留末尾的 stretch）
        while self._message_layout.count() > 1:
            item = self._message_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for msg in messages:
            role = msg.get("role", "bot")
            text = msg.get("text", "")
            reply_to = msg.get("reply_to", "")
            emoji_bytes = msg.get("emoji_bytes", b"") or b""
            voice_bytes = msg.get("voice_bytes", b"") or b""
            if not text and not emoji_bytes and not voice_bytes:
                continue
            if role == "user":
                label = (
                    getattr(self._config.chat, "user_name", "用户")
                    if self._config else "用户"
                )
            elif role == "system":
                label = ""
            else:
                label = (
                    getattr(self._config.chat, "pet_name", "桌宠")
                    if self._config else "桌宠"
                )
            bubble = self._create_bubble(role, label, text, reply_to=reply_to, emoji_bytes=emoji_bytes, voice_bytes=voice_bytes)
            if role == "user":
                self._message_layout.insertWidget(self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignRight)
            elif role == "system":
                self._message_layout.insertWidget(self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignCenter)
            else:
                self._message_layout.insertWidget(self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignLeft)
        def _deferred_scroll() -> None:
            self._message_layout.invalidate()
            self._message_layout.activate()
            self._scroll_to_bottom()

        QTimer.singleShot(0, _deferred_scroll)

    def _is_in_title_bar(self, widget: QWidget) -> bool:
        """检查 widget 是否属于标题栏。"""
        current = widget
        while current is not None:
            if current is self._title_bar:
                return True
            current = current.parentWidget()
        return False


# 模块级辅助：把 #RRGGBB 转 "r, g, b" 用于 QSS rgba()
def _hex_to_rgb_tuple(hex_color: str) -> str:
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return "0, 0, 0"
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return f"{r}, {g}, {b}"
    except Exception:
        return "0, 0, 0"
