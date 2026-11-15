# -*- coding: utf-8 -*-
"""桌宠语音输入服务：录音 → ASR → 以用户名义发送。

链路：

    QAudioSource 采集 PCM（GUI 线程）
      → RMS 静音检测断句
      → WAV 封装 → ASREngine.recognize()（asyncio 线程）
      → 文本缓冲，等待 silence_timeout 秒无新内容
      → 以用户名义 put 到 adapter._in_queue

线程模型（关键）：
- ``QAudioSource`` 与 ``QIODevice`` 必须在**有事件循环的 GUI 线程**创建与持有；
- 本服务运行在 asyncio 线程，通过 ``_adapter._out_queue`` 向 GUI 线程下发
  ``voice_record_start`` / ``voice_record_stop`` 指令；
- GUI 线程采集到的 WAV 字节通过 ``_adapter._in_queue`` 回传（action=voice_audio），
  由 ``_poll_in_queue`` 转交本服务。

与现有 TTS 播放的关系：TTS 播放期间录音会录到自己的声音（自激），
因此 ``_tts_playing`` 为真时直接丢弃该段。
"""

from __future__ import annotations

import asyncio
import base64
import io
import queue
import time
import wave
from typing import TYPE_CHECKING, Any, cast

from src.app.plugin_system.api.log_api import get_logger

if TYPE_CHECKING:
    from ..config import DesktopPetConfig
    from ..plugin import DesktopPetAdapter


class VoiceInputService:
    """语音输入编排：录音控制、断句、ASR、投递。

    生命周期由 ``DesktopPetAdapter`` 管理（``_start_voice_input`` / ``_stop_voice_input``）。
    """

    def __init__(self, adapter: "DesktopPetAdapter") -> None:
        self._adapter = adapter
        self._log = get_logger("desktop_pet.voice_input")

        self._running: bool = False
        self._task: asyncio.Task | None = None

        # 待投递的文本缓冲（多句合并）
        self._pending_texts: list[str] = []
        self._pending_deadline: float = 0.0
        self._pending_lock = asyncio.Lock()

        # 收到的 WAV 片段队列（GUI 线程 → asyncio 线程）
        self._audio_queue: queue.Queue[bytes] = queue.Queue()

    # ── 配置 ──────────────────────────────────────────

    @property
    def _config(self) -> "DesktopPetConfig | None":
        return self._adapter._config

    def _cfg(self) -> Any:
        cfg = self._config
        return getattr(cfg, "voice_input", None) if cfg else None

    @property
    def running(self) -> bool:
        """当前是否处于录音状态。"""
        return self._running

    # ── 生命周期 ──────────────────────────────────────

    async def start(self) -> None:
        """开始录音。"""
        if self._running:
            return
        cfg = self._cfg()
        if cfg is None:
            self._log.warning("voice_input 配置缺失，跳过启动")
            return

        # 通知 GUI 线程创建 QAudioSource 并开始采集
        self._adapter._out_queue.put({
            "action": "voice_record_start",
            "device_name": getattr(cfg, "device_name", "") or "",
            "sample_rate": int(getattr(cfg, "sample_rate", 16000)),
            "channels": int(getattr(cfg, "channels", 1)),
            "silence_threshold": int(getattr(cfg, "silence_threshold", 500)),
            "silence_sec": float(getattr(cfg, "silence_sec", 1.5)),
            "min_speech_sec": float(getattr(cfg, "min_speech_sec", 0.5)),
            "max_record_sec": float(getattr(cfg, "max_record_sec", 60.0)),
        })
        self._running = True
        self._task = asyncio.ensure_future(self._consume_loop())
        self._log.info("Voice input started")

    async def stop(self) -> None:
        """停止录音并清空待投递内容。"""
        if not self._running:
            return
        self._running = False
        self._adapter._out_queue.put({"action": "voice_record_stop"})
        if self._task is not None:
            self._task.cancel()
            self._task = None
        # 清空缓冲
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        async with self._pending_lock:
            self._pending_texts.clear()
            self._pending_deadline = 0.0
        self._log.info("Voice input stopped")

    # ── 音频接收（由 _poll_in_queue 调用） ────────────

    def on_audio_segment(self, wav_bytes: bytes) -> None:
        """GUI 线程送来的一个语音片段（WAV 字节）。"""
        if not self._running or not wav_bytes:
            return
        # 自激保护：TTS 正在播报时丢弃
        if getattr(self._adapter, "_tts_playing", False):
            self._log.debug("Skip audio segment: TTS is playing")
            return
        self._audio_queue.put(wav_bytes)

    # ── 主循环 ────────────────────────────────────────

    async def _consume_loop(self) -> None:
        """消费音频片段队列 + 处理投递超时。"""
        try:
            while self._running:
                # 1. 处理待投递超时
                await self._maybe_flush()

                # 2. 取音频片段（非阻塞）
                try:
                    wav_bytes = self._audio_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.2)
                    continue

                await self._transcribe_and_buffer(wav_bytes)
        except asyncio.CancelledError:
            pass
        except Exception:
            self._log.error("Voice input consume loop crashed", exc_info=True)

    async def _transcribe_and_buffer(self, wav_bytes: bytes) -> None:
        """调用 ASR 识别，并把结果加入待投递缓冲。"""
        text = await self._transcribe(wav_bytes)
        if not text:
            return

        cfg = self._cfg()
        timeout = float(getattr(cfg, "silence_timeout", 3.0)) if cfg else 3.0
        async with self._pending_lock:
            self._pending_texts.append(text)
            self._pending_deadline = time.time() + timeout
        self._log.info(
            f"ASR result buffered: {text[:50]!r} "
            f"(total {len(self._pending_texts)} segment(s), flush in {timeout:.1f}s)"
        )

    async def _maybe_flush(self) -> None:
        """到点则把缓冲的文本作为用户消息投递。"""
        async with self._pending_lock:
            if not self._pending_texts:
                return
            if time.time() < self._pending_deadline:
                return
            text = " ".join(self._pending_texts).strip()
            self._pending_texts.clear()
            self._pending_deadline = 0.0

        if not text:
            return

        cfg = self._cfg()
        send_as_user = bool(getattr(cfg, "send_as_user", True)) if cfg else True
        msg: dict[str, Any] = {
            "text": text,
            "source": "voice_input",
        }
        if not send_as_user:
            # 作为系统提醒身份投递（仍进同一私聊流，但标注身份）
            msg["is_proactive"] = True
            msg["nickname"] = "语音输入"

        try:
            self._adapter._in_queue.put(msg)
            self._log.info(f"Voice message dispatched as user: {text[:60]!r}")
        except Exception:
            self._log.error("Failed to enqueue voice message", exc_info=True)

    def cancel_pending(self, reason: str = "") -> None:
        """取消待投递缓冲（用户手动打字时调用）。"""
        if not self._pending_texts:
            return
        self._pending_texts.clear()
        self._pending_deadline = 0.0
        self._log.info(f"Voice buffer cancelled{(' by ' + reason) if reason else ''}")

    # ── ASR ───────────────────────────────────────────

    async def _transcribe(self, wav_bytes: bytes) -> str:
        """调用框架 ASR 引擎把 WAV 转成文本。

        Returns:
            识别文本；失败返回空串。
        """
        cfg = self._cfg()
        max_retries = max(0, int(getattr(cfg, "asr_max_retries", 2))) if cfg else 2

        try:
            from src.core.managers.media_manager import get_media_manager
            from src.core.utils.base64_helper import base64_encode_bytes
        except ImportError:
            self._log.warning("media_manager not available, ASR disabled")
            return ""

        b64 = base64_encode_bytes(wav_bytes)
        total_attempts = max_retries + 1

        for attempt in range(1, total_attempts + 1):
            try:
                mm = get_media_manager()
                result = mm.recognize_media(b64, media_type="voice")
                if hasattr(result, "__await__"):
                    result = await result
                text = str(result or "").strip()
                if text:
                    if attempt > 1:
                        self._log.info(
                            f"ASR succeeded on attempt {attempt}/{total_attempts}"
                        )
                    return text
                raise ValueError("ASR returned empty result")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt >= total_attempts:
                    self._log.error(
                        f"ASR failed after {total_attempts} attempt(s): "
                        f"{type(exc).__name__}: {exc}",
                        exc_info=True,
                    )
                    return ""
                delay = 1.5 * attempt
                self._log.warning(
                    f"ASR attempt {attempt}/{total_attempts} failed "
                    f"({type(exc).__name__}: {exc}), retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

        return ""


# ── WAV 工具（供 GUI 线程侧调用） ─────────────────────


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int, sample_width: int = 2) -> bytes:
    """把原始 PCM 字节封装成 WAV 文件字节。

    Args:
        pcm: 原始 PCM 数据（小端有符号）。
        sample_rate: 采样率。
        channels: 声道数。
        sample_width: 每样本字节数（16bit = 2）。

    Returns:
        WAV 字节。
    """
    if not pcm:
        return b""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


__all__ = ["VoiceInputService", "pcm_to_wav"]
