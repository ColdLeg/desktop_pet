# -*- coding: utf-8 -*-
"""GUI 线程侧的录音器：QAudioSource 采集 + RMS 断句 + WAV 输出。

**必须在 GUI 线程运行**（Qt 音频对象有线程亲和性）。
本模块只做采集与断句，识别与投递交给 ``VoiceInputService``（asyncio 线程）。

断句策略：
- 每收到一块 PCM 就计算 RMS；
- RMS ≥ silence_threshold → 记为"有声"，重置静音计时；
- RMS <  silence_threshold → 累积静音时长，达到 ``silence_sec`` 且本段有效时长
  ≥ ``min_speech_sec`` → 切出一句；
- 单句超过 ``max_record_sec`` → 强制切句。
"""

from __future__ import annotations

import array
import math
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
    _HAS_AUDIO = True
except ImportError:  # pragma: no cover
    _HAS_AUDIO = False


class VoiceRecorder(QObject):
    """GUI 线程内的录音 + 断句器。

    信号：
        segment_ready(bytes): 切出一句完整 WAV（16bit PCM）
        error(str): 设备打开失败等错误
    """

    segment_ready = Signal(bytes)
    error = Signal(str)

    # 每次从 QAudioSource 读取的块大小（字节）
    READ_CHUNK = 3200  # 16kHz * 1ch * 2byte * 100ms

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source: Any = None
        self._io_device: Any = None
        self._timer: QTimer | None = None
        self._pcm = bytearray()

        # 参数（start 时设置）
        self._sample_rate = 16000
        self._channels = 1
        self._sample_width = 2
        self._silence_threshold = 500
        self._silence_sec = 1.5
        self._min_speech_sec = 0.5
        self._max_record_sec = 60.0

        # 断句状态
        self._silence_bytes = 0        # 连续静音累积字节
        self._speech_bytes = 0         # 本段有效有声字节
        self._recording = False

    # ── 公共 API ──────────────────────────────────────

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(
        self,
        *,
        device_name: str = "",
        sample_rate: int = 16000,
        channels: int = 1,
        silence_threshold: int = 500,
        silence_sec: float = 1.5,
        min_speech_sec: float = 0.5,
        max_record_sec: float = 60.0,
    ) -> bool:
        """开始录音。返回是否成功。"""
        if not _HAS_AUDIO:
            self.error.emit("PySide6.QtMultimedia 不可用，无法录音")
            return False
        if self._recording:
            return True

        self._sample_rate = int(sample_rate)
        self._channels = int(channels)
        self._silence_threshold = int(silence_threshold)
        self._silence_sec = float(silence_sec)
        self._min_speech_sec = float(min_speech_sec)
        self._max_record_sec = float(max_record_sec)

        fmt = QAudioFormat()
        fmt.setSampleRate(self._sample_rate)
        fmt.setChannelCount(self._channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        device = self._pick_device(device_name)
        if device is None:
            self.error.emit("未找到可用的麦克风设备")
            return False
        if not device.isFormatSupported(fmt):
            # 退回设备首选格式的采样率
            preferred = device.preferredFormat()
            fmt.setSampleRate(preferred.sampleRate())
            fmt.setChannelCount(preferred.channelCount())
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self._sample_rate = preferred.sampleRate()
            self._channels = preferred.channelCount()

        try:
            self._source = QAudioSource(device, fmt, self)
            self._io_device = self._source.start()
        except Exception as exc:
            self.error.emit(f"录音启动失败: {exc}")
            return False

        if self._io_device is None:
            self.error.emit("录音设备打开失败")
            return False

        # 清状态
        self._pcm.clear()
        self._silence_bytes = 0
        self._speech_bytes = 0
        self._recording = True

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(100)
        return True

    def stop(self) -> None:
        """停止录音并清理。"""
        self._recording = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._source is not None:
            try:
                self._source.stop()
            except Exception:
                pass
            self._source = None
        self._io_device = None
        self._pcm.clear()
        self._silence_bytes = 0
        self._speech_bytes = 0

    # ── 内部 ──────────────────────────────────────────

    def _pick_device(self, name: str) -> Any:
        """按名称选择输入设备；名称为空或未匹配则用系统默认。"""
        devices = QMediaDevices.audioInputs()
        if not devices:
            return None
        if name:
            for d in devices:
                if name.lower() in d.description().lower():
                    return d
        default = QMediaDevices.defaultAudioInput()
        return default if default is not None and not default.isNull() else devices[0]

    def _poll(self) -> None:
        """定时读取音频数据并做断句判定。"""
        if not self._recording or self._io_device is None:
            return
        try:
            data = self._io_device.readAll()
        except Exception:
            return
        if not data:
            return

        chunk = bytes(data.data()) if hasattr(data, "data") else bytes(data)
        if not chunk:
            return

        self._pcm.extend(chunk)

        rms = self._rms(chunk)
        bytes_per_sec = self._sample_rate * self._channels * self._sample_width
        silence_bytes_limit = int(self._silence_sec * bytes_per_sec)
        max_bytes = int(self._max_record_sec * bytes_per_sec)
        min_speech_bytes = int(self._min_speech_sec * bytes_per_sec)

        if rms >= self._silence_threshold:
            self._speech_bytes += len(chunk)
            self._silence_bytes = 0
        else:
            self._silence_bytes += len(chunk)

        # 强制切句（超长）
        if len(self._pcm) >= max_bytes:
            self._emit_segment(min_speech_bytes)
            return

        # 静音断句
        if self._silence_bytes >= silence_bytes_limit:
            self._emit_segment(min_speech_bytes)

    def _emit_segment(self, min_speech_bytes: int) -> None:
        """切出一句并发射；太短的段直接丢弃。"""
        pcm = bytes(self._pcm)
        self._pcm.clear()
        self._silence_bytes = 0
        speech = self._speech_bytes
        self._speech_bytes = 0

        if speech < min_speech_bytes or not pcm:
            return

        # 本地封装 WAV：不跨层导入 services（会连带执行 services/__init__
        # 拉起重依赖链），且 wave 是标准库，开销可忽略
        wav = self._pcm_to_wav(pcm, self._sample_rate, self._channels, self._sample_width)
        if wav:
            self.segment_ready.emit(wav)

    @staticmethod
    def _pcm_to_wav(
        pcm: bytes,
        sample_rate: int,
        channels: int,
        sample_width: int = 2,
    ) -> bytes:
        """把原始 PCM 字节封装成 WAV 文件字节。"""
        import io
        import wave

        if not pcm:
            return b""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()

    @staticmethod
    def _rms(pcm: bytes) -> int:
        """计算 16bit 小端 PCM 的 RMS（0-32767）。"""
        if len(pcm) < 2:
            return 0
        samples = array.array("h")
        usable = len(pcm) - (len(pcm) % 2)
        samples.frombytes(pcm[:usable])
        if not samples:
            return 0
        total = 0
        for s in samples:
            total += s * s
        return int(math.sqrt(total / len(samples)))


__all__ = ["VoiceRecorder", "_HAS_AUDIO"]
