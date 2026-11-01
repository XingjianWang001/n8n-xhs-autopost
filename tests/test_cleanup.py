import pytest

pytest.importorskip("playwright")
import xhs_bot  # noqa: E402


def test_cleanup_only_touches_files_the_workflow_wrote(tmp_path):
    downloads = xhs_bot.SCREENSHOT_DIR
    tasks = [
        {"image_path": str(downloads / "image-42.png")},
        {"image_path": "downloads/image-43.jpg"},
        {"image_path": str(tmp_path / "my-photo.jpg")},
        {"image_path": str(downloads / ".." / "README.md")},
    ]
    paths = xhs_bot.cleanup_paths(tmp_path / "xhs_task_42.json", tasks)
    assert paths == [
        tmp_path / "xhs_task_42.json",
        (downloads / "image-42.png").resolve(),
        (downloads / "image-43.jpg").resolve(),
    ]
    assert xhs_bot.cleanup_paths(tmp_path / "task.example.json", []) == []
