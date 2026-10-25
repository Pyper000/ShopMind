# -*- coding: utf-8 -*-
"""FastAPI 服务入口

路由：
    POST /commerce/intents                 提交买家意图（同步返回最终回复；启用队列时内部入队后等结果）
    POST /commerce/intents/async           提交买家意图（立即返回 task_id，结果走 WS 或轮询）
    GET  /commerce/tasks/{task_id}         查任务状态（queued / running / done / failed）
    WS   /commerce/events                  订阅会话事件流
    GET  /commerce/orders/{order_id}       查询订单（直连 UseCase，不过 Agent）
    POST /commerce/orders/{order_id}/cancel  取消订单（直连 UseCase）
    GET  /health                           健康检查（含依赖连通性与队列深度）

启动：
    uv run uvicorn app.presentation.server:app --port 8000
    uv run python -m app.worker          # 启用队列时另起消费进程

同步接口为什么保留：13 case 评测脚本与前端都依赖它直接返回 final_text，
改成纯异步会一次性搞挂回归与前端。削峰由 worker 并发度保证，与接口形态无关。
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.domain.buyer.preference import BuyerPreference
from app.application.agents.orchestrator import SubmitIntentInput
from app.composition import Container, build_container
from app.domain.queue.ports.task_queue import IntentTask, TaskStatus
from app.presentation.connection import ConnectionManager
from app.presentation.dto import (
    CancelOrderRequest,
    SubmitIntentRequest,
    SubmitIntentResponse,
)
from app.presentation.mvp_dto import (
    AccountRequest,
    InventoryRequest,
    MemoryConsentRequest,
    PreferenceRequest,
    QuickFeedbackRequest,
    TemplateCandidateRequest,
    TemplateRollbackRequest,
    TemplateReviewRequest,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

logger = logging.getLogger(__name__)

# 幂等键有效期：同一会话同一句话在此窗口内重复提交视为重复请求
_IDEMPOTENCY_TTL_SECONDS = 600
# 轮数计数器存活时长：比幂等窗口长得多，让一整段会话都能被正确分类
_TURN_COUNTER_TTL_SECONDS = 86400


def build_app() -> FastAPI:
    state: dict = {}
    active_sessions: set[str] = set()
    request_times: dict[str, list[float]] = {}

    async def _forward_remote_events(c: Container) -> None:
        """把其他进程（worker）广播的事件转发给本进程的 WS 订阅者。"""
        if c.backplane is None:
            return
        try:
            async for event in c.backplane.listen():
                c.bus.deliver_local(event)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            logger.warning("事件背板监听中断：%s", err)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        c = await build_container()
        state["c"] = c
        state["connections"] = ConnectionManager(c.bus)
        await c.startup()
        if c.backplane is not None:
            # 跨进程事件转发：不开这个任务，worker 产生的流式事件到不了前端
            state["forwarder"] = asyncio.create_task(_forward_remote_events(c))
        try:
            yield
        finally:
            forwarder = state.pop("forwarder", None)
            if forwarder is not None:
                forwarder.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await forwarder
            state.pop("c", None)
            await c.shutdown()

    api = FastAPI(title="ShopMind 购物方案 Agent", version="0.4.0", lifespan=lifespan)

    def container() -> Container:
        if "c" not in state:
            raise HTTPException(status_code=503, detail="服务尚未就绪")
        return state["c"]

    def _mvp_store(c: Container):
        if c.mvp_store is None:
            raise HTTPException(status_code=503, detail="本地 MVP 持久化未配置")
        return c.mvp_store

    def _authenticated_user(c: Container, request: Request, required: bool = True) -> str | None:
        authorization = request.headers.get("authorization", "")
        if not authorization:
            if required:
                raise HTTPException(status_code=401, detail="此操作需要登录")
            return None
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="登录凭证无效")
        user_id = _mvp_store(c).authenticate(token)
        if user_id is None:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        return user_id

    def _intent_identity(c: Container, request: Request, session_id: str) -> tuple[str, bool]:
        user_id = _authenticated_user(c, request, required=False)
        if user_id is None:
            return f"guest-{session_id}", False
        return user_id, _mvp_store(c).has_memory_consent(user_id)

    def _local_review_request(request: Request) -> None:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(status_code=403, detail="模板审核仅允许本机访问")

    settings_origins = build_container_origins()
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.get("/health")
    async def health() -> dict:
        """依赖连通性一并报出，避免"进程活着但存储已挂"被当成健康。"""
        c = container()
        database = "disabled"
        if c.db_engine is not None:
            try:
                async with c.db_engine.connect() as conn:
                    await conn.execute(text("select 1"))
                database = c.db_engine.url.get_backend_name()
            except Exception as err:  # noqa: BLE001
                database = f"error: {err}"
        redis_state = "disabled"
        if c.cache.enabled:
            redis_state = "ok" if await c.cache.ping() else "error"
        return {
            "status": "ok",
            "model": c.settings.llm_model,
            "database": database,
            "redis": redis_state,
            "semantic_cache": c.semantic_cache.enabled,
            "queue": "enabled" if c.task_queue is not None else "disabled",
            "queue_depth": await c.task_queue.depth() if c.task_queue is not None else 0,
        }

    @api.post("/commerce/intents", response_model=SubmitIntentResponse)
    async def submit_intent(body: SubmitIntentRequest, request: Request) -> SubmitIntentResponse:
        c = container()
        session_id = body.shopping_session_id or f"session-{uuid.uuid4().hex[:8]}"
        buyer_id, memory_consent = _intent_identity(c, request, session_id)
        intent = SubmitIntentInput(
            shopping_session_id=session_id,
            buyer_id=buyer_id,
            locale=body.locale,
            currency=body.currency,
            raw_query=body.raw_query,
            checklist_selection=body.checklist_selection.model_dump() if body.checklist_selection else None,
            memory_consent=memory_consent,
        )
        if c.task_queue is None:
            now = time.monotonic()
            for address in list(request_times):
                request_times[address] = [t for t in request_times[address] if now - t < 3600]
                if not request_times[address]:
                    del request_times[address]
            address = request.client.host if request.client else "unknown"
            times = request_times.setdefault(address, [])
            if len(times) >= 30 or len(active_sessions) >= 2:
                raise HTTPException(status_code=429, detail="演示服务繁忙或达到每小时调用上限，请稍后重试")
            if session_id in active_sessions:
                raise HTTPException(status_code=409, detail="当前会话仍在处理中")
            times.append(now)
            active_sessions.add(session_id)
            trace = c.bus.subscribe(session_id)
            try:
                result = await asyncio.wait_for(c.orchestrator.handle_intent(intent), timeout=240)
                events = []
                while not trace.empty():
                    event = trace.get_nowait()
                    if event.type in {"plan.preview", "plan.update", "model.call", "tool.invoke", "tool.result", "agent.dispatch", "agent.result", "error"}:
                        events.append({"type": event.type, "payload": event.payload, "occurred_at": event.occurred_at})
                return SubmitIntentResponse(
                    shopping_session_id=result.shopping_session_id, final_text=result.final_text, events=events,
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="处理超时，请稍后重试")
            finally:
                c.bus.unsubscribe(session_id, trace)
                active_sessions.discard(session_id)

        task_id = await _enqueue(c, intent)
        final_text = await _await_result(c, task_id, session_id)
        return SubmitIntentResponse(shopping_session_id=session_id, final_text=final_text)

    @api.post("/commerce/intents/stream")
    async def submit_intent_stream(body: SubmitIntentRequest, request: Request) -> StreamingResponse:
        """Stream ShopMind events and token deltas as Server-Sent Events."""
        c = container()
        if c.task_queue is not None:
            raise HTTPException(status_code=503, detail="实时流暂不支持队列模式")
        session_id = body.shopping_session_id or f"session-{uuid.uuid4().hex[:8]}"
        buyer_id, memory_consent = _intent_identity(c, request, session_id)
        now = time.monotonic()
        for address in list(request_times):
            request_times[address] = [t for t in request_times[address] if now - t < 3600]
            if not request_times[address]:
                del request_times[address]
        address = request.client.host if request.client else "unknown"
        times = request_times.setdefault(address, [])
        if len(times) >= 30 or len(active_sessions) >= 2:
            raise HTTPException(status_code=429, detail="演示服务繁忙或达到每小时调用上限，请稍后重试")
        if session_id in active_sessions:
            raise HTTPException(status_code=409, detail="当前会话仍在处理中")
        times.append(now)
        active_sessions.add(session_id)
        trace = c.bus.subscribe(session_id)
        intent = SubmitIntentInput(
            shopping_session_id=session_id,
            buyer_id=buyer_id,
            locale=body.locale,
            currency=body.currency,
            raw_query=body.raw_query,
            checklist_selection=body.checklist_selection.model_dump() if body.checklist_selection else None,
            memory_consent=memory_consent,
        )

        async def process_turn() -> None:
            try:
                await asyncio.wait_for(c.orchestrator.handle_intent(intent), timeout=240)
            except asyncio.TimeoutError:
                c.bus.publish(session_id, "error", {"message": "处理超时，请稍后重试"})
            except Exception as err:  # noqa: BLE001
                logger.exception("ShopMind SSE 请求异常")
                c.bus.publish(session_id, "error", {"message": str(err)})

        task = asyncio.create_task(process_turn())

        async def stream_events():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(trace.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        if task.done():
                            break
                        if await request.is_disconnected():
                            break
                        yield ": keep-alive\n\n"
                        continue
                    payload = json.dumps(event.to_dict(), ensure_ascii=False)
                    yield f"event: {event.type}\ndata: {payload}\n\n"
            finally:
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                c.bus.unsubscribe(session_id, trace)
                active_sessions.discard(session_id)

        return StreamingResponse(
            stream_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.post("/commerce/mvp/auth/register")
    async def register_account(body: AccountRequest) -> dict:
        try:
            user_id, token = _mvp_store(container()).register(body.username, body.password)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        return {"user_id": user_id, "token": token, "token_ttl_days": 30}

    @api.post("/commerce/mvp/auth/login")
    async def login_account(body: AccountRequest) -> dict:
        try:
            user_id, token = _mvp_store(container()).login(body.username, body.password)
        except ValueError as err:
            raise HTTPException(status_code=401, detail=str(err)) from err
        return {"user_id": user_id, "token": token, "token_ttl_days": 30}

    @api.post("/commerce/mvp/auth/logout")
    async def logout_account(request: Request) -> dict:
        _authenticated_user(container(), request)
        _, _, token = request.headers.get("authorization", "").partition(" ")
        _mvp_store(container()).revoke_token(token)
        return {"logged_out": True}

    @api.get("/commerce/mvp/memory")
    async def get_memory(request: Request) -> dict:
        c = container()
        user_id = _authenticated_user(c, request)
        store = _mvp_store(c)
        enabled = store.has_memory_consent(user_id)
        if not enabled:
            return {"consent": False, "preferences": [], "inventory": []}
        preferences = await c.preference_store.list_by_buyer(user_id)
        return {
            "consent": True,
            "preferences": [
                {"kind": item.kind, "statement": item.statement, "created_at": item.created_at}
                for item in preferences
            ],
            "inventory": store.list_inventory(user_id),
        }

    @api.post("/commerce/mvp/memory/consent")
    async def set_memory_consent(body: MemoryConsentRequest, request: Request) -> dict:
        c = container()
        user_id = _authenticated_user(c, request)
        _mvp_store(c).set_memory_consent(user_id, body.enabled)
        return {"user_id": user_id, "consent": body.enabled}

    @api.post("/commerce/mvp/memory/preferences")
    async def add_memory_preference(body: PreferenceRequest, request: Request) -> dict:
        c = container()
        user_id = _authenticated_user(c, request)
        if not _mvp_store(c).has_memory_consent(user_id):
            raise HTTPException(status_code=403, detail="请先明确授权跨会话记忆")
        try:
            preference = BuyerPreference(user_id, body.kind, body.statement.strip())
            await c.preference_store.append(preference)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        return {"saved": True, "kind": preference.kind, "statement": preference.statement}

    @api.delete("/commerce/mvp/memory/preferences")
    async def delete_memory_preference(statement: str, request: Request) -> dict:
        c = container()
        user_id = _authenticated_user(c, request)
        deleted = await c.preference_store.delete(user_id, statement)
        return {"deleted": deleted}

    @api.post("/commerce/mvp/memory/inventory")
    async def add_inventory_item(body: InventoryRequest, request: Request) -> dict:
        c = container()
        user_id = _authenticated_user(c, request)
        if not _mvp_store(c).has_memory_consent(user_id):
            raise HTTPException(status_code=403, detail="请先明确授权跨会话记忆")
        try:
            item = _mvp_store(c).add_inventory(user_id, body.item, body.quantity, body.condition)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        return {"saved": True, **item}

    @api.delete("/commerce/mvp/memory/inventory/{item}")
    async def delete_inventory_item(item: str, request: Request) -> dict:
        c = container()
        user_id = _authenticated_user(c, request)
        return {"deleted": _mvp_store(c).delete_inventory(user_id, item)}

    @api.delete("/commerce/mvp/memory")
    async def forget_all_memory(request: Request) -> dict:
        c = container()
        user_id = _authenticated_user(c, request)
        preferences = await c.preference_store.list_by_buyer(user_id)
        for preference in preferences:
            await c.preference_store.delete(user_id, preference.statement)
        _mvp_store(c).clear_inventory(user_id)
        _mvp_store(c).set_memory_consent(user_id, False)
        return {"deleted_preferences": len(preferences), "deleted_inventory": True, "consent": False}

    @api.post("/commerce/mvp/feedback")
    async def save_quick_feedback(body: QuickFeedbackRequest) -> dict:
        try:
            return _mvp_store(container()).save_feedback(body.session_id, body.kind, body.value, body.note)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err

    @api.get("/commerce/mvp/feedback/summary")
    async def get_feedback_summary() -> dict:
        return _mvp_store(container()).feedback_summary()

    @api.post("/commerce/mvp/template-candidates")
    async def submit_template_candidate(body: TemplateCandidateRequest) -> dict:
        sections = [section.model_dump(exclude_none=True) for section in body.sections]
        try:
            return _mvp_store(container()).submit_template_candidate(sections)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err

    @api.post("/commerce/mvp/template-candidates/{candidate_id}/evaluate")
    async def evaluate_template_candidate(candidate_id: str, request: Request) -> dict:
        _local_review_request(request)
        try:
            result = _mvp_store(container()).evaluate_template_candidate(candidate_id)
        except ValueError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        if result is None:
            raise HTTPException(status_code=404, detail="候选不存在")
        return result

    @api.get("/commerce/mvp/template-candidates")
    async def list_template_candidates(request: Request, status: str = "pending") -> dict:
        _local_review_request(request)
        try:
            items = _mvp_store(container()).list_template_candidates(status)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        c = container()
        if status == "approved" and c.scene_templates is not None:
            for item in items:
                item["active"] = c.scene_templates.is_published_version_active(
                    item["published_scene_id"], item["published_version"],
                )
        return {"items": items}

    @api.post("/commerce/mvp/template-candidates/{candidate_id}/review")
    async def review_template_candidate(
        candidate_id: str, body: TemplateReviewRequest, request: Request,
    ) -> dict:
        _local_review_request(request)
        c = container()
        mvp_store = _mvp_store(c)
        candidate = mvp_store.get_template_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="候选不存在")
        if candidate["status"] != "pending":
            raise HTTPException(status_code=409, detail="该候选已审核")
        scene_id = ""
        version = None
        if body.action == "approve":
            if c.scene_templates is None:
                raise HTTPException(status_code=503, detail="模板运行时未配置")
            if not candidate["evaluation_passed"]:
                raise HTTPException(status_code=409, detail="候选必须先通过结构评测，才能人工审核发布")
            review = body.model_dump()
            try:
                scene_id, version = c.scene_templates.publish_reviewed_candidate(candidate, review)
            except (ValueError, RuntimeError) as err:
                raise HTTPException(status_code=422, detail=str(err)) from err
            status = "approved"
        else:
            status = "rejected"
        result = mvp_store.review_template_candidate(
            candidate_id, status=status, reviewer=body.reviewer, note=body.note,
            scene_id=scene_id, version=version,
        )
        return result or {"candidate_id": candidate_id, "status": status}

    @api.post("/commerce/mvp/templates/{scene_id}/rollback")
    async def rollback_template(
        scene_id: str, body: TemplateRollbackRequest, request: Request,
    ) -> dict:
        _local_review_request(request)
        c = container()
        if c.scene_templates is None:
            raise HTTPException(status_code=503, detail="模板运行时未配置")
        try:
            restored_version = c.scene_templates.rollback_published_template(scene_id)
        except ValueError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        action = _mvp_store(c).record_template_action(
            scene_id, "rollback", body.reviewer, body.note, restored_version,
        )
        return {"scene_id": scene_id, "restored_version": restored_version, **action}

    @api.post("/commerce/intents/async")
    async def submit_intent_async(body: SubmitIntentRequest, request: Request) -> dict:
        c = container()
        session_id = body.shopping_session_id or f"session-{uuid.uuid4().hex[:8]}"
        buyer_id, memory_consent = _intent_identity(c, request, session_id)
        intent = SubmitIntentInput(
            shopping_session_id=session_id,
            buyer_id=buyer_id,
            locale=body.locale,
            currency=body.currency,
            raw_query=body.raw_query,
            checklist_selection=body.checklist_selection.model_dump() if body.checklist_selection else None,
            memory_consent=memory_consent,
        )
        if c.task_queue is None:
            raise HTTPException(status_code=503, detail="队列未启用，请使用 /commerce/intents")
        task_id = await _enqueue(c, intent)
        return {"shopping_session_id": session_id, "task_id": task_id, "state": "queued"}

    @api.get("/commerce/tasks/{task_id}")
    async def get_task(task_id: str) -> dict:
        c = container()
        if c.task_queue is None:
            raise HTTPException(status_code=503, detail="队列未启用")
        status = await c.task_queue.get_status(task_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"任务不存在或已过期：{task_id}")
        return {
            "task_id": status.task_id,
            "state": status.state,
            "final_text": status.final_text,
            "error": status.error,
            "queue_position": status.queue_position,
        }

    @api.websocket("/commerce/events")
    async def commerce_events(websocket: WebSocket) -> None:
        await state["connections"].serve(websocket)

    @api.get("/commerce/orders/{order_id}")
    async def get_order(order_id: str) -> dict:
        try:
            return await container().query_order.execute(order_id)
        except ValueError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err

    @api.post("/commerce/orders/{order_id}/cancel")
    async def cancel_order_endpoint(order_id: str, body: CancelOrderRequest) -> dict:
        try:
            return await container().cancel_order.execute(order_id, body.reason)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    # 云端镜像同时提供编译后的 React 前端；本地开发仍使用独立 Vite 服务。
    project_root = Path(__file__).resolve().parents[2]
    static_dir = project_root / "frontend-dist"
    if not static_dir.is_dir():
        static_dir = project_root / "frontend" / "dist"
    if static_dir.is_dir() and (static_dir / "index.html").is_file():
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            api.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @api.get("/{path:path}", include_in_schema=False)
        async def serve_frontend(path: str) -> FileResponse:
            if path.startswith("commerce/mvp"):
                raise HTTPException(status_code=404, detail="ShopMind MVP 路由不存在")
            static_root = static_dir.resolve()
            requested = (static_root / path).resolve()
            if requested.is_file() and static_root in requested.parents:
                return FileResponse(requested)
            return FileResponse(static_root / "index.html")

    return api


async def _queue_priority(c: Container, session_id: str) -> int:
    """按对话轮数定队列优先级（0 = 正常，1 = 大请求）。

    长会话上下文大、单次耗时长，分到低优先流，避免堵住新会话。
    轮数计数存 Redis（计数不原子，但优先级本身是启发式，差一两次无影响）；
    Redis 不可用或开关关闭时一律返回 0，退化为单队列。
    """
    if not c.settings.queue_priority_enabled or not c.cache.enabled:
        return 0
    key = f"ecommerce:turns:{session_id}"
    try:
        current = int(await c.cache.get_raw(key) or 0) + 1
        await c.cache.set_json(key, current, _TURN_COUNTER_TTL_SECONDS)
    except Exception:  # noqa: BLE001 —— 计数失败不能影响入队
        return 0
    return 1 if current >= c.settings.queue_large_request_turns else 0


async def _enqueue(c: Container, intent: SubmitIntentInput) -> str:
    """入队并做幂等保护。

    队列是 at-least-once，且买家/前端可能重复提交。用「会话 + 问句」指纹做幂等键，
    命中说明短时间内已提交过同样内容，直接复用原 task_id，不再入队一次。
    这一步对写操作（下单）尤其关键：重复消费等于重复下单。
    """
    fingerprint = hashlib.sha256(
        f"{intent.shopping_session_id}\n{intent.raw_query}".encode(),
    ).hexdigest()[:32]
    idem_key = f"idem:{fingerprint}"
    task_id = f"task-{uuid.uuid4().hex[:12]}"

    acquired = await c.cache.set_if_absent(idem_key, task_id, _IDEMPOTENCY_TTL_SECONDS)
    if not acquired:
        previous = await c.cache.get_raw(idem_key)
        if previous:
            logger.info("幂等命中，复用已有任务：%s", previous)
            return previous

    await c.task_queue.enqueue(  # type: ignore[union-attr]
        IntentTask(
            task_id=task_id,
            shopping_session_id=intent.shopping_session_id,
            buyer_id=intent.buyer_id,
            locale=intent.locale,
            currency=intent.currency,
            raw_query=intent.raw_query,
            memory_consent=intent.memory_consent,
            priority=await _queue_priority(c, intent.shopping_session_id),
        ),
    )
    await c.task_queue.set_status(TaskStatus(task_id=task_id, state="queued"))  # type: ignore[union-attr]
    c.bus.publish(intent.shopping_session_id, "task.queued", {"task_id": task_id})
    return task_id


async def _await_result(c: Container, task_id: str, session_id: str) -> str:
    """等 worker 跑完。

    优先等 final.result 事件（实时）；同时定期查任务状态兜底——
    worker 崩溃或任务进死信时事件永远不会来，只靠等事件会把请求挂死。
    """
    queue = c.bus.subscribe(session_id)
    deadline = time.monotonic() + c.settings.queue_wait_seconds
    try:
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                status = await c.task_queue.get_status(task_id)  # type: ignore[union-attr]
                if status is not None and status.state == "done":
                    return status.final_text
                if status is not None and status.state == "failed":
                    return f"[error] {status.error}"
                continue
            if event.type == "final.result":
                return str(event.payload.get("text", ""))
        return "[error] 处理超时，请稍后重试或改用异步接口查询任务状态"
    finally:
        c.bus.unsubscribe(session_id, queue)


def build_container_origins() -> list[str]:
    """CORS 需要在 app 构造期就确定，此处单独读一次配置。"""
    from app.infrastructure.settings import load_settings

    return load_settings().cors_origins


app = build_app()


if __name__ == "__main__":
    import uvicorn

    from app.infrastructure.settings import load_settings

    uvicorn.run(app, host="0.0.0.0", port=load_settings().port)
