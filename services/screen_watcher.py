"""桌面宠物的定时截图主动监控服务。

按配置间隔在 GUI 线程截图（桌宠中心所在屏），缩放到长边 1080p，
调主程序 VLM 识别，注入 system reminder，并把识别结果作为群聊消息
put 到 in_queue 触发 sub+actor 决策。

累计截图张数达到 max_snapshots_before_purge 时批量删除全部，重新累计。
"""

from __future__ import annotations

import asyncio
import io
import os
import time
from typing import TYPE_CHECKING, Any, cast

from src.core.components.base import BaseService
from src.app.plugin_system.api.log_api import get_logger
from src.core.prompt import (
    SystemReminderBucket,
    SystemReminderConsumeType,
    SystemReminderInsertType,
    get_system_reminder_store,
)

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from ..config import DesktopPetConfig


class ScreenWatcherService(BaseService):
    """定时截图主动监控服务。"""

    service_name = "screen_watcher"
    service_description = "桌面宠物的定时截图 VLM 识别主动搭话服务"
    version = "0.1.0"

    def __init__(self, plugin: BasePlugin) -> None:
        super().__init__(plugin)
        self._config: DesktopPetConfig | None = None
        self._task: asyncio.Task | None = None
        self._snapshot_count: int = 0
        self._log = get_logger("desktop_pet.screen_watcher")

        # 由 plugin 注入：用于请求 GUI 线程截图、用于 put 用户消息到 in_queue
        self._adapter: Any = None

    def bind_adapter(self, adapter: Any) -> None:
        """注入 DesktopPetAdapter 实例，用于截图请求和 in_queue 投递。"""
        self._adapter = adapter

    def _get_config(self) -> DesktopPetConfig | None:
        if self.plugin and hasattr(self.plugin, "config"):
            return cast("DesktopPetConfig", self.plugin.config)
        return None

    async def start(self) -> None:
        self._config = self._get_config()
        if self._config and self._config.screen_watcher.enabled:
            self._task = asyncio.create_task(self._watch_loop())
            self._log.info("Screen watcher started")
        else:
            self._log.info("Screen watcher disabled, skipping")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
            self._log.info("Screen watcher stopped")

    async def _watch_loop(self) -> None:
        cfg = self._config
        if not cfg or not self._adapter:
            return
        interval = max(5, int(cfg.screen_watcher.interval))
        while True:
            try:
                await asyncio.sleep(interval)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                self._log.error("Screen watch tick failed", exc_info=True)
                await asyncio.sleep(interval)

    async def _tick(self) -> None:
        cfg = self._config
        if not cfg or not self._adapter:
            return

        # 1. 请求 GUI 线程截图
        png_bytes = await self._request_screenshot()
        if not png_bytes:
            self._log.warning("Screenshot returned empty, skip tick")
            return

        # 2. 缩放到长边 1080p
        scaled_bytes = self._scale_to_1080p(png_bytes)
        if not scaled_bytes:
            scaled_bytes = png_bytes

        # 3. 保存到 snapshot_dir
        snapshot_dir = cfg.screen_watcher.snapshot_dir
        try:
            os.makedirs(snapshot_dir, exist_ok=True)
            filename = f"snapshot_{int(time.time())}_{self._snapshot_count}.png"
            filepath = os.path.join(snapshot_dir, filename)
            with open(filepath, "wb") as f:
                f.write(scaled_bytes)
        except Exception:
            self._log.error("Failed to save snapshot", exc_info=True)
            filepath = ""

        # 4. 计数 + 达阈值清理
        self._snapshot_count += 1
        threshold = max(1, int(cfg.screen_watcher.max_snapshots_before_purge))
        if self._snapshot_count >= threshold:
            self._purge_all_snapshots(snapshot_dir)
            self._snapshot_count = 0

        # 5. 调 VLM 识别
        vlm_prompt = cfg.screen_watcher.vlm_prompt.strip() or "请描述当前屏幕画面。"
        description = await self._recognize_with_vlm(filepath or scaled_bytes, vlm_prompt)
        if not description:
            self._log.info("VLM returned empty, skip sending message")
            return

        # 6. 注入 system reminder（让 actor 后续能看到截图内容）
        try:
            store = get_system_reminder_store()
            store.set(
                bucket=SystemReminderBucket.ACTOR,
                name="desktop_pet_screen_vision",
                content=f"屏幕画面描述：{description}",
                insert_type=SystemReminderInsertType.DYNAMIC,
                consume=SystemReminderConsumeType.ONCE,
            )
        except Exception:
            self._log.error("Failed to inject screen_vision reminder", exc_info=True)

        # 7. 把识别结果作为群聊消息 put 到 in_queue
        try:
            self._adapter._in_queue.put({
                "text": description,
                "is_proactive": True,
                "source": "screenshot",
            })
            self._log.info(f"Screenshot message enqueued (count={self._snapshot_count})")
        except Exception:
            self._log.error("Failed to enqueue screenshot message", exc_info=True)

    async def _request_screenshot(self) -> bytes:
        """通过 adapter 请求 GUI 线程截图，返回 PNG 字节。"""
        adapter = self._adapter
        loop = asyncio.get_running_loop()
        with adapter._screenshot_lock:
            seq = adapter._screenshot_seq
            adapter._screenshot_seq += 1
            future: asyncio.Future[Any] = loop.create_future()
            adapter._screenshot_futures[seq] = future
        # 通知 GUI 线程截图
        adapter._out_queue.put({"action": "take_screenshot", "seq": seq})
        try:
            result = await asyncio.wait_for(future, timeout=10.0)
            return result if isinstance(result, (bytes, bytearray)) else bytes(result)
        except asyncio.TimeoutError:
            self._log.warning("Screenshot request timed out")
            with adapter._screenshot_lock:
                adapter._screenshot_futures.pop(seq, None)
            return b""

    def _scale_to_1080p(self, png_bytes: bytes) -> bytes:
        """等比缩放到长边 1080p，返回 PNG 字节；失败返回空 bytes。"""
        try:
            from PIL import Image
        except ImportError:
            return b""
        try:
            img = Image.open(io.BytesIO(png_bytes))
            w, h = img.size
            long_side = max(w, h)
            if long_side <= 1080:
                # 已经满足，原样返回
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
            scale = 1080 / long_side
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            self._log.error("Failed to scale screenshot", exc_info=True)
            return b""

    async def _recognize_with_vlm(self, source: Any, prompt: str) -> str:
        """调用主程序 MediaManager 识别图片，返回描述文本。

        Args:
            source: 截图文件路径或 PNG bytes。
            prompt: VLM 提示词（当前 MediaManager 使用内部 prompt template，此参数暂未使用）。
        """
        try:
            from src.core.managers.media_manager import get_media_manager
        except ImportError:
            self._log.warning("media_manager not available")
            return ""
        try:
            mm = get_media_manager()
            # 读取图片数据
            if isinstance(source, str) and os.path.isfile(source):
                with open(source, "rb") as f:
                    raw_data = f.read()
            else:
                raw_data = source if isinstance(source, (bytes, bytearray)) else bytes(source)
            # base64 编码后调用 recognize_media
            from src.core.utils.base64_helper import base64_encode_bytes
            base64_data = base64_encode_bytes(raw_data)
            result = mm.recognize_media(base64_data, media_type="image")
            if hasattr(result, "__await__"):
                result = await result
            return str(result or "").strip()
        except Exception:
            self._log.error("VLM recognize failed", exc_info=True)
            return ""

    def _purge_all_snapshots(self, snapshot_dir: str) -> None:
        """删除 snapshot_dir 下所有 PNG 文件。"""
        try:
            if not os.path.isdir(snapshot_dir):
                return
            for name in os.listdir(snapshot_dir):
                if name.lower().endswith(".png"):
                    try:
                        os.remove(os.path.join(snapshot_dir, name))
                    except Exception:
                        pass
            self._log.info(f"Purged all snapshots in {snapshot_dir}")
        except Exception:
            self._log.error("Failed to purge snapshots", exc_info=True)
