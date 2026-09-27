from types import SimpleNamespace

from tools.policy_corpus import _apify_dataset_id


def test_apify_dataset_id_supports_run_objects():
    run = SimpleNamespace(default_dataset_id="dataset-from-object")

    assert _apify_dataset_id(run) == "dataset-from-object"


def test_apify_dataset_id_supports_mapping_results():
    run = {"defaultDatasetId": "dataset-from-mapping"}

    assert _apify_dataset_id(run) == "dataset-from-mapping"
