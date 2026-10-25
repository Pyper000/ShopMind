# -*- coding: utf-8 -*-
"""Small, versioned scene templates for fast first-pass checklists."""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Optional

from app.domain.catalog.ports.retrieval_ports import EmbeddingClient
from app.domain.session.shopping_plan import ShoppingPlan


_TEMPLATE_PATH = Path(__file__).with_name("scene_templates.json")
logger = logging.getLogger(__name__)


class SceneTemplateStore:
    """Read the shipped template catalogue; templates survive app restarts/deploys."""

    def __init__(
        self, path: Optional[Path] = None, embedder: Optional[EmbeddingClient] = None,
        semantic_threshold: float = 0.76, published_path: Optional[Path] = None,
    ) -> None:
        self._path = path or _TEMPLATE_PATH
        self._published_path = published_path
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self.version = int(data["version"])
        self._base_modules = {module["id"]: module for module in data.get("modules", [])}
        self._base_scenes = list(data["scenes"])
        self._published_modules: dict[str, dict] = {}
        self._published_scenes: list[dict] = []
        if self._published_path and self._published_path.exists():
            published = json.loads(self._published_path.read_text(encoding="utf-8"))
            self._published_modules.update({module["id"]: module for module in published.get("modules", [])})
            self._published_scenes = list(published.get("scenes", []))
            self.version = max(self.version, int(published.get("version", self.version)))
        self._refresh_catalog()
        self._embedder = embedder
        self._semantic_threshold = semantic_threshold
        self._scene_vectors: Optional[dict[str, list[list[float]]]] = None

    def publish_reviewed_candidate(self, candidate: dict, review: dict) -> tuple[str, int]:
        """Persist a human-authored template version after an explicit approval."""
        if self._published_path is None:
            raise RuntimeError("当前实例未配置可写的发布目录")
        scene_id = str(review.get("scene_id", "")).strip()
        name = str(review.get("name", "")).strip()
        core_use = str(review.get("core_use", "")).strip()
        match_terms = list(dict.fromkeys(str(term).strip() for term in review.get("match_terms", []) if str(term).strip()))
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", scene_id):
            raise ValueError("scene_id 需为 3–64 位小写字母、数字或下划线，并以字母开头")
        if not name or len(name) > 80 or not core_use or len(core_use) > 120:
            raise ValueError("模板名称和核心用途不能为空")
        if len(match_terms) < 2 or any(len(term) > 40 for term in match_terms):
            raise ValueError("至少填写两个长度不超过 40 字的匹配词")
        sections = candidate.get("sections", [])
        if not 2 <= len(sections) <= 6:
            raise ValueError("候选清单必须有 2–6 个模块")
        next_version = self.version + 1
        modules: list[dict] = []
        module_ids: list[str] = []
        for index, section in enumerate(sections, start=1):
            module_id = f"{scene_id}_v{next_version}_{index}"
            module_ids.append(module_id)
            modules.append({
                "id": module_id,
                "title": section["title"],
                "required": section["required"],
                "optional": section["optional"],
            })
        scene = {
            "id": scene_id,
            "name": name,
            "core_use": core_use,
            "match_terms": match_terms,
            "semantic_description": str(review.get("semantic_description", "")).strip() or f"{name}；{core_use}",
            "semantic_examples": [],
            "incompatible_terms": [str(term).strip() for term in review.get("incompatible_terms", []) if str(term).strip()],
            "module_ids": module_ids,
            "version": next_version,
            "active": True,
        }
        self._published_scenes = [
            {**item, "active": False} if item.get("id") == scene_id and item.get("active", True) else item
            for item in self._published_scenes
        ]
        self._published_modules.update({module["id"]: module for module in modules})
        self._published_scenes.append(scene)
        self.version = next_version
        self._write_published_catalog()
        self._refresh_catalog()
        self._scene_vectors = None
        return scene_id, next_version

    def rollback_published_template(self, scene_id: str) -> Optional[int]:
        """Deactivate the current published version and reactivate its prior version."""
        versions = sorted(
            (scene for scene in self._published_scenes if scene.get("id") == scene_id),
            key=lambda scene: int(scene.get("version", 0)),
        )
        active = [scene for scene in versions if scene.get("active", True)]
        if not active:
            raise ValueError("没有可回退的已发布模板")
        current = active[-1]
        self._published_scenes = [
            {**scene, "active": False} if scene.get("version") == current.get("version") else scene
            for scene in self._published_scenes
        ]
        previous = [scene for scene in versions if int(scene.get("version", 0)) < int(current.get("version", 0))]
        restored_version = int(previous[-1]["version"]) if previous else None
        if restored_version is not None:
            self._published_scenes = [
                {**scene, "active": True} if scene.get("id") == scene_id and scene.get("version") == restored_version else scene
                for scene in self._published_scenes
            ]
        self._write_published_catalog()
        self._refresh_catalog()
        self._scene_vectors = None
        return restored_version

    def is_published_version_active(self, scene_id: str, version: Optional[int]) -> bool:
        return any(
            scene.get("id") == scene_id
            and scene.get("version") == version
            and scene.get("active", True)
            for scene in self._published_scenes
        )

    def _refresh_catalog(self) -> None:
        self._modules = {**self._base_modules, **self._published_modules}
        active = [scene for scene in self._published_scenes if scene.get("active", True)]
        active_ids = {scene["id"] for scene in active}
        self._scenes = [*[scene for scene in self._base_scenes if scene["id"] not in active_ids], *active]

    def _write_published_catalog(self) -> None:
        if self._published_path is None:
            raise RuntimeError("当前实例未配置可写的发布目录")
        payload = {
            "version": self.version,
            "modules": list(self._published_modules.values()),
            "scenes": self._published_scenes,
        }
        self._published_path.parent.mkdir(parents=True, exist_ok=True)
        self._published_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def match(self, query: str) -> Optional[dict]:
        normalized = query.casefold().strip()
        if self._explicitly_excludes_scene(re.sub(r"\s+", "", normalized)):
            return None
        matches = [
            scene for scene in self._scenes
            if self._scene_is_compatible(normalized, scene)
            and any(term in normalized for term in scene["match_terms"])
        ]
        return max(
            matches,
            key=lambda scene: (
                max((len(term) for term in scene["match_terms"] if term in normalized), default=0),
                int(scene.get("version", 0)),
            ),
            default=None,
        )

    async def match_semantic(self, query: str) -> Optional[dict]:
        """Match a paraphrased task to a known template; safely fall through on embedding errors."""
        lexical_match = self.match(query)
        if lexical_match is not None:
            return lexical_match
        normalized = re.sub(r"\s+", "", query.casefold())
        if self._explicitly_excludes_scene(normalized):
            return None
        compatible_scenes = [scene for scene in self._scenes if self._scene_is_compatible(normalized, scene)]
        if not compatible_scenes:
            return None
        if self._embedder is not None:
            try:
                if self._scene_vectors is None:
                    descriptions: list[str] = []
                    scene_ids: list[str] = []
                    for scene in self._scenes:
                        prototypes = [
                            scene.get("semantic_description")
                            or "；".join((scene["name"], scene["core_use"], *scene["match_terms"])),
                            *scene.get("semantic_examples", []),
                        ]
                        descriptions.extend(prototypes)
                        scene_ids.extend([scene["id"]] * len(prototypes))
                    vectors = await self._embedder.embed_batch([*descriptions, query])
                    if len(vectors) != len(descriptions) + 1:
                        raise RuntimeError("场景模板向量数量不完整")
                    self._scene_vectors = {}
                    for scene_id, vector in zip(scene_ids, vectors[:-1]):
                        self._scene_vectors.setdefault(scene_id, []).append(vector)
                    query_vector = vectors[-1]
                else:
                    query_vector = await self._embedder.embed(query)
                if len(self._scene_vectors) != len(self._scenes):
                    raise RuntimeError("已缓存的场景模板向量数量不完整")
            except Exception as err:  # noqa: BLE001 — semantic routing must never block ordinary planning
                logger.warning("场景语义匹配失败，回退开放场景规划：%s", err)
            else:
                ranked = sorted(
                    (
                        (max(self._cosine(query_vector, vector) for vector in self._scene_vectors[scene["id"]]), scene)
                        for scene in compatible_scenes
                    ),
                    key=lambda entry: entry[0], reverse=True,
                )
                if ranked and ranked[0][0] >= self._semantic_threshold:
                    if len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.04:
                        return ranked[0][1]

        # Keep a narrow, explicit intent fallback for the reported adult-cat paraphrase
        # when embeddings are disabled or unavailable; vector matching is attempted first.
        if "猫" in normalized and any(
            cue in normalized for cue in ("第一次", "首次", "成年", "接回", "领养", "收养", "养一只", "新猫", "刚养")
        ):
            return next((scene for scene in compatible_scenes if scene["id"] == "first_cat_home"), None)
        return None

    @staticmethod
    def _scene_is_compatible(query: str, scene: dict) -> bool:
        """Do not let semantic similarity override explicit incompatible entities."""
        return not any(term in query for term in scene.get("incompatible_terms", []))

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0

    @staticmethod
    def _explicitly_excludes_scene(query: str) -> bool:
        return (
            any(term in query for term in ("不去海边", "不是海边", "取消海边", "不去沙滩", "不养猫", "不接猫", "取消养猫"))
            or bool(re.search(r"(?:不想|不打算|暂不|不要|无需).{0,8}(?:养|接|领养|收养).{0,8}猫(?:咪)?", query))
        )

    def build_plan(self, query: str, scene: dict) -> ShoppingPlan:
        duration_query = re.sub(
            r"(?:(?:20\d{2})(?:年|[-/.]))?\d{1,2}(?:月|[-/.])\d{1,2}日?"
            r"(?:(?:至|到|[-~～])\d{1,2}日?)?",
            "", query,
        )
        duration = re.search(r"([0-9]+|[一二两三四五六七八九十]+)\s*(天|晚|夜)", duration_query)
        if duration is None:
            duration = re.search(r"([0-9]+|[一二两三四五六七八九十]+)\s*日", duration_query)
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
            template_version=int(scene.get("version", self.version)),
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
