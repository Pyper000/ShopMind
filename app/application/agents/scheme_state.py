# -*- coding: utf-8 -*-
"""Structured shopping state shared by MainAgent and SearchAgent."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from app.domain.session.shopping_plan import ShoppingPlan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchemeMutation:
    state_version: int
    target_slots: list[dict]
    added_slots: list[str]
    removed_slots: list[str]
    existing_slots: list[str]
    deferred_slots: list[str]
    pending_slots: list[str]
    budget_change: Optional[dict]
    preference_change: Optional[dict]
    scene_changed: bool
    core_use_changed: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SearchRequest:
    scheme: ShoppingPlan
    mutation: SchemeMutation

    def to_dict(self) -> dict:
        return {"scheme": self.scheme.to_dict(), "mutation": self.mutation.to_dict()}


def build_mutation(previous: Optional[ShoppingPlan], current: ShoppingPlan) -> SchemeMutation:
    old_buy = list(dict.fromkeys([
        *(previous.must_buy if previous else []),
        *(previous.optional if previous else []),
    ]))
    new_buy = list(dict.fromkeys([*current.must_buy, *current.optional]))
    old_set, new_set = set(old_buy), set(new_buy)
    removed = [slot for slot in old_buy if slot not in new_set]
    added = [slot for slot in new_buy if slot not in old_set]
    old_budget = previous.budget_major if previous else None
    old_preferences = previous.preferences if previous else []
    budget_change = (
        {"from": old_budget, "to": current.budget_major}
        if old_budget != current.budget_major else None
    )
    preference_change = (
        {"from": old_preferences, "to": current.preferences}
        if old_preferences != current.preferences else None
    )
    goal_changed = bool(previous and previous.goal != current.goal)
    core_use_changed = bool(previous and previous.core_use != current.core_use)
    people_changed = bool(previous and previous.people != current.people)
    scene_changed = goal_changed or core_use_changed or people_changed

    statuses: dict[str, str] = {}
    for slot in current.tasks:
        if slot in current.reusable:
            statuses[slot] = "have"
        elif slot in current.not_buy:
            statuses[slot] = "deferred"
        elif slot in current.unknowns:
            statuses[slot] = "pending"
        elif slot in current.optional:
            statuses[slot] = "optional"
        elif slot in current.must_buy:
            statuses[slot] = "must_buy"
        else:
            statuses[slot] = "pending"
    for slot in [*current.must_buy, *current.optional, *current.reusable, *current.not_buy, *current.unknowns]:
        if slot in current.must_buy:
            status = "must_buy"
        elif slot in current.optional:
            status = "optional"
        elif slot in current.reusable:
            status = "have"
        elif slot in current.not_buy:
            status = "deferred"
        else:
            status = "pending"
        statuses.setdefault(slot, status)
    statuses.update({slot: "removed" for slot in removed})

    return SchemeMutation(
        state_version=current.state_version,
        target_slots=[{"name": name, "status": status} for name, status in statuses.items()],
        added_slots=added,
        removed_slots=removed,
        existing_slots=list(current.reusable),
        deferred_slots=list(current.not_buy),
        pending_slots=list(current.unknowns),
        budget_change=budget_change,
        preference_change=preference_change,
        scene_changed=scene_changed,
        core_use_changed=core_use_changed,
    )


class SchemeStateRegistry:
    """Versioned scheme snapshots in DATA_DIR; AgentState stores conversation history."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._data_dir = data_dir / "schemes" if data_dir else None
        self._states: dict[str, ShoppingPlan] = {}
        self._mutations: dict[str, SchemeMutation] = {}

    def update(self, session_id: str, plan: ShoppingPlan) -> SearchRequest:
        previous = self._states.get(session_id)
        if previous is None:
            self._load(session_id)
            previous = self._states.get(session_id)
        plan = ShoppingPlan(**{**plan.to_dict(), "state_version": (previous.state_version if previous else 0) + 1})
        mutation = build_mutation(previous, plan)
        self._states[session_id] = plan
        self._mutations[session_id] = mutation
        self._save(session_id, plan, mutation)
        return SearchRequest(plan, mutation)

    def get(self, session_id: str) -> Optional[SearchRequest]:
        if session_id not in self._states:
            self._load(session_id)
        plan = self._states.get(session_id)
        mutation = self._mutations.get(session_id)
        return SearchRequest(plan, mutation) if plan and mutation else None

    def _path(self, session_id: str) -> Optional[Path]:
        if self._data_dir is None or not session_id.replace("-", "").replace("_", "").isalnum():
            return None
        return self._data_dir / f"{session_id}.json"

    def _load(self, session_id: str) -> None:
        path = self._path(session_id)
        if path is None or not path.is_file():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            self._states[session_id] = ShoppingPlan(**saved["scheme"])
            self._mutations[session_id] = SchemeMutation(**saved["mutation"])
        except (OSError, ValueError, TypeError, KeyError) as err:
            logger.warning("方案状态读取失败，会话按新方案处理：%s (%s)", session_id, err)

    def _save(self, session_id: str, plan: ShoppingPlan, mutation: SchemeMutation) -> None:
        path = self._path(session_id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            snapshot = {"scheme": plan.to_dict(), "mutation": mutation.to_dict()}
            temporary.write_text(
                json.dumps(snapshot, ensure_ascii=False), encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as err:
            logger.warning("方案状态落盘失败：%s (%s)", session_id, err)


class CandidatePool:
    """Candidate IDs grouped by session and task; only catalog retrieval results enter it."""

    def __init__(self) -> None:
        self._pools: dict[str, dict[str, list[str]]] = {}
        self._result_counts: dict[str, dict[str, int]] = {}

    def get(self, session_id: str, task: str) -> list[str]:
        return list(self._pools.get(session_id, {}).get(task, []))

    def result_count(self, session_id: str, task: str) -> int:
        return self._result_counts.get(session_id, {}).get(task, 0)

    def put(
        self, session_id: str, task: str, product_ids: list[str], *,
        merge: bool = False, result_count: int = 0,
    ) -> None:
        slots = self._pools.setdefault(session_id, {})
        previous = slots.get(task, []) if merge else []
        slots[task] = list(dict.fromkeys([*previous, *product_ids]))
        self._result_counts.setdefault(session_id, {})[task] = result_count

    def apply(self, session_id: str, mutation: SchemeMutation) -> None:
        slots = self._pools.setdefault(session_id, {})
        if mutation.scene_changed:
            slots.clear()
            self._result_counts.pop(session_id, None)
            return
        excluded = set(mutation.removed_slots + mutation.existing_slots + mutation.deferred_slots + mutation.pending_slots)
        for task in excluded:
            slots.pop(task, None)
            self._result_counts.get(session_id, {}).pop(task, None)

    def clear(self, session_id: str) -> None:
        self._pools.pop(session_id, None)
        self._result_counts.pop(session_id, None)


def choose_retrieval_action(
    request: Optional[SearchRequest], task: str, cached_ids: list[str],
    min_candidates: int = 3, cached_count: Optional[int] = None,
) -> tuple[str, str]:
    if request is None:
        return "recall_local", "没有结构化方案快照，按当前商品任务召回。"
    mutation = request.mutation
    if mutation.scene_changed or mutation.core_use_changed:
        return "recall_all", "场景、人数或核心用途变化，按完整方案重新召回。"
    if task in mutation.added_slots:
        return "recall_local", f"仅为新增任务局部召回：{task}。"
    if not cached_ids or (cached_count is not None and cached_count < min_candidates):
        return "recall_local", f"仅为候选不足的任务局部补充召回：{task}。"
    if mutation.budget_change or mutation.preference_change:
        return "filter_and_rerank", "预算或偏好变化，复用该任务候选并重新过滤排序。"
    return "filter_and_rerank", "复用该任务候选，按当前方案重新过滤排序。"
