import pytest

pytest.importorskip("playwright")
import xhs_bot  # noqa: E402


def test_parse_tags_splits_on_hash_space_and_commas():
    assert xhs_bot.parse_tags(["#通勤,#效率"]) == ["通勤", "效率"]
    assert xhs_bot.parse_tags("通勤，效率、生活") == ["通勤", "效率", "生活"]
    assert xhs_bot.parse_tags("#a #b") == ["a", "b"]
    assert xhs_bot.parse_tags(["a", "#b"]) == ["a", "b"]
    assert xhs_bot.parse_tags(None) == []
    assert xhs_bot.parse_tags("") == []
