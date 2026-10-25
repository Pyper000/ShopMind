from app.application.agents.orchestrator import MainAgentOrchestrator, SubmitIntentInput
from app.application.memory.preference_selector import PreferenceSelector
from app.infrastructure.persistence.shopmind_mvp_store import ShopMindMvpStore


class EmptyPreferenceStore:
    async def list_by_buyer(self, buyer_id):
        return []


def _orchestrator(preference_store, inventory_store):
    value = object.__new__(MainAgentOrchestrator)
    value._scheme_states = None
    value._preference_store = preference_store
    value._preference_selector = PreferenceSelector()
    value._preference_top_k = 5
    value._inventory_store = inventory_store
    value._injected_memory_hints = {}
    return value


async def test_user_authorized_inventory_reaches_main_agent_only(tmp_path):
    memory_store = ShopMindMvpStore(tmp_path)
    user_id, _ = memory_store.register("inventory01", "safe-test-password")
    memory_store.set_memory_consent(user_id, True)
    memory_store.add_inventory(user_id, "猫砂盆", "1 个", "开放式")
    orchestrator = _orchestrator(EmptyPreferenceStore(), memory_store)
    intent = SubmitIntentInput(
        shopping_session_id="inventory-session", buyer_id=user_id,
        locale="zh-CN", currency="CNY", raw_query="我准备养猫", memory_consent=True,
    )

    messages = await orchestrator._build_inputs(intent, intent.shopping_session_id)
    hint = messages[0].get_text_content()

    assert "<buyer-inventory>" in hint
    assert "猫砂盆" in hint and "开放式" in hint
    assert "不得推断相近物品也已拥有" in hint


async def test_without_consent_inventory_is_not_read(tmp_path):
    class ForbiddenInventoryRead:
        def list_inventory(self, buyer_id):
            raise AssertionError("未授权时不能读取长期库存")

    orchestrator = _orchestrator(EmptyPreferenceStore(), ForbiddenInventoryRead())
    intent = SubmitIntentInput(
        shopping_session_id="guest-session", buyer_id="guest-guest-session",
        locale="zh-CN", currency="CNY", raw_query="我准备养猫", memory_consent=False,
    )

    messages = await orchestrator._build_inputs(intent, intent.shopping_session_id)

    assert len(messages) == 1
    assert "<buyer-inventory>" not in messages[0].get_text_content()
