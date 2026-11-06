import json
from pathlib import Path

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
        if NODES[name]["type"] not in ROUTERS:
            assert NODES[name].get("onError") == "continueErrorOutput", name
            assert outputs(name)[1] == ["Mark Failed"], name


# Exit code 0 is no proof of a post: n8n reports a command killed by a signal as a success.
def test_only_the_bots_success_report_marks_the_row_done():
    assert inputs("Mark Done") == [("Published?", 0)]
    assert inputs("Published?") == [("Publish", 0)]
    assert outputs("Published?")[1] == ["Mark Failed"]
    (condition,) = NODES["Published?"]["parameters"]["conditions"]["conditions"]
    assert condition["leftValue"] == "={{ $json.stdout }}"
    assert condition["operator"] == {"type": "string", "operation": "contains"}
    assert condition["rightValue"] == SUCCESS_REPORT
