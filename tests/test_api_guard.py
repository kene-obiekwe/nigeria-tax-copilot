"""D-108: tests marked `api` run only when TAXY_RUN_API_TESTS=1, even with keys set or .env loaded."""
import pytest

import conftest


class FakeItem:
    def __init__(self, api):
        self.api, self.added = api, []

    def get_closest_marker(self, name):
        return object() if name == "api" and self.api else None

    def add_marker(self, marker):
        self.added.append(marker)


def test_only_the_exact_flag_enables_api_tests():
    assert conftest.api_tests_enabled({"TAXY_RUN_API_TESTS": "1"})
    for env in ({}, {"TAXY_RUN_API_TESTS": "0"}, {"TAXY_RUN_API_TESTS": "true"},
                {"LLM_API_KEY": "set", "EMBEDDING_API_KEY": "set"}):
        assert not conftest.api_tests_enabled(env), env


def test_api_items_are_skipped_without_the_flag(monkeypatch):
    monkeypatch.delenv("TAXY_RUN_API_TESTS", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "not-a-real-key")  # a key alone never enables them
    api, plain = FakeItem(True), FakeItem(False)
    conftest.pytest_collection_modifyitems(None, [api, plain])
    assert plain.added == [] and len(api.added) == 1
    assert api.added[0].mark.name == "skip" and api.added[0].mark.kwargs["reason"] == conftest.API_SKIP_REASON


def test_api_items_run_with_the_flag(monkeypatch):
    monkeypatch.setenv("TAXY_RUN_API_TESTS", "1")
    api = FakeItem(True)
    conftest.pytest_collection_modifyitems(None, [api])
    assert api.added == []


@pytest.mark.api
def test_this_api_marked_test_is_skipped_in_a_plain_run():
    """Reaching this body in a plain run would mean the guard failed."""
    assert conftest.api_tests_enabled(), "an api test ran without TAXY_RUN_API_TESTS=1"
