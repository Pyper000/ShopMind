# -*- coding: utf-8 -*-
"""Small, versioned scene templates for fast first-pass checklists."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from app.domain.session.shopping_plan import ShoppingPlan


_TEMPLATE_PATH = Path(__file__).with_name("scene_templates.json")


class SceneTemplateStore:
    """Read the shipped template catalogue; templates survive app restarts/deploys."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _TEMPLATE_PATH
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self.version = int(data["version"])
        self._modules = {module["id"]: module for module in data.get("modules", [])}
        self._scenes = list(data["scenes"])

    def match(self, query: str) -> Optional[dict]:
        normalized = query.casefold().strip()
        if any(term in normalized for term in ("不去海边", "不是海边", "取消海边", "不去沙滩")):
            return None
        return next(
            (scene for scene in self._scenes if any(term in normalized for term in scene["match_terms"])),
            None,
        )

    def build_plan(self, query: str, scene: dict) -> ShoppingPlan:
        duration = re.search(r"([0-9]+|[一二两三四五六七八九十]+)\s*(天|日|晚|夜)", query)
        duration_days = self._number(duration.group(1)) if duration else (2 if "周末" in query else None)
        people = re.search(r"([0-9]+)\s*人", query)
        if scene.get("module_ids"):
            sections = [self._build_section(self._modules[module_id], duration_days, int(people.group(1)) if people else None)
                        for module_id in scene["module_ids"]]
            required = [item["name"] for section in sections for item in section["required"]]
            optional = [item["name"] for section in sections for item in section["optional"]]
        else:
            required = list(scene["must_have"])
            optional = list(scene["optional"])
            sections = [{"id": scene["id"], "title": scene["name"],
                         "required": [{"name": item} for item in required],
                         "optional": [{"name": item} for item in optional]}]
        items = list(dict.fromkeys([*required, *optional]))
        duration_label = f"{duration_days}天" if duration_days else "周末" if "周末" in query else None
        goal = f"{scene['name']}（{duration_label}）" if duration_label else scene["name"]
        return ShoppingPlan(
            goal=goal,
            core_use=scene["core_use"],
            people=int(people.group(1)) if people else None,
            stage="explore",
            tasks=items,
            optional=optional,
            unknowns=items,
            prep=list(scene.get("prep", [])),
            template_id=scene["id"],
            template_version=self.version,
            checklist_sections=sections,
        )

    def _build_section(self, module: dict, days: int | None, people: int | None) -> dict:
        def build_items(items: list[dict]) -> list[dict]:
            result = []
            for item in items:
                entry = {key: value for key, value in item.items() if key not in {"quantity_rule", "unit", "extra", "per_person"}}
                rule = item.get("quantity_rule")
                if rule == "days_plus_extra":
                    unit = item.get("unit", "件")
                    extra = int(item.get("extra", 1))
                    if days:
                        count = days + extra
                        if item.get("per_person") and people:
                            count *= people
                        entry["quantity"] = f"{count}{unit}"
                    else:
                        entry["quantity"] = f"按天数×{people or 1}人 + {extra}备用{unit}"
                result.append(entry)
            return result

        section = {
            "id": module["id"], "title": module["title"],
            "required": build_items(module.get("required", [])),
            "optional": build_items(module.get("optional", [])),
        }
        if module.get("reference"):
            section["reference"] = module["reference"]
        return section

    @staticmethod
    def _number(value: str) -> int:
        if value.isdigit():
            return int(value)
        digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9}
        if "十" in value:
            left, _, right = value.partition("十")
            return (digits.get(left, 1) if left else 1) * 10 + digits.get(right, 0)
        return digits.get(value, 1)
