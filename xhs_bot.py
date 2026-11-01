"""Publish image notes to the Xiaohongshu (RED) creator center with Playwright.

Usage:
    python xhs_bot.py --mode login
    python xhs_bot.py --mode publish_from_file --file xhs_task.json [--headless] [--cleanup]

Exit code is 0 only when every task was published successfully, so callers
such as n8n's Execute Command node can detect failures.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_FILE = SCRIPT_DIR / "xhs_state.json"
SCREENSHOT_DIR = SCRIPT_DIR / "downloads"
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish"

LOGIN_TIMEOUT_MS = 60_000
UPLOAD_TIMEOUT_MS = 30_000
PUBLISH_TIMEOUT_MS = 15_000
TAG_SUGGESTION_WAIT_MS = 2_000


class PublishError(Exception):
    """Raised when a single note fails to publish."""


def log(level: str, message: str) -> None:
    # Errors go to stderr so n8n's Execute Command node includes them in its error message.
    stream = sys.stderr if level in ("ERROR", "WARN") else sys.stdout
    print(f"[{level}] {message}", file=stream, flush=True)


def login(state_file: Path) -> int:
    """Open a visible browser, wait for a QR-code login and save the session."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        log("INFO", f"Please scan the QR code within {LOGIN_TIMEOUT_MS // 1000} seconds...")
        page.goto(PUBLISH_URL)
        try:
            page.wait_for_selector('text="发布笔记"', timeout=LOGIN_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            log("ERROR", "Login timed out.")
            return 1
        context.storage_state(path=str(state_file))
        browser.close()
    log("SUCCESS", f"Login successful. Session saved to {state_file}")
    return 0


def load_tasks(path: Path) -> list[dict[str, Any]]:
    """Read a task file containing one task object or a list of them."""
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    tasks = data if isinstance(data, list) else [data]
    if not tasks:
        raise ValueError("Task file is empty.")

    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"Task #{i} is not a JSON object.")
        for field in ("title", "image_path"):
            if not task.get(field):
                raise ValueError(f"Task #{i} is missing required field '{field}'.")
    return tasks


def parse_tags(tags: str | list[str] | None) -> list[str]:
    """Accept '#a #b', 'a b' or ['a', '#b'] and return ['a', 'b']."""
    if not tags:
        return []
    if isinstance(tags, list):
        tags = " ".join(tags)
    return [t for t in tags.replace("#", " ").split() if t]


def resolve_image_path(task: dict[str, Any]) -> Path:
    """Relative image paths resolve from the project root."""
    path = Path(task["image_path"])
    return path if path.is_absolute() else SCRIPT_DIR / path


def save_screenshot(page: Page) -> None:
    try:
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        path = SCREENSHOT_DIR / f"error-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.png"
        page.screenshot(path=str(path), full_page=True)
        log("INFO", f"Screenshot saved to {path}")
    except PlaywrightError as e:
        log("WARN", f"Could not save screenshot: {e}")


def publish_one(page: Page, task: dict[str, Any]) -> None:
    title = task["title"]
    content = task.get("content", "")
    image_path = resolve_image_path(task)
    tags = parse_tags(task.get("tags"))

    if not image_path.is_file():
        raise PublishError(f"Image file not found: {image_path}")

    log("INFO", "Navigating to creator center...")
    page.goto(PUBLISH_URL)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2_000)

    if "login" in page.url:
        raise PublishError("Session expired. Run `--mode login` again.")

    log("INFO", "Switching to image/text tab...")
    try:
        page.locator("div").filter(has_text="上传图文").last.click(timeout=5_000)
        page.wait_for_timeout(1_000)
    except PlaywrightError as e:
        log("WARN", f"Tab switch failed (maybe already on the tab): {e}")

    log("INFO", f"Uploading image: {image_path}")
    page.locator("input[type='file']").first.set_input_files(str(image_path))
    title_input = page.locator("input[placeholder*='填写标题']")
    try:
        title_input.wait_for(timeout=UPLOAD_TIMEOUT_MS)
    except PlaywrightTimeoutError as e:
        raise PublishError("Editor did not load after image upload.") from e

    log("INFO", "Filling title...")
    title_input.fill(title)

    log("INFO", "Filling content...")
    editor = page.locator("#post-textarea")
    if editor.count() == 0:
        editor = page.locator(".c-input_textarea")
    if editor.count() == 0:
        editor = page.locator("[contenteditable='true']").first
    editor.click()
    editor.fill(content)
    # Move the caret to the end so tags are appended after the body text.
    page.wait_for_timeout(500)
    editor.press("Control+End")
    editor.press("Enter")
    editor.press("Enter")

    for tag in tags:
        log("INFO", f"Adding tag: #{tag}")
        editor.type(f"#{tag}", delay=100)
        page.wait_for_timeout(TAG_SUGGESTION_WAIT_MS)
        # Pick the first topic suggestion.
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        editor.press("Control+End")
        editor.type(" ")

    log("INFO", "Clicking publish button...")
    page.locator("button").filter(has_text="发布").last.click()
    try:
        page.wait_for_selector("text=发布成功", timeout=PUBLISH_TIMEOUT_MS)
    except PlaywrightTimeoutError as e:
        raise PublishError(
            "Success message not detected. Check the creator center before retrying "
            "to avoid a duplicate post."
        ) from e

    log("SUCCESS", f"Published: {title}")
    # Give the page a moment to finish any post-publish requests.
    page.wait_for_timeout(3_000)


def publish_all(tasks: list[dict[str, Any]], state_file: Path, headless: bool) -> int:
    if not state_file.is_file():
        log("ERROR", f"Session file not found: {state_file}. Run `--mode login` first.")
        return 1

    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(state_file))
        for i, task in enumerate(tasks, start=1):
            log("INFO", f"Task {i}/{len(tasks)}: {task['title']}")
            page = context.new_page()
            try:
                publish_one(page, task)
            except (PublishError, PlaywrightError) as e:
                failures += 1
                log("ERROR", f"Task {i} failed: {e}")
                save_screenshot(page)
            finally:
                page.close()
        browser.close()

    if failures:
        log("ERROR", f"{failures}/{len(tasks)} task(s) failed.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Xiaohongshu note publisher")
    parser.add_argument("--mode", required=True, choices=["login", "publish_from_file"])
    parser.add_argument("--file", type=Path, help="Task JSON file (publish_from_file mode)")
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"Login session file (default: {DEFAULT_STATE_FILE.name} next to this script)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser without a window when publishing",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete the task file and its images once every task is published",
    )
    args = parser.parse_args(argv)

    if args.mode == "login":
        return login(args.state)

    if not args.file or not args.file.is_file():
        log("ERROR", f"Task file not found: {args.file}")
        return 1
    try:
        tasks = load_tasks(args.file)
    except (OSError, ValueError) as e:
        log("ERROR", f"Invalid task file: {e}")
        return 1
    code = publish_all(tasks, args.state, args.headless)
    if code == 0 and args.cleanup:
        for path in [args.file, *(resolve_image_path(t) for t in tasks)]:
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                # Already published: failing here would get the note posted again.
                log("WARN", f"Could not delete {path}: {e}")
    return code


if __name__ == "__main__":
    sys.exit(main())
