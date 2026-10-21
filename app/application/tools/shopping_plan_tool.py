# -*- coding: utf-8 -*-
"""MainAgent tool for publishing one structured shopping mission."""
import json
from typing import Optional

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolChunk

from app.domain.session.shopping_plan import ShoppingPlan
from app.application.agents.scheme_state import CandidatePool, SchemeStateRegistry
from app.infrastructure.context import ShoppingContext
from app.infrastructure.eventbus import TradeEventBus


def build_update_shopping_plan_tool(
    bus: TradeEventBus,
    scheme_states: SchemeStateRegistry | None = None,
    candidate_pool: CandidatePool | None = None,
):
    async def update_shopping_plan_tool(
        goal: str,
        stage: str = "explore",
        core_use: Optional[str] = None,
        people: int | str | None = None,
        destination: Optional[str] = None,
        budget_major: float | str | None = None,
        currency: str = "CNY",
        preferences: list[str] | None = None,
        tasks: list[str] | None = None,
        must_buy: list[str] | None = None,
        optional: list[str] | None = None,
        reusable: list[str] | None = None,
        not_buy: list[str] | None = None,
        unknowns: list[str] | None = None,
        excludes: list[str] | None = None,
        prep: list[str] | None = None,
        missing_slots: list[str] | None = None,
    ) -> ToolChunk:
        """Create or update the structured plan for a scene-based shopping request.

        Args:
            goal (`str`): The buyer's overall mission, such as "prepare for a US camping trip".
            stage (`str`): One of explore, clarify, search, compare, confirm, service.
            core_use (`str | None`): The main activity or purpose that defines this shopping mission.
            people (`int | None`): Number of people; leave unknown as None.
            destination (`str | None`): Two-letter destination code, such as US or JP.
            budget_major (`float | None`): Total budget in major currency units.
            currency (`str`): Budget currency, default CNY.
            preferences (`list[str] | None`): Explicit preferences such as lightweight or durable.
            tasks (`list[str] | None`): Up to four concrete product tasks, such as camping light.
            must_buy (`list[str] | None`): Confirmed missing items to purchase.
            optional (`list[str] | None`): Optional purchase slots.
            reusable (`list[str] | None`): Items the buyer already owns.
            not_buy (`list[str] | None`): Deferred or explicitly unwanted purchase slots.
            unknowns (`list[str] | None`): Purchase status not yet confirmed.
            excludes (`list[str] | None`): Explicit product exclusions.
            prep (`list[str] | None`): Non-product reminders; never searched.
            missing_slots (`list[str] | None`): Important unknowns that may require one question.
        """
        if isinstance(budget_major, str):
            try:
                budget_major = float(budget_major)
            except ValueError:
                return ToolChunk(
                    content=[TextBlock(type="text", text=f"[error] budget_major 非法：{budget_major}")],
                    state=ToolResultState.ERROR,
                )
        if isinstance(people, str):
            try:
                people = int(people)
            except ValueError:
                return ToolChunk(
                    content=[TextBlock(type="text", text=f"[error] people 非法：{people}")],
                    state=ToolResultState.ERROR,
                )
        try:
            plan = ShoppingPlan(
                goal=goal,
                core_use=core_use,
                people=people,
                stage=stage,
                destination=destination.upper() if destination else None,
                budget_major=budget_major,
                currency=currency.upper(),
                preferences=preferences or [],
                tasks=tasks or [],
                must_buy=must_buy or [],
                optional=optional or [],
                reusable=reusable or [],
                not_buy=not_buy or [],
                unknowns=unknowns or [],
                excludes=excludes or [],
                prep=prep or [],
                missing_slots=missing_slots or [],
            )
        except ValueError as err:
            return ToolChunk(
                content=[TextBlock(type="text", text=f"[error] {err}")],
                state=ToolResultState.ERROR,
            )

        session_id = ShoppingContext.current_session_id()
        request = scheme_states.update(session_id, plan) if scheme_states else None
        if request and candidate_pool:
            candidate_pool.apply(session_id, request.mutation)
        if request:
            plan = request.scheme
        payload = plan.to_dict()
        if request:
            payload["mutation"] = request.mutation.to_dict()
            payload["search_request"] = request.to_dict()
        bus.publish(
            session_id,
            "plan.update",
            {
                "shopping_plan": plan.to_dict(),
                **({"mutation": request.mutation.to_dict(), "search_request": request.to_dict()} if request else {}),
            },
        )
        return ToolChunk(
            content=[TextBlock(type="text", text=json.dumps(payload, ensure_ascii=False))],
            state=ToolResultState.SUCCESS,
        )

    return update_shopping_plan_tool
