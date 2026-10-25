import json
from pathlib import Path

import pytest

from app.infrastructure.persistence.scene_template_store import SceneTemplateStore


_CASES = json.loads((Path(__file__).parents[1] / "eval" / "shopmind_mvp_regression_v1.json").read_text(encoding="utf-8"))["cases"]


class CatBiasedEmbedder:
    """Ensure explicit dog entity conflicts defeat even an incorrect semantic score."""
    async def embed_batch(self, texts):
        return [[1.0, 0.0] if "猫" in text else [0.0, 1.0] for text in texts]

    async def embed(self, text):
        return [1.0, 0.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _CASES, ids=[case["id"] for case in _CASES])
async def test_routing_regression_dataset(case):
    store = SceneTemplateStore(embedder=CatBiasedEmbedder() if case["route"] == "open_scene" and case["id"].startswith("dog_") else None)
    scene = store.match(case["query"])
    route = "keyword" if scene else "semantic"
    if scene is None:
        scene = await store.match_semantic(case["query"])
    if scene is None:
        route = "open_scene"

    if case["route"] == "open_scene":
        assert scene is None or scene["id"] != case.get("forbidden_template_id")
        assert scene is None, "开放场景不得意外命中任何现存模板"
        return

    assert scene is not None and scene["id"] == case["template_id"]
    assert route == case["route"]
    plan = store.build_plan(case["query"], scene)
    assert len(plan.checklist_sections) >= case["min_modules"]
    assert set(case["required_anchors"]).issubset(plan.tasks)
