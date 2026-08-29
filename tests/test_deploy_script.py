from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy.sh"


def _fake_docker(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    stdin_capture = tmp_path / "prompt.capture"
    fake = bin_dir / "docker"
    fake.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
if [ "${1:-}" = compose ] && [ "${2:-}" = version ]; then
  exit 0
fi
if [ "${1:-}" = compose ] && [ "${2:-}" = ps ] && [ "${3:-}" = -q ]; then
  if [ "${FAKE_SERVICES_HEALTHY:-0}" = "1" ]; then
    printf '%s-container\\n' "$4"
  fi
  exit 0
fi
if [ "${1:-}" = compose ] && [ "${2:-}" = images ] && [ "${3:-}" = -q ]; then
  if [ "${FAKE_IMAGES_PRESENT:-1}" = "1" ]; then
    printf '%s-image\\n' "$4"
  fi
  exit 0
fi
if [ "${1:-}" = inspect ]; then
  printf 'healthy\\n'
  exit 0
fi
if [ "${1:-}" = compose ] && [ "${2:-}" = port ]; then
  printf '0.0.0.0:5174\\n'
  exit 0
fi
if [ "${1:-}" = compose ] && [ "${2:-}" = exec ]; then
  case "$*" in
    *'current_phase'*)
      if [ "${FAKE_RETRY_NO_CHAPTER:-0}" = "1" ]; then
        printf 'ending.review\\t\\n'
      else
        printf 'chapter.validate\\t1\\n'
      fi
      exit 0
      ;;
  esac
  cat > "$FAKE_PROMPT_CAPTURE"
fi
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bin_dir, log, stdin_capture


def _run_deploy(tmp_path: Path, *args: str, input_text: str = "") -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    bin_dir, log, capture = _fake_docker(tmp_path)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# A prompt from the author\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)
    env["NOVEL_OS_DATA_DIR"] = str(tmp_path / "data")
    env["NOVEL_OS_NONINTERACTIVE"] = "1"
    env["NOVEL_OS_APPROVAL"] = "auto"
    env["NOVEL_OS_OUTPUT"] = "markdown epub"
    result = subprocess.run(
        [str(SCRIPT), *args, str(prompt)],
        cwd=ROOT,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, log, capture


def test_novel_forwards_prompt_to_backend_with_container_project_path(tmp_path: Path):
    result, log, capture = _run_deploy(tmp_path, "novel")

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "compose up --detach --no-recreate --wait --wait-timeout 180 backend" in calls
    assert "compose up --detach --build" not in calls
    assert "compose exec -T backend novel-os-entrypoint python core/orchestrator.py run" in calls
    assert "--project /data/projects/prompt" in calls
    assert "--prompt -" in calls
    assert "--approval auto" in calls
    assert "--output markdown epub" in calls
    assert capture.read_text(encoding="utf-8") == "# A prompt from the author\n"


def test_novel_status_uses_latest_persisted_run_without_manual_run_id(tmp_path: Path):
    data_dir = tmp_path / "data"
    projects = data_dir / "projects" / "status-smoke" / "outputs" / "runs" / "RUN123"
    projects.mkdir(parents=True, exist_ok=True)
    (projects / "run.json").write_text("{}\n", encoding="utf-8")
    bin_dir, log, capture = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)
    env["NOVEL_OS_DATA_DIR"] = str(data_dir)
    result = subprocess.run(
        [str(SCRIPT), "novel-status"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "compose up --detach --no-recreate --wait --wait-timeout 180 backend" in calls
    assert "compose up --detach --build" not in calls
    assert "run-status --project /data/projects/status-smoke --run-id RUN123" in calls


def test_novel_status_resolves_run_id_without_project(tmp_path: Path):
    data_dir = tmp_path / "data"
    run = data_dir / "projects" / "status-by-id" / "outputs" / "runs" / "RUN123"
    run.mkdir(parents=True, exist_ok=True)
    (run / "run.json").write_text("{}\n", encoding="utf-8")
    bin_dir, log, capture = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)
    env["NOVEL_OS_DATA_DIR"] = str(data_dir)

    result = subprocess.run(
        [str(SCRIPT), "novel-status", "RUN123"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "run-status --project /data/projects/status-by-id --run-id RUN123" in calls


def test_novel_status_rejects_duplicate_run_id_without_project(tmp_path: Path):
    data_dir = tmp_path / "data"
    for project in ("duplicate-a", "duplicate-b"):
        run = data_dir / "projects" / project / "outputs" / "runs" / "RUN999"
        run.mkdir(parents=True, exist_ok=True)
        (run / "run.json").write_text("{}\n", encoding="utf-8")
    bin_dir, log, capture = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)
    env["NOVEL_OS_DATA_DIR"] = str(data_dir)

    result = subprocess.run(
        [str(SCRIPT), "novel-status", "RUN999"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Run ID is ambiguous across projects: RUN999" in result.stderr
    assert "compose exec" not in log.read_text(encoding="utf-8")


def test_novel_resume_uses_latest_run_and_optional_approval(tmp_path: Path):
    data_dir = tmp_path / "data"
    run = data_dir / "projects" / "resume-smoke" / "outputs" / "runs" / "RUN456"
    run.mkdir(parents=True)
    (run / "run.json").write_text("{}\n", encoding="utf-8")
    bin_dir, log, capture = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)
    env["NOVEL_OS_DATA_DIR"] = str(data_dir)
    env["NOVEL_OS_APPROVAL"] = "auto"

    result = subprocess.run(
        [str(SCRIPT), "novel-resume"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "resume --project /data/projects/resume-smoke --run-id RUN456 --approval auto" in calls


def test_novel_retry_reads_current_stage_from_manifest(tmp_path: Path):
    data_dir = tmp_path / "data"
    run = data_dir / "projects" / "retry-smoke" / "outputs" / "runs" / "RUN789"
    run.mkdir(parents=True)
    (run / "run.json").write_text(
        '{"current_phase":"chapter.validate","current_chapter":1}\n',
        encoding="utf-8",
    )
    bin_dir, log, capture = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)
    env["NOVEL_OS_DATA_DIR"] = str(data_dir)

    result = subprocess.run(
        [str(SCRIPT), "novel-retry"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "retry --project /data/projects/retry-smoke --run-id RUN789 --phase chapter.validate --chapter 1" in calls


def test_novel_retry_omits_empty_current_chapter(tmp_path: Path):
    data_dir = tmp_path / "data"
    run = data_dir / "projects" / "retry-ending" / "outputs" / "runs" / "RUN790"
    run.mkdir(parents=True)
    (run / "run.json").write_text(
        '{"current_phase":"ending.review","current_chapter":null}\n',
        encoding="utf-8",
    )
    bin_dir, log, capture = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)
    env["FAKE_RETRY_NO_CHAPTER"] = "1"
    env["NOVEL_OS_DATA_DIR"] = str(data_dir)

    result = subprocess.run(
        [str(SCRIPT), "novel-retry"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "retry --project /data/projects/retry-ending --run-id RUN790 --phase ending.review" in calls
    assert "--phase ending.review --chapter" not in calls


def test_novel_reuses_healthy_services_without_compose_up(tmp_path: Path):
    bin_dir, log, capture = _fake_docker(tmp_path)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Healthy services\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)
    env["FAKE_SERVICES_HEALTHY"] = "1"
    env["NOVEL_OS_NONINTERACTIVE"] = "1"
    env["NOVEL_OS_PROJECT_NAME"] = "healthy-services"
    env["NOVEL_OS_DRY_RUN"] = "1"
    env["NOVEL_OS_OUTPUT"] = "markdown"

    result = subprocess.run(
        [str(SCRIPT), "novel", str(prompt)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "compose up" not in calls
    assert "compose exec -T backend novel-os-entrypoint python core/orchestrator.py run" in calls


def test_novel_builds_only_when_images_are_missing(tmp_path: Path):
    bin_dir, log, capture = _fake_docker(tmp_path)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# First launch\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)
    env["FAKE_IMAGES_PRESENT"] = "0"
    env["NOVEL_OS_NONINTERACTIVE"] = "1"
    env["NOVEL_OS_PROJECT_NAME"] = "first-launch"
    env["NOVEL_OS_DRY_RUN"] = "1"
    env["NOVEL_OS_OUTPUT"] = "markdown"

    result = subprocess.run(
        [str(SCRIPT), "novel", str(prompt)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "compose up --detach --no-recreate --build --wait --wait-timeout 180 backend" in calls


def test_novel_help_lists_all_commands_without_starting_services(tmp_path: Path):
    bin_dir, log, capture = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_PROMPT_CAPTURE"] = str(capture)

    result = subprocess.run(
        [str(SCRIPT), "novel", "--help"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Novel OS novel commands" in result.stdout
    for command in ("novel", "novel-status", "novel-resume", "novel-retry", "up", "down", "restart", "logs", "status", "config"):
        assert command in result.stdout
    for description in ("interactive", "most recent", "Resume", "Retry", "persistent", "health"):
        assert description.lower() in result.stdout.lower()
    assert "Prompt file not found" not in result.stderr
    assert not log.exists() or "compose up" not in log.read_text(encoding="utf-8")
