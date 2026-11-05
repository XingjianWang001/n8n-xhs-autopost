import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

import xhs_bot  # noqa: E402

# Stand-in for the publish page: clicking 发布 shows RESULT, e.g. the success toast or a rejection.
PAGE = """<!doctype html><meta charset="utf-8">
<div>上传图文</div>
<input type="file" onchange="document.getElementById('editor').hidden = false">
<div id="editor" hidden>
  <input placeholder="填写标题会有更多赞哦">
  <div id="post-textarea" contenteditable="true"></div>
  <button onclick="document.getElementById('result').textContent = RESULT">发布</button>
  <div id="result"></div>
</div>"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except PlaywrightError:
            pytest.skip("needs Chromium (run `playwright install chromium`)")
        yield browser
        browser.close()


@pytest.fixture
def publish(browser, tmp_path, monkeypatch):
    monkeypatch.setattr(xhs_bot, "PUBLISH_TIMEOUT_MS", 1_000)
    monkeypatch.setattr(xhs_bot, "TAG_SUGGESTION_WAIT_MS", 0)

    def run(content, result, image=b"\x89PNG"):
        (tmp_path / "image.png").write_bytes(image)
        page = browser.new_page()
        body = PAGE.replace("= RESULT", f"= '{result}'")
        page.route(xhs_bot.PUBLISH_URL, lambda r: r.fulfill(content_type="text/html", body=body))
        try:
            task = {"title": "t", "content": content, "image_path": str(tmp_path / "image.png")}
            xhs_bot.publish_one(page, task)
        finally:
            page.close()

    return run


def test_success_toast_counts_as_published(publish):
    publish("今天学会了一个小技巧", "发布成功")


@pytest.mark.parametrize("content", ["今天学会了一个小技巧", "第一次笔记发布成功的那天，我很开心"])
def test_rejected_post_is_a_failure_even_if_the_note_says_published(publish, content):
    with pytest.raises(xhs_bot.PublishError):
        publish(content, "标题超出字数")


def test_empty_image_is_rejected_before_upload(publish):
    with pytest.raises(xhs_bot.PublishError, match="empty"):
        publish("今天学会了一个小技巧", "发布成功", image=b"")
