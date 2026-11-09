import json
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = json.loads(
    (Path(__file__).parent.parent / "workflows" / "xhs-auto-post.json").read_text(encoding="utf-8")
)
NODES = {node["name"]: node for node in WORKFLOW["nodes"]}
ROUTERS = {"n8n-nodes-base.switch", "n8n-nodes-base.if"}  # they only pick a branch
SUCCESS_REPORT = "[SUCCESS] Published:"  # what xhs_bot.py prints once the success toast shows


def outputs(name):
    branches = WORKFLOW["connections"].get(name, {}).get("main", [])
    return [[c["node"] for c in branch] for branch in branches]


def inputs(name):
    return [(src, i) for src in NODES for i, branch in enumerate(outputs(src)) if name in branch]


def steps():
    """Every node a run passes through after Mark Generating, before Mark Done or Mark Failed."""
    found, todo = [], list(outputs("Mark Generating")[0])
    while todo:
        name = todo.pop()
        if name in found or name in ("Mark Done", "Mark Failed"):
            continue
        found.append(name)
        branches = outputs(name) if NODES[name]["type"] in ROUTERS else outputs(name)[:1]
        todo += [n for branch in branches for n in branch]
    return found


def test_every_step_that_can_fail_marks_the_row_failed():
    for name in steps():
        assert not NODES[name].get("disabled"), name  # a disabled node passes its input straight on
        if NODES[name]["type"] not in ROUTERS:
            assert NODES[name].get("onError") == "continueErrorOutput", name
            assert outputs(name)[1] == ["Mark Failed"], name


# Exit code 0 is no proof of a post: n8n reports a command killed by a signal as a success.
def test_only_the_bots_success_report_marks_the_row_done():
    assert "Published?" in steps()  # reached through every step's success output
    assert inputs("Mark Done") == [("Published?", 0)]
    assert inputs("Published?") == [("Publish", 0)]
    assert outputs("Published?")[1] == ["Mark Failed"]
    (condition,) = NODES["Published?"]["parameters"]["conditions"]["conditions"]
    assert condition["leftValue"] == "={{ $json.stdout }}"
    assert condition["operator"] == {"type": "string", "operation": "contains"}
    assert condition["rightValue"] == SUCCESS_REPORT


def notion_error(name):
    """What a Notion node writes to the row's Error field (None if it leaves Error alone)."""
    values = NODES[name]["parameters"]["propertiesUi"]["propertyValues"]
    return next((p["textContent"] for p in values if p["key"] == "Error|rich_text"), None)


# Stop, an execution timeout or a killed n8n ends the run inside Publish, before Mark Done or
# Mark Failed: the row keeps what Mark Generating wrote, though the note may already be live.
def test_a_row_left_in_generating_says_to_check_the_creator_center():
    assert "check the creator center" in notion_error("Mark Generating")
    assert notion_error("Mark Done") == ""  # a published row must not keep that warning


def mark_failed_error(item, publish_ran):
    """The Error Mark Failed writes for an incoming item, evaluated by node as n8n would."""
    expression = notion_error("Mark Failed").removeprefix("={{").removesuffix("}}")
    node_ref = f"() => ({{isExecuted: {json.dumps(publish_ran)}}})"  # $('Publish').isExecuted
    script = f"const $json = {json.dumps(item)}, $ = {node_ref}; process.stdout.write({expression})"
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout


# A retry of a post that went out is a duplicate (#1, #5). Killing only python after
# [SUCCESS] makes sh exit 137/143, which n8n routes to the error output without stdout.
@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize(
    "item",
    [
        {"error": "[WARN] Tab switch failed\nKilled"},  # Publish error output: stderr only
        {"error": "Command failed with exit code 143"},
        {"exitCode": 0, "stdout": "", "stderr": ""},  # Published? false: n8n says exit 0
    ],
    ids=["sigkill", "sigterm", "no success report"],
)
def test_every_failure_after_publish_says_to_check_the_creator_center(item):
    assert "check the creator center" in mark_failed_error(item, publish_ran=True)


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_a_failure_before_publish_gives_its_own_reason():
    error = mark_failed_error({"error": {"message": "quota exceeded"}}, publish_ran=False)
    assert error == "quota exceeded"


def parse_copy(model_output):
    """Run the Parse Copy node's code on a Gemini reply, as n8n would."""
    cfg = {"IMAGE_STYLE": "flat", "IMAGE_PROVIDER": "gemini"}
    reply = {"candidates": [{"content": {"parts": [{"text": json.dumps(model_output)}]}}]}
    stubs = f"const $ = () => ({{first: () => ({{json: {json.dumps(cfg)}}})}}), "
    stubs += f"$input = {{first: () => ({{json: {json.dumps(reply)}}})}};"
    code = NODES["Parse Copy"]["parameters"]["jsCode"]
    script = f"{stubs} process.stdout.write(JSON.stringify((() => {{{code}}})()))"
    return subprocess.run(["node", "-e", script], capture_output=True, text=True)


# Xiaohongshu rejects a title over 20 characters only after the image is made and 发布 is clicked.
@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_a_title_over_20_characters_fails_before_publishing():
    copy = {"title": "打工人必看！3个让下班后效率翻倍的小习惯✨", "content": "正文"}
    assert "Title is 21 characters, Xiaohongshu allows at most 20" in parse_copy(copy).stderr
    copy["title"] = "通勤路上的 3 个小习惯 ☕"  # examples/task.example.json
    assert json.loads(parse_copy(copy).stdout)["json"]["title"] == copy["title"]
