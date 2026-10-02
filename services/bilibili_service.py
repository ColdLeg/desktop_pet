# -*- coding: utf-8 -*-
"""桌面宠物内置 B 站直播弹幕服务。

管理 B 站开放平台 WebSocket 长连接生命周期：
- 创建 HTTP API 客户端 + WebSocket 客户端 + 消息分发器
- 收到弹幕/礼物/SC/上舰 → 通过 core_sink 发送到核心群聊流
- 同时推送到 chat_window 显示
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any, cast

from src.app.plugin_system.api.log_api import get_logger
from src.kernel.concurrency import get_task_manager

from ..bilibili.api import BilibiliApi, BilibiliApiError, StartResponse
from ..bilibili.client import BilibiliClient, BilibiliClientError
from ..bilibili.dispatcher import BilibiliDispatcher

if TYPE_CHECKING:
    from ..plugin import DesktopPetAdapter

logger = get_logger("desktop_pet.bilibili")


class BilibiliService:
    """管理 B 站直播弹幕 WebSocket 长连接。

    生命周期：
    - start(): 创建 API 客户端 → clean stale game_id → 启动会话循环
    - stop(): 停止会话 → 调用 end_app → 清理资源
    - 消息回调: 收到弹幕 → dispatcher → envelope → core_sink → chat window
    """

    def __init__(self, adapter: "DesktopPetAdapter") -> None:
        self._adapter = adapter
        self._api: BilibiliApi | None = None
        self._client: BilibiliClient | None = None
        self._dispatcher: BilibiliDispatcher | None = None
        self._start_resp: StartResponse | None = None
        self._session_task: asyncio.Task | None = None
        self._stopping: bool = False

    # ── 配置读取 ──────────────────────────────────────

    @property
    def _config(self):
        if self._adapter._config is None:
            return None
        return self._adapter._config

    def _get_bilibili_cfg(self):
        """获取 B 站配置节；不存在则返回 None。"""
        cfg = self._config
        if cfg is None:
            return None
        try:
            return cfg.bilibili
        except Exception:
            return None

    def _is_enabled(self) -> bool:
        """读取配置开关。"""
        bili = self._get_bilibili_cfg()
        if bili is None:
            return False
        return bool(bili.enabled)

    # ── 生命周期 ──────────────────────────────────────

    async def start(self) -> None:
        """启动 B 站服务：构造 API/dispatcher，启动会话循环。"""
        bili = self._get_bilibili_cfg()
        if bili is None:
            logger.warning("B 站配置缺失，跳过启动")
            return
        if not bili.access_key_id or not bili.access_key_secret or not bili.id_code or bili.app_id <= 0:
            logger.warning("B 站凭证未完整填写，跳过启动")
            return

        self._api = BilibiliApi(
            host=bili.host,
            access_key_id=bili.access_key_id,
            access_key_secret=bili.access_key_secret,
            app_id=bili.app_id,
            id_code=bili.id_code,
            timeout=15.0,
        )
        self._dispatcher = BilibiliDispatcher(
            stream_name_override=bili.stream_name,
        )

        # 清理上次遗留的 game_id
        await self._cleanup_stale_game_id()

        self._stopping = False
        self._session_task = asyncio.ensure_future(self._session_loop())
        logger.info("B 站弹幕服务已启动")

    async def stop(self) -> None:
        """停止 B 站服务：关闭会话、释放资源。"""
        self._stopping = True
        await self._stop_session(end_app=True)

        if self._api is not None:
            try:
                await self._api.aclose()
            except Exception as exc:
                logger.debug(f"关闭 HTTP 客户端异常: {exc}")
            self._api = None

        self._dispatcher = None
        logger.info("B 站弹幕服务已停止")

    # ── 会话循环 ──────────────────────────────────────

    async def _session_loop(self) -> None:
        """长跑任务：维持一次 B 站会话，断开后自动重连。"""
        while not self._stopping:
            try:
                await self._run_one_session()
                if self._stopping:
                    break
            except asyncio.CancelledError:
                break
            except (BilibiliApiError, BilibiliClientError) as exc:
                logger.warning(f"B 站会话异常: {exc}")
            except Exception as exc:
                logger.error(f"B 站会话未预期异常: {exc}", exc_info=True)

            if self._stopping:
                break

            # 退避重连
            await asyncio.sleep(5.0)

    async def _run_one_session(self) -> None:
        """完整跑一次会话：start_app → client.start → 等断。"""
        if self._api is None or self._dispatcher is None:
            return

        bili = self._get_bilibili_cfg()
        if bili is None:
            return

        # 1) 调 /v2/app/start
        logger.info("调用 /v2/app/start 启动 B 站应用")
        start_resp = await self._api.start_app()
        self._start_resp = start_resp
        self._persist_game_id(start_resp.game_id)
        self._dispatcher.update_room_context(
            room_id=start_resp.anchor_room_id,
            anchor_uname=start_resp.anchor_uname,
        )
        logger.info(
            f"start_app 成功 game_id={start_resp.game_id} "
            f"主播={start_resp.anchor_uname} room={start_resp.anchor_room_id}"
        )

        # 2) 建 client
        self._client = BilibiliClient(
            api=self._api,
            on_event=self._on_bilibili_event,
            heartbeat_ws_interval=20.0,
            heartbeat_app_interval=20.0,
        )

        try:
            await self._client.start(start_resp)
            await self._client.wait_closed()
        finally:
            try:
                await self._client.stop()
            except Exception as exc:
                logger.debug(f"停止 client 异常: {exc}")
            await self._safe_end_app(start_resp.game_id)
            self._client = None
            self._start_resp = None

    async def _stop_session(self, *, end_app: bool) -> None:
        """关闭当前会话。"""
        if self._client is not None:
            try:
                await self._client.stop()
            except Exception as exc:
                logger.debug(f"关闭 client 异常: {exc}")

        if self._session_task is not None:
            self._session_task.cancel()
            self._session_task = None

        if end_app and self._start_resp is not None and self._api is not None:
            await self._safe_end_app(self._start_resp.game_id)

        self._client = None
        self._start_resp = None

    async def _safe_end_app(self, game_id: str) -> None:
        """调 /v2/app/end；失败只记日志。"""
        if not game_id or self._api is None:
            return
        try:
            await self._api.end_app(game_id)
            logger.info(f"已结束 game_id={game_id}")
        except Exception as exc:
            logger.warning(f"end_app 失败 game_id={game_id}: {exc}")
        finally:
            self._clear_persisted_game_id()

    # ── 消息回调 ──────────────────────────────────────

    async def _on_bilibili_event(self, payload: dict[str, Any]) -> None:
        """收到 op=5 业务包：dispatch → 发送到核心 → 推送 chat window。"""
        if self._dispatcher is None:
            return

        try:
            envelope = await self._dispatcher.dispatch(payload)
        except Exception:
            logger.exception("B 站消息 dispatch 失败")
            return

        if envelope is None:
            return

        # 发送到核心群聊流
        try:
            if self._adapter.core_sink is not None:
                await self._adapter.core_sink.send(envelope)
        except Exception:
            logger.exception("发送 B 站 envelope 到核心失败")

        # 提取文本推送到 chat window
        text = self._extract_text(envelope)
        if text:
            self._adapter._out_queue.put({
                "action": "append_chat",
                "role": "system",
                "text": f"[B站] {text}",
            })

    @staticmethod
    def _extract_text(envelope: dict) -> str:
        """从 envelope 的 message_segment 提取显示文本。"""
        seg = envelope.get("message_segment")
        if isinstance(seg, dict) and seg.get("type") == "text":
            return str(seg.get("data") or "")
        if isinstance(seg, list):
            parts = []
            for item in seg:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("data") or ""))
            return " ".join(parts)
        return ""

    # ── game_id 持久化 ────────────────────────────────

    @staticmethod
    def _game_id_path() -> str:
        base_dir = os.path.join(os.getcwd(), "data", "desktop_pet", "bilibili")
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, "last_game_id.txt")

    def _persist_game_id(self, game_id: str) -> None:
        if not game_id:
            return
        try:
            with open(self._game_id_path(), "w", encoding="utf-8") as f:
                f.write(game_id)
        except OSError as exc:
            logger.debug(f"持久化 game_id 失败（忽略）: {exc}")

    def _clear_persisted_game_id(self) -> None:
        path = self._game_id_path()
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:
                logger.debug(f"清空持久化 game_id 失败（忽略）: {exc}")

    async def _cleanup_stale_game_id(self) -> None:
        """启动时释放上次遗留的 game_id。"""
        path = self._game_id_path()
        try:
            with open(path, encoding="utf-8") as f:
                stale = f.read().strip()
        except FileNotFoundError:
            return
        except OSError:
            return

        if not stale:
            self._clear_persisted_game_id()
            return

        logger.info(f"检测到上次遗留 game_id={stale}，尝试释放")
        try:
            if self._api is not None:
                await self._api.end_app(stale)
                logger.info(f"已释放遗留 game_id={stale}")
        except Exception as exc:
            logger.info(f"释放遗留 game_id 失败（可能已过期）: {exc}")
        finally:
            self._clear_persisted_game_id()