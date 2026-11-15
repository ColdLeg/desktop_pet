"""桌面宠物的定时截图主动监控服务。

两种工作模式（``screen_watcher.burst_enabled`` 控制）：

**多帧模式（默认）**
按配置间隔连拍若干张截图 → 用 64×36 HSV 网格打分（算法移植自
``plugins/dsh-see-now/src/cuts.js``）→ 按变化量筛选留帧 → 拼成一张网格图
（每格烧序号与时间偏移）→ 交给 VLM 识别。

画面整体静止（所有帧分数低于 ``still_threshold``）时跳过本次 VLM，省 token。

**单张模式**
旧行为：截一张、缩到 1080p、直接识别。

识别结果注入 system reminder，并作为群聊消息 put 到 in_queue 触发 sub+actor 决策。
累计图片张数达到 max_snapshots_before_purge 时批量删除全部，重新累计。
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
    SystemReminderConsumeType,
    SystemReminderInsertType,
    get_system_reminder_store,
)

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from ..config import DesktopPetConfig


class ScreenWatcherService(BaseService):
    """定时截图主动监控服务。"""

    name = "screen_watcher"
    service_description = "桌面宠物的定时截图 VLM 识别主动搭话服务"
    version = "1.0.0"

    def __init__(self, plugin: BasePlugin) -> None:
        super().__init__(plugin)
        self._config: DesktopPetConfig | None = None
        self._task: asyncio.Task | None = None
        self._snapshot_count: int = 0
        self._log = get_logger("desktop_pet.screen_watcher")

        # 由 plugin 注入：用于请求 GUI 线程截图、用于 put 用户消息到 in_queue
        self._adapter: Any = None

        # 主动休眠状态（由用户通过菜单切换；与按时间表的定时休眠并行，
        # 两者任一生效即视为休眠中，休眠期间不截图）
        self._manual_sleeping: bool = False

        # 跨 tick 的画面基准（上一轮最后一帧的 HSV 网格），用于首帧变化判定
        self._last_hsv: list[float] | None = None

    def bind_adapter(self, adapter: Any) -> None:
        """注入 DesktopPetAdapter 实例，用于截图请求和 in_queue 投递。"""
        self._adapter = adapter

    def set_manual_sleep(self, sleeping: bool) -> None:
        """设置/解除主动休眠。

        与按时间表的定时休眠（sleep 分区）相互独立、可叠加：
        任一处于休眠态即暂停截图；主动休眠不受定时休眠时段结束影响。

        Args:
            sleeping: True 进入主动休眠，False 解除。
        """
        old = self._manual_sleeping
        self._manual_sleeping = sleeping
        if old != sleeping:
            self._log.info(f"Manual sleep {'enabled' if sleeping else 'disabled'}")

    @property
    def manual_sleeping(self) -> bool:
        """当前是否处于主动休眠。"""
        return self._manual_sleeping

    def _is_sleeping_now(self) -> bool:
        """判断当前是否处于休眠态：主动休眠 或 定时休眠时段内。

        Returns:
            bool: True 表示休眠中（应跳过截图）。
        """
        if self._manual_sleeping:
            return True
        cfg = self._config
        if cfg is None:
            return False
        try:
            if not cfg.sleep.enabled:
                return False
            from datetime import datetime
            hour = datetime.now().hour
            wake = int(cfg.sleep.wake_start_hour)
            sleep = int(cfg.sleep.sleep_start_hour)
            # 白天：wake <= hour < sleep；其余为定时休眠时段
            return not (wake <= hour < sleep)
        except Exception:
            return False

    def _get_config(self) -> DesktopPetConfig | None:
        if self.plugin and hasattr(self.plugin, "config"):
            return cast("DesktopPetConfig", self.plugin.config)
        return None

    def _stream_bucket(self) -> str:
        """构造桌宠私聊流的私有 bucket key（stream:{stream_id}:actor）。"""
        from src.core.prompt import STREAM_BUCKET_PREFIX
        from src.core.models.stream import ChatStream
        cfg = self._config
        user_id = (cfg.chat.user_qq_id.strip() or "local_user") if cfg else "local_user"
        stream_id = ChatStream.generate_stream_id(platform="desktop_pet", user_id=user_id)
        return f"{STREAM_BUCKET_PREFIX}{stream_id}:actor"

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
                # 休眠中（主动休眠 或 定时休眠时段）不截图
                if self._is_sleeping_now():
                    self._log.debug("Sleeping (manual or scheduled), skip screenshot tick")
                    continue
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

        sw = cfg.screen_watcher
        burst_enabled = bool(getattr(sw, "burst_enabled", True))

        # 多帧模式依赖 Pillow（缩放/拼图/打分）；缺失时自动退回单张模式
        if burst_enabled:
            try:
                import PIL  # noqa: F401
            except ImportError:
                self._log.warning(
                    "Pillow not available, falling back to single-frame mode"
                )
                burst_enabled = False

        if burst_enabled:
            await self._tick_burst()
        else:
            await self._tick_single()

    async def _tick_single(self) -> None:
        """单张模式（旧行为，``burst_enabled=false`` 时使用）。"""
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
        filepath = self._save_snapshot(scaled_bytes, prefix="snapshot")

        # 4. 调 VLM 识别
        vlm_prompt = cfg.screen_watcher.vlm_prompt.strip() or "请描述当前屏幕画面。"
        description = await self._recognize_with_vlm(filepath or scaled_bytes, vlm_prompt)
        if not description:
            self._log.info("VLM returned empty, skip sending message")
            return

        await self._publish_description(description)

    async def _tick_burst(self) -> None:
        """多帧模式：连拍 → 变化筛选 → 拼图 → VLM。"""
        from PIL import Image

        cfg = self._config
        if not cfg or not self._adapter:
            return

        sw = cfg.screen_watcher
        burst_frames = max(2, int(getattr(sw, "burst_frames", 6)))
        burst_interval = max(0.5, float(getattr(sw, "burst_interval", 2.0)))
        change_threshold = float(getattr(sw, "change_threshold", 12.0))
        still_threshold = float(getattr(sw, "still_threshold", 1.5))
        min_frames = max(1, int(getattr(sw, "min_frames", 2)))
        max_frames = max(1, int(getattr(sw, "max_frames", 6)))
        cell_width = max(160, int(getattr(sw, "sheet_cell_width", 640)))
        skip_still = bool(getattr(sw, "skip_still", True))

        # ── 1. 连拍 ──
        captured: list[tuple[float, bytes]] = []
        consecutive_failures = 0
        for i in range(burst_frames):
            if i > 0:
                await asyncio.sleep(burst_interval)
            # 中途进入休眠 → 放弃本次
            if self._is_sleeping_now():
                self._log.info(f"Entered sleep during burst ({i}/{burst_frames}), abort")
                return
            png = await self._request_screenshot()
            if not png:
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    self._log.warning("Screenshot failed twice in a row, abort burst")
                    break
                continue
            consecutive_failures = 0
            captured.append((time.time(), png))

        if not captured:
            self._log.warning("Burst captured no frames, skip tick")
            return

        # ── 2. 变化筛选 ──
        scored = self._score_frames(captured, change_threshold, still_threshold)

        if skip_still and scored["max_score"] < still_threshold:
            self._log.info(
                f"Screen unchanged (max score={scored['max_score']:.1f} < "
                f"{still_threshold}), skip VLM"
            )
            return

        selected = self._select_frames(scored, min_frames, max_frames)
        if not selected:
            self._log.info("No frames selected after filtering, skip tick")
            return

        # ── 3. 拼图 ──
        frames_for_sheet: list[tuple[str, Any]] = []
        base_ts = selected[0][0]
        for idx, (ts, png, _score) in enumerate(selected, start=1):
            try:
                img = Image.open(io.BytesIO(png))
                img.load()
            except Exception:
                self._log.warning(f"Failed to decode frame #{idx} for sheet", exc_info=True)
                continue
            offset = ts - base_ts
            label = f"{idx}  +{offset:.1f}s" if offset > 0 else f"{idx}  +0s"
            frames_for_sheet.append((label, img))

        if not frames_for_sheet:
            self._log.warning("All frames failed to decode, skip tick")
            return

        from .sheet_builder import build_sheet

        try:
            sheet_png = build_sheet(frames_for_sheet, cell_width=cell_width)
        except Exception:
            self._log.error("Failed to build frame sheet", exc_info=True)
            return
        if not sheet_png:
            self._log.warning("Sheet builder returned empty, skip tick")
            return

        # ── 4. 存盘（存拼图）──
        snapshot_dir = sw.snapshot_dir
        filepath = self._save_snapshot(sheet_png, prefix="sheet")

        # ── 5. VLM 识别 ──
        vlm_prompt = sw.vlm_prompt.strip() or "请描述当前屏幕画面。"
        frame_note = (
            f"\n\n这是一张按时间顺序拼接的画面变化图，共 {len(frames_for_sheet)} 格"
            f"（左上→右下），每格左上角标注了序号与相对首帧的时间偏移。"
            "请结合画面之间的变化过程，描述用户在这段时间里做了什么。"
        )
        description = await self._recognize_with_vlm(
            filepath or sheet_png, vlm_prompt + frame_note
        )
        if not description:
            self._log.info("VLM returned empty, skip sending message")
            return

        self._log.info(
            f"Sheet recognized: kept {len(selected)}/{len(captured)} frames, "
            f"max_score={scored['max_score']:.1f}"
        )
        await self._publish_description(description)

    def _score_frames(
        self,
        captured: list[tuple[float, bytes]],
        change_threshold: float,
        still_threshold: float,
    ) -> dict[str, Any]:
        """对连拍帧逐帧打分（相对上一帧），并更新跨 tick 基准。

        Returns:
            ``{"items": [(ts, png, score), ...], "max_score": float}``
        """
        from PIL import Image

        from .frame_diff import analyze_frame, normalize_score, score_pair

        items: list[tuple[float, bytes, float]] = []
        prev_hsv = self._last_hsv
        max_score = 0.0

        for ts, png in captured:
            try:
                img = Image.open(io.BytesIO(png))
                img.load()
                hsv, mean_v = analyze_frame(img)
            except Exception:
                self._log.warning("Failed to analyze frame, skipping", exc_info=True)
                continue

            if prev_hsv is None:
                # 首帧无前序：作为基准，分数记 0
                raw = 0.0
            else:
                raw = score_pair(prev_hsv, hsv)
            score = normalize_score(raw, mean_v) if raw > 0 else 0.0
            items.append((ts, png, score))
            max_score = max(max_score, score)
            prev_hsv = hsv

        # 跨 tick 基准：用最后一帧的 HSV
        if prev_hsv is not None:
            self._last_hsv = prev_hsv

        return {"items": items, "max_score": max_score}

    def _select_frames(
        self,
        scored: dict[str, Any],
        min_frames: int,
        max_frames: int,
    ) -> list[tuple[float, bytes, float]]:
        """按变化分筛选留帧，保证数量在 [min_frames, max_frames] 内且保持时间顺序。

        规则：
        - 首帧总是保留（作为基准）；
        - 其余帧分数 ≥ 阈值 → 保留；
        - 不足 min_frames → 按分数从高到低补齐（不含已选）；
        - 超过 max_frames → 保留首帧 + 分数最高的 max_frames-1 帧，再按时间排序。
        """
        items: list[tuple[float, bytes, float]] = scored["items"]
        if not items:
            return []

        cfg = self._config
        threshold = float(
            getattr(cfg.screen_watcher, "change_threshold", 12.0) if cfg else 12.0
        )

        # 首帧必留
        first = items[0]
        rest = items[1:]

        kept = [first] + [it for it in rest if it[2] >= threshold]

        # 补齐
        if len(kept) < min_frames and len(items) > len(kept):
            kept_ids = {id(it) for it in kept}
            candidates = sorted(
                (it for it in items if id(it) not in kept_ids),
                key=lambda x: x[2],
                reverse=True,
            )
            for cand in candidates:
                if len(kept) >= min_frames:
                    break
                kept.append(cand)

        # 截断：首帧 + 分数最高的若干帧
        if len(kept) > max_frames:
            head = kept[0]
            others = kept[1:]
            others = sorted(others, key=lambda x: x[2], reverse=True)[: max_frames - 1]
            kept = [head] + others

        # 恢复时间顺序
        kept.sort(key=lambda x: x[0])
        return kept

    def _save_snapshot(self, data: bytes, *, prefix: str) -> str:
        """把图片字节写入 snapshot_dir，返回路径；失败返回空串。

        累计计数达到 ``max_snapshots_before_purge`` 时清空目录并重置计数。
        """
        cfg = self._config
        if not cfg:
            return ""
        snapshot_dir = cfg.screen_watcher.snapshot_dir
        filepath = ""
        try:
            os.makedirs(snapshot_dir, exist_ok=True)
            filename = f"{prefix}_{int(time.time())}_{self._snapshot_count}.png"
            filepath = os.path.join(snapshot_dir, filename)
            with open(filepath, "wb") as f:
                f.write(data)
        except Exception:
            self._log.error("Failed to save snapshot", exc_info=True)
            filepath = ""

        self._snapshot_count += 1
        threshold = max(1, int(cfg.screen_watcher.max_snapshots_before_purge))
        if self._snapshot_count >= threshold:
            self._purge_all_snapshots(snapshot_dir)
            self._snapshot_count = 0
        return filepath

    async def _publish_description(self, description: str) -> None:
        """注入 reminder 并把识别结果投递到 in_queue。"""
        # 注入 system reminder（写入桌宠流私有 bucket）
        try:
            store = get_system_reminder_store()
            store.set(
                bucket=self._stream_bucket(),
                name="desktop_pet_screen_vision",
                content=f"屏幕画面描述：{description}",
                insert_type=SystemReminderInsertType.DYNAMIC,
                consume=SystemReminderConsumeType.ONCE,
            )
        except Exception:
            self._log.error("Failed to inject screen_vision reminder", exc_info=True)

        # 把识别结果作为群聊消息 put 到 in_queue
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

        请求失败（异常或返回空）时按配置指数退避重试，重试次数与间隔
        来自 ``screen_watcher.vlm_*`` 配置项，每次失败都会记录日志。

        Args:
            source: 截图文件路径或 PNG bytes。
            prompt: VLM 提示词（当前 MediaManager 使用内部 prompt template，此参数暂未使用）。

        Returns:
            识别出的描述文本；全部尝试失败时返回空字符串。
        """
        try:
            from src.core.managers.media_manager import get_media_manager
        except ImportError:
            self._log.warning("media_manager not available")
            return ""

        # 读取图片数据（只需读一次，重试复用）
        try:
            if isinstance(source, str) and os.path.isfile(source):
                with open(source, "rb") as f:
                    raw_data = f.read()
            else:
                raw_data = source if isinstance(source, (bytes, bytearray)) else bytes(source)
            from src.core.utils.base64_helper import base64_encode_bytes
            base64_data = base64_encode_bytes(raw_data)
        except Exception:
            self._log.error("Failed to read screenshot data for VLM", exc_info=True)
            return ""

        cfg = self._config
        max_retries = max(0, int(getattr(cfg.screen_watcher, "vlm_max_retries", 3))) if cfg else 3
        base_delay = max(0.0, float(getattr(cfg.screen_watcher, "vlm_retry_delay", 3.0))) if cfg else 3.0
        backoff = max(1.0, float(getattr(cfg.screen_watcher, "vlm_retry_backoff", 2.0))) if cfg else 2.0

        try:
            mm = get_media_manager()
        except Exception:
            self._log.error("Failed to get media_manager", exc_info=True)
            return ""

        total_attempts = max_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                result = mm.recognize_media(base64_data, media_type="image")
                if hasattr(result, "__await__"):
                    result = await result
                text = str(result or "").strip()
                if text:
                    if attempt > 1:
                        self._log.info(
                            f"VLM recognize succeeded on attempt {attempt}/{total_attempts} "
                            f"({len(text)} chars)"
                        )
                    return text
                # 空结果视作失败，进入重试
                raise ValueError("VLM returned empty result")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt >= total_attempts:
                    self._log.error(
                        f"VLM recognize failed after {total_attempts} attempt(s): "
                        f"{type(exc).__name__}: {exc}",
                        exc_info=True,
                    )
                    return ""
                delay = base_delay * (backoff ** (attempt - 1))
                self._log.warning(
                    f"VLM recognize attempt {attempt}/{total_attempts} failed "
                    f"({type(exc).__name__}: {exc}), retrying in {delay:.1f}s"
                )
                if delay > 0:
                    await asyncio.sleep(delay)

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
