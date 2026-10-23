# -*- coding: utf-8 -*-
"""A small, validated snapshot of the buyer's current shopping mission."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


STAGES = {"explore", "clarify", "search", "compare", "confirm", "service"}


@dataclass(frozen=True)
class ShoppingPlan:
    goal: str
    stage: str = "explore"
    destination: Optional[str] = None
    budget_major: Optional[float] = None
    currency: str = "CNY"
    preferences: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    core_use: Optional[str] = None
    people: Optional[int] = None
    state_version: int = 0
    template_id: Optional[str] = None
    template_version: Optional[int] = None
    checklist_sections: list[dict] = field(default_factory=list)
    must_buy: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    reusable: list[str] = field(default_factory=list)
    not_buy: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    prep: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("购物目标不能为空")
        if self.stage not in STAGES:
            raise ValueError(f"未知购物阶段：{self.stage}")
        if self.budget_major is not None and self.budget_major <= 0:
            raise ValueError("预算必须大于 0")
        if self.people is not None and self.people <= 0:
            raise ValueError("人数必须为正整数")
        if self.state_version < 0:
            raise ValueError("state_version 不能为负数")
        if len(self.tasks) > 32:
            raise ValueError("单份清单最多包含 32 件具体物品，请按任务分批整理")

    def to_dict(self) -> dict:
        return asdict(self)
