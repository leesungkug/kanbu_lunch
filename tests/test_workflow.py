from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Final

import pytest
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

WORKFLOW: Final = (
    Path(__file__).resolve().parents[1] / ".github/workflows/instagram-slack-notifier.yml"
)


def mapping_value(node: Node, key: str) -> Node:
    assert isinstance(node, MappingNode)
    for field, value in node.value:
        assert isinstance(field, ScalarNode)
        if field.value == key:
            return value
    pytest.fail(f"Missing workflow field: {key}")


def scalar_value(node: Node) -> str:
    assert isinstance(node, ScalarNode)
    return node.value


def workflow_node() -> Node:
    node = yaml.compose(WORKFLOW.read_text(encoding="utf-8"))
    assert node is not None
    return node


def step_script(name: str) -> str:
    job = mapping_value(mapping_value(workflow_node(), "jobs"), "notify")
    steps = mapping_value(job, "steps")
    assert isinstance(steps, SequenceNode)
    for step in steps.value:
        if scalar_value(mapping_value(step, "name")) == name:
            return scalar_value(mapping_value(step, "run"))
    pytest.fail(f"Missing workflow step: {name}")


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def git_repositories(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote, runner, author = (tmp_path / name for name in ("remote", "runner", "author"))
    remote.mkdir()
    git(remote, "init", "--bare", "--initial-branch=main")
    git(tmp_path, "clone", str(remote), str(runner))
    git(runner, "config", "user.name", "Workflow test")
    git(runner, "config", "user.email", "workflow@example.invalid")
    (runner / ".instagram_state.json").write_text('{"post": "old"}\n', encoding="utf-8")
    git(runner, "add", ".instagram_state.json")
    git(runner, "commit", "-m", "Initial state")
    git(runner, "push", "origin", "main")
    git(tmp_path, "clone", str(remote), str(author))
    git(author, "config", "user.name", "Workflow test")
    git(author, "config", "user.email", "workflow@example.invalid")
    return remote, runner, author


def run_state_step(runner: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-e",
            "-o",
            "pipefail",
            "-c",
            step_script("Commit updated state"),
        ],
        cwd=runner,
        env=os.environ | {"TARGET_BRANCH": "main"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_all_notifier_events_serialize_before_checkout() -> None:
    # Given the complete workflow, including manual dispatches from other refs.
    workflow = workflow_node()
    # When GitHub resolves its workflow-level concurrency policy.
    concurrency = mapping_value(workflow, "concurrency")
    # Then every event uses the main-state lock without cancelling a sender.
    assert scalar_value(mapping_value(concurrency, "group")) == "instagram-slack-notifier-main"
    assert scalar_value(mapping_value(concurrency, "cancel-in-progress")) == "false"


@pytest.mark.parametrize("source", ["browser", "apify"])
def test_config_keeps_secret_data_literal(tmp_path: Path, source: str) -> None:
    # Given a secret containing JSON-sensitive characters and shell syntax.
    marker = tmp_path / "unexpected-shell-execution"
    secret = f'https://example.invalid/"\\\n$(touch {marker})'
    script = step_script("Create runtime config")
    script = script.replace("${{ secrets.SLACK_WEBHOOK_URL }}", secret)
    script = script.replace("${{ github.workspace }}", str(tmp_path))
    # When the actual workflow config step runs with a fake secret.
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env=os.environ
        | {
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_WORKSPACE": str(tmp_path),
            "SLACK_WEBHOOK_URL": secret,
            "INSTAGRAM_SOURCE": source,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    # Then it neither executes secret text nor corrupts the generated JSON.
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert config["slack_webhook_url"] == secret
    assert config["instagram_source"] == source
    assert config["state_file"] == str(tmp_path / ".instagram_state.json")


def test_state_push_recovers_when_main_gets_an_unrelated_commit(
    git_repositories: tuple[Path, Path, Path],
) -> None:
    # Given a sender checkout behind an unrelated main-branch commit.
    remote, runner, author = git_repositories
    (author / "notes.txt").write_text("Independent change\n", encoding="utf-8")
    git(author, "add", "notes.txt")
    git(author, "commit", "-m", "Unrelated change")
    git(author, "push", "origin", "main")
    (runner / ".instagram_state.json").write_text('{"post": "sent"}\n', encoding="utf-8")
    # When the workflow persists the sent-post state.
    result = run_state_step(runner)
    # Then both independent changes are retained remotely.
    assert result.returncode == 0, result.stderr
    assert git(remote, "show", "main:notes.txt") == "Independent change"
    assert git(remote, "show", "main:.instagram_state.json") == '{"post": "sent"}'


def test_state_push_refuses_to_overwrite_a_concurrent_state_change(
    git_repositories: tuple[Path, Path, Path],
) -> None:
    # Given another writer has changed the same persisted state.
    remote, runner, author = git_repositories
    (author / ".instagram_state.json").write_text('{"post": "other"}\n', encoding="utf-8")
    git(author, "add", ".instagram_state.json")
    git(author, "commit", "-m", "Concurrent state update")
    git(author, "push", "origin", "main")
    (runner / ".instagram_state.json").write_text('{"post": "sent"}\n', encoding="utf-8")
    # When this workflow tries to persist its state.
    result = run_state_step(runner)
    # Then it fails visibly and preserves the remote writer's state.
    assert result.returncode != 0
    assert git(remote, "show", "main:.instagram_state.json") == '{"post": "other"}'


def test_no_state_change_does_not_create_a_commit(
    git_repositories: tuple[Path, Path, Path],
) -> None:
    # Given the notifier left its state unchanged.
    remote, runner, _author = git_repositories
    original_head = git(remote, "rev-parse", "main")
    # When the state-persistence step runs.
    result = run_state_step(runner)
    # Then the workflow succeeds without a new commit.
    assert result.returncode == 0, result.stderr
    assert git(remote, "rev-parse", "main") == original_head


def test_manual_dispatch_defaults_to_dry_run() -> None:
    # Given a manual run with its default inputs.
    dispatch = mapping_value(mapping_value(workflow_node(), "on"), "workflow_dispatch")
    # When the dry-run default is resolved.
    dry_run = mapping_value(mapping_value(dispatch, "inputs"), "dry_run")
    # Then inspecting the workflow cannot send Slack by default.
    assert scalar_value(mapping_value(dry_run, "default")) == "true"


@pytest.mark.parametrize(
    ("event", "dry_run", "force_notify", "expected_flag"),
    [
        ("workflow_dispatch", "true", "true", "--dry-run"),
        ("workflow_dispatch", "true", "false", "--dry-run"),
        ("workflow_dispatch", "false", "true", "--force-notify"),
        ("workflow_dispatch", "false", "false", None),
        ("schedule", "true", "true", None),
        ("repository_dispatch", "true", "true", None),
    ],
)
def test_notifier_arguments_respect_manual_dry_run_precedence(
    tmp_path: Path,
    event: str,
    dry_run: str,
    force_notify: str,
    expected_flag: str | None,
) -> None:
    # Given a command recorder instead of a notifier or a browser installation.
    record = tmp_path / "arguments.txt"
    for executable in ("python", "uv"):
        stub = tmp_path / executable
        stub.write_text('#!/bin/bash\nprintf \'%s\\n\' "$@" > "$ARGV_RECORD"\n', encoding="utf-8")
        stub.chmod(0o700)
    # When the actual workflow run step handles the trigger inputs.
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step_script("Run notifier")],
        cwd=tmp_path,
        env=os.environ
        | {
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "ARGV_RECORD": str(record),
            "EVENT_NAME": event,
            "DRY_RUN": dry_run,
            "FORCE_NOTIFY": force_notify,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    # Then dry-run wins over force, and automatic events retain normal behavior.
    expected = [
        "run",
        "--locked",
        "--no-dev",
        "python",
        "instagram_slack_notifier.py",
        "--config",
        str(tmp_path / "config.json"),
    ]
    if expected_flag is not None:
        expected.append(expected_flag)
    assert result.returncode == 0, result.stderr
    assert record.read_text(encoding="utf-8").splitlines() == expected
