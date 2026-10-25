import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("LLM_API_KEY", "unit-test-placeholder")

from app.application.agents.orchestrator import SubmitIntentOutput
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.persistence.json_file_stores import JsonFilePreferenceStore
from app.infrastructure.persistence.scene_template_store import SceneTemplateStore
from app.infrastructure.persistence.shopmind_mvp_store import ShopMindMvpStore
from app.presentation import server


class LocalMvpContainer:
    def __init__(self, data_dir):
        self.bus = TradeEventBus()
        self.task_queue = None
        self.backplane = None
        self.mvp_store = ShopMindMvpStore(data_dir)
        self.preference_store = JsonFilePreferenceStore(data_dir)
        self.scene_templates = SceneTemplateStore(published_path=data_dir / "published_templates.json")
        self.orchestrator = self
        self.last_intent = None

    async def startup(self):
        pass

    async def shutdown(self):
        pass

    async def handle_intent(self, intent):
        self.last_intent = intent
        self.bus.publish(intent.shopping_session_id, "final.result", {"text": "测试完成"})
        return SubmitIntentOutput(intent.shopping_session_id, "测试完成")


def _client(monkeypatch, tmp_path):
    container = LocalMvpContainer(tmp_path)
    monkeypatch.setattr(server, "build_container_origins", lambda: [])
    monkeypatch.setattr(server, "build_container", lambda: _resolved(container))
    return TestClient(server.build_app()), container


async def _resolved(value):
    return value


def _account(client):
    response = client.post("/commerce/mvp/auth/register", json={"username": "shopper01", "password": "safe-test-password"})
    assert response.status_code == 200
    return response.json()


def test_optional_account_requires_explicit_memory_consent_and_scopes_identity(monkeypatch, tmp_path):
    client, container = _client(monkeypatch, tmp_path)
    with client:
        account = _account(client)
        headers = {"Authorization": f"Bearer {account['token']}"}
        denied = client.post("/commerce/mvp/memory/preferences", headers=headers, json={"kind": "like", "statement": "喜欢易清洁材质"})
        assert denied.status_code == 403
        assert client.post("/commerce/mvp/memory/consent", headers=headers, json={"enabled": True}).json()["consent"] is True
        assert client.post("/commerce/mvp/memory/preferences", headers=headers, json={"kind": "like", "statement": "喜欢易清洁材质"}).status_code == 200
        assert client.post("/commerce/mvp/memory/inventory", headers=headers, json={"item": "猫砂盆", "quantity": "1 个"}).status_code == 200
        memory = client.get("/commerce/mvp/memory", headers=headers).json()
        assert memory["consent"] is True
        assert memory["preferences"][0]["statement"] == "喜欢易清洁材质"
        assert memory["inventory"][0]["item"] == "猫砂盆"

        user_turn = client.post("/commerce/intents/stream", headers=headers, json={
            "shopping_session_id": "account-session", "buyer_id": "forged-buyer", "raw_query": "测试授权身份",
        })
        assert user_turn.status_code == 200
        assert container.last_intent.buyer_id == account["user_id"]
        assert container.last_intent.memory_consent is True

        guest_turn = client.post("/commerce/intents/stream", json={
            "shopping_session_id": "guest-session", "buyer_id": account["user_id"], "raw_query": "测试游客隔离",
        })
        assert guest_turn.status_code == 200
        assert container.last_intent.buyer_id == "guest-guest-session"
        assert container.last_intent.memory_consent is False

        client.post("/commerce/mvp/memory/consent", headers=headers, json={"enabled": False})
        disabled_memory = client.get("/commerce/mvp/memory", headers=headers).json()
        assert disabled_memory == {"consent": False, "preferences": [], "inventory": []}
        assert client.delete("/commerce/mvp/memory", headers=headers).json()["consent"] is False


def test_one_tap_feedback_and_explicit_purchase_outcome_are_aggregated(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    with client:
        first = client.post("/commerce/mvp/feedback", json={"session_id": "fb-1", "kind": "checklist_usefulness", "value": "helpful"})
        assert first.status_code == 200
        client.post("/commerce/mvp/feedback", json={"session_id": "fb-1", "kind": "purchase_outcome", "value": "not_found"})
        client.post("/commerce/mvp/feedback", json={"session_id": "fb-2", "kind": "checklist_usefulness", "value": "needs_work"})
        # Re-submission updates one session record instead of inflating the response count.
        client.post("/commerce/mvp/feedback", json={"session_id": "fb-2", "kind": "checklist_usefulness", "value": "helpful"})
        summary = client.get("/commerce/mvp/feedback/summary").json()
        assert summary["checklist_usefulness"] == {"responses": 2, "helpful": 2, "needs_work": 0, "helpful_rate": 1.0}
        assert summary["purchase_outcomes"] == {"not_found": 1}


def test_candidate_anonymization_human_review_and_versioned_publish(monkeypatch, tmp_path):
    client, container = _client(monkeypatch, tmp_path)
    sections = [
        {"title": "出发准备", "required": [
            {"name": "证件收纳袋"}, {"name": "身份证件", "purchasable": False},
        ], "optional": [{"name": "行李秤", "condition": "托运行李时"}]},
        {"title": "途中用品", "required": [{"name": "颈枕"}], "optional": [{"name": "眼罩"}]},
    ]
    with client:
        personal = client.post("/commerce/mvp/template-candidates", json={
            "anonymized": True, "sections": [{"title": "联系方式 13800138000", "required": [{"name": "物品"}]}] * 2,
        })
        assert personal.status_code == 422

        submitted = client.post("/commerce/mvp/template-candidates", json={"anonymized": True, "sections": sections})
        assert submitted.status_code == 200
        candidate = submitted.json()
        assert "session_id" not in candidate and "raw_query" not in candidate and "goal" not in candidate
        pending = client.get("/commerce/mvp/template-candidates?status=pending").json()["items"]
        assert len(pending) == 1 and pending[0]["candidate_id"] == candidate["candidate_id"]

        rejected = client.post(f"/commerce/mvp/template-candidates/{candidate['candidate_id']}/review", json={
            "action": "reject", "reviewer": "human-reviewer", "note": "模块边界需重做",
        })
        assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
        approved_candidate = client.post("/commerce/mvp/template-candidates", json={"anonymized": True, "sections": sections}).json()
        pre_evaluation = client.post(f"/commerce/mvp/template-candidates/{approved_candidate['candidate_id']}/review", json={
            "action": "approve", "reviewer": "human-reviewer", "scene_id": "travel_accessories",
            "name": "轻量旅行准备", "core_use": "准备一次短途旅行",
            "match_terms": ["轻装出行", "周末短途旅行"],
        })
        assert pre_evaluation.status_code == 409
        evaluation = client.post(f"/commerce/mvp/template-candidates/{approved_candidate['candidate_id']}/evaluate")
        assert evaluation.status_code == 200 and evaluation.json()["evaluation_passed"] is True
        approved = client.post(f"/commerce/mvp/template-candidates/{approved_candidate['candidate_id']}/review", json={
            "action": "approve", "reviewer": "human-reviewer", "scene_id": "travel_accessories",
            "name": "轻量旅行准备", "core_use": "准备一次短途旅行",
            "semantic_description": "为短途旅程准备随身和行李物品",
            "match_terms": ["轻装出行", "周末短途旅行"], "incompatible_terms": [],
        })
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert approved.json()["published_version"] is not None
        scene = container.scene_templates.match("我想周末短途旅行准备东西")
        assert scene and scene["id"] == "travel_accessories"
        plan = container.scene_templates.build_plan("我想周末短途旅行准备东西", scene)
        assert plan.template_version == approved.json()["published_version"]
        assert len(plan.checklist_sections) == 2
        published_items = {item["name"]: item for section in plan.checklist_sections
                           for item in [*section["required"], *section["optional"]]}
        assert published_items["身份证件"]["purchasable"] is False
        reloaded = SceneTemplateStore(published_path=tmp_path / "published_templates.json")
        assert reloaded.match("我想周末短途旅行准备东西")["id"] == "travel_accessories"
        second_candidate = client.post("/commerce/mvp/template-candidates", json={"anonymized": True, "sections": sections}).json()
        assert client.post(f"/commerce/mvp/template-candidates/{second_candidate['candidate_id']}/evaluate").json()["evaluation_passed"] is True
        second_version = client.post(f"/commerce/mvp/template-candidates/{second_candidate['candidate_id']}/review", json={
            "action": "approve", "reviewer": "human-reviewer", "scene_id": "travel_accessories",
            "name": "短途出行物品", "core_use": "准备周末短途出行",
            "semantic_description": "更新后的短途出行清单",
            "match_terms": ["轻装出行", "周末短途旅行"], "incompatible_terms": [],
        }).json()["published_version"]
        assert container.scene_templates.match("我想周末短途旅行准备东西")["version"] == second_version
        rollback = client.post("/commerce/mvp/templates/travel_accessories/rollback", json={
            "reviewer": "human-reviewer", "note": "回退刚发布的清单版本",
        })
        assert rollback.status_code == 200
        assert rollback.json()["restored_version"] == approved.json()["published_version"]
        assert container.scene_templates.match("我想周末短途旅行准备东西")["version"] == approved.json()["published_version"]


def test_candidate_request_rejects_raw_query_and_account_login_works(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    with client:
        account = _account(client)
        login = client.post("/commerce/mvp/auth/login", json={"username": "shopper01", "password": "safe-test-password"})
        assert login.status_code == 200 and login.json()["user_id"] == account["user_id"]
        rejected = client.post("/commerce/mvp/template-candidates", json={
            "anonymized": True, "raw_query": "不得收集原始对话", "sections": [
                {"title": "模块一", "required": [{"name": "物品一"}]},
                {"title": "模块二", "required": [{"name": "物品二"}]},
            ],
        })
        assert rejected.status_code == 422


def test_template_candidate_must_pass_structural_evaluation_before_publication(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    sections = [
        {"title": "准备环节", "required": [{"name": "通用用品"}]},
        {"title": "收尾环节", "optional": [{"name": "物品"}]},
    ]
    with client:
        candidate = client.post("/commerce/mvp/template-candidates", json={"anonymized": True, "sections": sections}).json()
        evaluation = client.post(f"/commerce/mvp/template-candidates/{candidate['candidate_id']}/evaluate").json()
        assert evaluation["evaluation_passed"] is False
        assert evaluation["evaluation"]["checks"]["specific_item_names"] is False
        review = client.post(f"/commerce/mvp/template-candidates/{candidate['candidate_id']}/review", json={
            "action": "approve", "reviewer": "human-reviewer", "scene_id": "generic_scene",
            "name": "通用场景", "core_use": "准备通用物品", "match_terms": ["通用物品", "通用场景"],
        })
        assert review.status_code == 409

