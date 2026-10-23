import json
from pathlib import Path

from app.infrastructure.persistence.seed_products import build_seed_products


DATASET = Path(__file__).resolve().parents[1] / "eval" / "shopmind_core_recall_20261022.jsonl"


def test_shopmind_core_recall_dataset_has_balanced_scenes_and_query_types():
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(cases) == 14
    assert {case["scene"] for case in cases} == {"beach-weekend", "first-rental"}
    assert {case["kind"] for case in cases} == {"lexical", "semantic"}
    assert sum(case["kind"] == "lexical" for case in cases) == 7
    assert sum(case["kind"] == "semantic" for case in cases) == 7
    assert len({case["slot"] for case in cases}) == 7
    assert len({(case["scene"], case["query"]) for case in cases}) == len(cases)
    assert all(case["shopping_task"] and case["relevant"] for case in cases)


def test_shopmind_core_recall_labels_reference_current_catalog_ids():
    product_ids = {product.product_id for product in build_seed_products()}
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert all(set(case["relevant"]) <= product_ids for case in cases)
    assert all(len(case["relevant"]) == len(set(case["relevant"])) for case in cases)
