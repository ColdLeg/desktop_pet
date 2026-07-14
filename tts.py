"""桌宠插件 TTS HTTP 客户端。

提供轻量异步函数合成语音，复用 tts_http_server 端点。
"""

from __future__ import annotations

from typing import Any

TTS_PROTOCOL_VERSION = "mfx-tts-http-v1"


async def synthesize_tts(
    *,
    text: str,
    config: Any,
    logger: Any = None,
) -> bytes | None:
    """调用 TTS HTTP 服务合成语音。

    Args:
        text: 待合成文本。
        config: TTSSection 配置对象，提供 endpoint/timeout/mime_type/provider。
        logger: 可选日志器。

    Returns:
        音频字节（WAV 或其他 mime_type 格式）；合成失败或无音频数据返回 None。
    """
    import base64

    import httpx

    endpoint = str(
        getattr(config, "endpoint", None)
        or "http://127.0.0.1:8000/router/tts_http_server/api/tts/v1/synthesize"
    )
    timeout = float(getattr(config, "timeout", 30.0) or 30.0)
    mime_type = str(getattr(config, "mime_type", "audio/wav") or "audio/wav")
    provider = str(getattr(config, "provider", "") or "")

    payload: dict[str, Any] = {
        "protocol": TTS_PROTOCOL_VERSION,
        "stream_id": "desktop_pet",
        "text": text,
        "emotion": None,
        "markers": {},
        "options": {
            "mime_type": mime_type,
        },
    }
    if provider:
        payload["options"]["provider"] = provider

    if logger:
        logger.debug(f"TTS request: endpoint={endpoint}, text={text[:50]!r}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            body = response.text.strip()
            message = str(error)
            if body:
                message = f"{message}; response={body}"
            raise RuntimeError(message) from error
        data = response.json()

    audio_base64 = data.get("audio_base64")
    if isinstance(audio_base64, str) and audio_base64:
        audio = base64.b64decode(audio_base64)
        if logger:
            logger.debug(f"TTS response: {len(audio)} bytes, mime={data.get('mime_type')}")
        return audio

    if logger:
        logger.debug("TTS response: no audio data")
    return None


__all__ = [
    "TTS_PROTOCOL_VERSION",
    "synthesize_tts",
]