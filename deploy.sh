#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
DATA_DIR="${NOVEL_OS_DATA_DIR:-$ROOT_DIR/docker-data}"

usage() {
  cat <<'EOF'
Usage: ./deploy.sh <command>

Commands:
  up       Build and start the frontend and backend
  down     Stop and remove the containers (persistent data is retained)
  restart  Rebuild and restart both services
  build    Build both images without starting containers
  logs     Follow service logs
  status   Show container and health status
  config   Validate and print the resolved Compose configuration
  novel [PROMPT]
           Start the full-book runner with an interactive setup
  novel-status [PROJECT] [RUN_ID]
           Show a run; without arguments, use the most recent run
  novel-resume [PROJECT] [RUN_ID]
           Resume a run; without arguments, use the most recent run
  novel-retry [PROJECT] [RUN_ID]
           Retry the current stage of a failed or blocked run
EOF
}

novel_usage() {
  cat <<'EOF'
Novel OS novel commands

Create a complete novel:
  ./deploy.sh novel
      Interactive mode. Choose a Markdown prompt, project name, inferred or
      explicit length, approval policy, and output formats.

  ./deploy.sh novel PROMPT
      Start directly from a prompt file, for example:
      ./deploy.sh novel './prompt/my-novel.md'

Manage persisted runs:
  ./deploy.sh novel-status [PROJECT] [RUN_ID]
      Show status, current phase, chapter, and error. Without arguments, uses
      the most recently updated run across all persistent projects.

  ./deploy.sh novel-resume [PROJECT] [RUN_ID]
      Resume a paused or interrupted run from its durable checkpoints. Without
      arguments, uses the most recent run.

  ./deploy.sh novel-retry [PROJECT] [RUN_ID]
      Retry the current failed or blocked stage. Without arguments, uses the
      most recent run and reads its phase and chapter automatically.

  ./deploy.sh novel --help
      Show this complete command reference. Also accepts -h or help.

Deployment and diagnostics:
  ./deploy.sh up
      Build and start the frontend and backend; wait for both health checks.

  ./deploy.sh down
      Stop containers. Manuscripts, media, settings, and the database remain
      under the persistent docker-data/ directory.

  ./deploy.sh restart
      Rebuild and restart both services. Run this explicitly after changing
      application code or Compose configuration.

  ./deploy.sh build
      Build both images without starting containers.

  ./deploy.sh logs [SERVICE]
      Follow the latest logs. SERVICE can be backend or frontend.

  ./deploy.sh status
      Show container health, ports, and the Studio URL.

  ./deploy.sh config
      Validate and print the resolved Docker Compose configuration.

Defaults used by novel:
  Project name       Prompt filename without .md
  Chapters           Inferred from the prompt
  Target words       Inferred from the prompt
  Approval           auto
  Quality policy     evidence_v1
  Outputs            markdown epub
  Retries            5 transient retries, 2 quality repair passes

Optional environment overrides:
  NOVEL_OS_PROJECT_NAME       Project directory name
  NOVEL_OS_CHAPTERS           Positive integer chapter count
  NOVEL_OS_WORDS              Positive integer target word count
  NOVEL_OS_APPROVAL           auto or review_required
  NOVEL_OS_QUALITY_POLICY     legacy or evidence_v1
  NOVEL_OS_OUTPUT             Space-separated formats: markdown html docx epub pdf
  NOVEL_OS_MAX_RETRIES        Transient failure retry count
  NOVEL_OS_MAX_QUALITY_REPAIRS
                              Automatic quality repair count
  NOVEL_OS_DRY_RUN=1          Persist intake only; do not call the LLM
  NOVEL_OS_WEB_PORT           Studio host port; default 5174

Examples:
  ./deploy.sh novel
  ./deploy.sh novel './prompt/my-novel.md'
  NOVEL_OS_OUTPUT='epub pdf' ./deploy.sh novel './prompt/my-novel.md'
  NOVEL_OS_APPROVAL=auto ./deploy.sh novel-resume
  ./deploy.sh novel-status my-novel RUN_ID
  ./deploy.sh logs backend

Runtime behavior:
  Novel commands reuse a healthy backend container and do not manage the
  frontend. Existing containers are never recreated by novel commands. Project
  artifacts are stored at docker-data/projects/PROJECT/outputs/.
EOF
}

show_url() {
  local binding port
  binding="$(docker compose port frontend 80 2>/dev/null | tail -n 1 || true)"
  port="${binding##*:}"
  if [[ -n "$binding" && "$port" != "$binding" ]]; then
    echo "Novel OS: http://localhost:${port}"
  else
    echo "Novel OS: http://localhost:${NOVEL_OS_WEB_PORT:-5174}"
  fi
}

ensure_backend() {
  local container_id health
  container_id="$(docker compose ps -q backend 2>/dev/null || true)"
  if [[ -n "$container_id" ]]; then
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
  fi
  if [[ -n "$container_id" && ( "$health" == "healthy" || "$health" == "running" ) ]]; then
    echo "Novel OS backend is healthy; reusing the running container."
    return
  fi

  local -a args=(docker compose up --detach --no-recreate)
  if [[ -z "$(docker compose images -q backend 2>/dev/null || true)" ]]; then
    args+=(--build)
  fi
  args+=(--wait --wait-timeout 180 backend)
  "${args[@]}"
}

is_interactive() {
  [[ -t 0 && "${NOVEL_OS_NONINTERACTIVE:-0}" != "1" ]]
}

choose_prompt() {
  local supplied="${1:-}"
  if [[ -n "$supplied" ]]; then
    if [[ ! -f "$supplied" ]]; then
      echo "Prompt file not found: $supplied" >&2
      return 1
    fi
    printf '%s\n' "$supplied"
    return
  fi

  if ! is_interactive; then
    echo "Pass a prompt file: ./deploy.sh novel ./prompt/your-novel.md" >&2
    return 1
  fi

  local files=() file choice index
  shopt -s nullglob
  files=("$ROOT_DIR"/prompt/*.md)
  shopt -u nullglob
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "No Markdown prompts found under $ROOT_DIR/prompt." >&2
    return 1
  fi

  printf '\nChoose a novel prompt:\n' >&2
  index=1
  for file in "${files[@]}"; do
    printf '  %d) %s\n' "$index" "$(basename "$file")" >&2
    index=$((index + 1))
  done
  while true; do
    printf 'Selection [1-%d]: ' "${#files[@]}" >&2
    IFS= read -r choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && ((choice >= 1 && choice <= ${#files[@]})); then
      printf '%s\n' "${files[choice - 1]}"
      return
    fi
    echo "Enter a number from the list." >&2
  done
}

validate_project_name() {
  local value="$1"
  if [[ -z "$value" || "$value" == "." || "$value" == ".." || "$value" == */* ]]; then
    echo "Project name must be one directory name." >&2
    return 1
  fi
}

run_novel() {
  local prompt_file project_name chapters words approval quality output dry_run answer
  local -a command_args output_formats

  prompt_file="$(choose_prompt "${1:-}")"
  prompt_file="$(cd "$(dirname "$prompt_file")" && pwd)/$(basename "$prompt_file")"
  project_name="${NOVEL_OS_PROJECT_NAME:-$(basename "$prompt_file")}"
  project_name="${project_name%.*}"
  chapters="${NOVEL_OS_CHAPTERS:-}"
  words="${NOVEL_OS_WORDS:-}"
  approval="${NOVEL_OS_APPROVAL:-auto}"
  quality="${NOVEL_OS_QUALITY_POLICY:-evidence_v1}"
  output="${NOVEL_OS_OUTPUT:-markdown epub}"
  dry_run="${NOVEL_OS_DRY_RUN:-0}"

  if is_interactive; then
    printf 'Project name [%s]: ' "$project_name"
    IFS= read -r answer
    project_name="${answer:-$project_name}"
    printf 'Chapter count [infer from prompt]: '
    IFS= read -r answer
    chapters="${answer:-$chapters}"
    printf 'Target words [infer from prompt]: '
    IFS= read -r answer
    words="${answer:-$words}"
    printf 'Approval [auto/review_required] [%s]: ' "$approval"
    IFS= read -r answer
    approval="${answer:-$approval}"
    printf 'Output formats [%s]: ' "$output"
    IFS= read -r answer
    output="${answer:-$output}"
  fi

  validate_project_name "$project_name"
  if [[ "$approval" != "auto" && "$approval" != "review_required" ]]; then
    echo "Approval must be auto or review_required." >&2
    return 1
  fi
  if [[ -n "$chapters" && ! "$chapters" =~ ^[1-9][0-9]*$ ]]; then
    echo "Chapter count must be a positive integer." >&2
    return 1
  fi
  if [[ -n "$words" && ! "$words" =~ ^[1-9][0-9]*$ ]]; then
    echo "Target words must be a positive integer." >&2
    return 1
  fi
  read -r -a output_formats <<< "$output"
  if [[ ${#output_formats[@]} -eq 0 ]]; then
    echo "Choose at least one output format." >&2
    return 1
  fi

  printf '\nNovel run\n'
  printf '  Prompt:  %s\n' "$prompt_file"
  printf '  Project: %s\n' "$project_name"
  printf '  Chapters: %s\n' "${chapters:-infer}"
  printf '  Words:    %s\n' "${words:-infer}"
  printf '  Approval: %s\n' "$approval"
  printf '  Output:   %s\n' "$output"

  if is_interactive; then
    printf 'Start this run? [Y/n]: '
    IFS= read -r answer
    case "${answer:-y}" in
      y|Y|yes|YES) ;;
      *) echo "Cancelled."; return 0 ;;
    esac
  fi

  ensure_backend
  command_args=(
    docker compose exec -T backend novel-os-entrypoint
    python core/orchestrator.py run
    --project "/data/projects/$project_name"
    --prompt -
    --approval "$approval"
    --quality-policy "$quality"
    --max-retries "${NOVEL_OS_MAX_RETRIES:-5}"
    --max-quality-repairs "${NOVEL_OS_MAX_QUALITY_REPAIRS:-2}"
  )
  [[ -n "$chapters" ]] && command_args+=(--chapters "$chapters")
  [[ -n "$words" ]] && command_args+=(--words "$words")
  [[ "$dry_run" == "1" ]] && command_args+=(--dry-run)
  command_args+=(--output "${output_formats[@]}")

  "${command_args[@]}" < "$prompt_file"
  printf '\nProject files: %s/projects/%s/outputs\n' "$DATA_DIR" "$project_name"
}

latest_run_manifest() {
  local requested_project="${1:-}" search_root file latest=""
  search_root="$DATA_DIR/projects"
  if [[ -n "$requested_project" ]]; then
    validate_project_name "$requested_project" || return 1
    search_root="$search_root/$requested_project"
  fi
  [[ -d "$search_root" ]] || return 1
  while IFS= read -r file; do
    if [[ -z "$latest" || "$file" -nt "$latest" ]]; then
      latest="$file"
    fi
  done < <(find "$search_root" -type f -path '*/outputs/runs/*/run.json' 2>/dev/null)
  [[ -n "$latest" ]] || return 1
  printf '%s\n' "$latest"
}

resolve_run_target() {
  local requested_project="${1:-}" requested_run="${2:-}" manifest relative
  if [[ -n "$requested_run" ]]; then
    validate_project_name "$requested_project" || return 1
    manifest="$DATA_DIR/projects/$requested_project/outputs/runs/$requested_run/run.json"
    if [[ ! -f "$manifest" ]]; then
      echo "Run not found: $requested_project / $requested_run" >&2
      return 1
    fi
  else
    if ! manifest="$(latest_run_manifest "$requested_project")"; then
      echo "No persisted novel runs found." >&2
      return 1
    fi
  fi
  relative="${manifest#"$DATA_DIR/projects/"}"
  RUN_PROJECT="${relative%%/outputs/runs/*}"
  RUN_ID="$(basename "$(dirname "$manifest")")"
}

novel_status() {
  resolve_run_target "${1:-}" "${2:-}"
  ensure_backend
  echo "Run: $RUN_PROJECT / $RUN_ID"
  docker compose exec -T backend novel-os-entrypoint \
    python core/orchestrator.py run-status \
    --project "/data/projects/$RUN_PROJECT" --run-id "$RUN_ID"
}

novel_resume() {
  local -a args
  resolve_run_target "${1:-}" "${2:-}"
  ensure_backend
  args=(
    docker compose exec -T backend novel-os-entrypoint
    python core/orchestrator.py resume
    --project "/data/projects/$RUN_PROJECT" --run-id "$RUN_ID"
  )
  [[ -n "${NOVEL_OS_APPROVAL:-}" ]] && args+=(--approval "$NOVEL_OS_APPROVAL")
  echo "Resuming: $RUN_PROJECT / $RUN_ID"
  "${args[@]}"
}

novel_retry() {
  local state phase chapter
  local -a args
  resolve_run_target "${1:-}" "${2:-}"
  ensure_backend
  state="$(docker compose exec -T backend python -c \
    'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); phase=d.get("current_phase") or ""; chapter=d.get("current_chapter"); print(phase, chapter if chapter is not None else "", sep="\t")' \
    "/data/projects/$RUN_PROJECT/outputs/runs/$RUN_ID/run.json")"
  # Command substitution removes trailing newlines. Use a tab-delimited
  # record so an empty current_chapter stays distinguishable from the phase.
  IFS=$'\t' read -r phase chapter <<< "$state"
  if [[ -z "$phase" ]]; then
    echo "The run manifest has no retryable phase." >&2
    return 1
  fi
  if [[ -n "$chapter" && ! "$chapter" =~ ^[0-9]+$ ]]; then
    echo "The run manifest has an invalid current chapter: $chapter" >&2
    return 1
  fi
  args=(
    docker compose exec -T backend novel-os-entrypoint
    python core/orchestrator.py retry
    --project "/data/projects/$RUN_PROJECT" --run-id "$RUN_ID" --phase "$phase"
  )
  [[ -n "$chapter" ]] && args+=(--chapter "$chapter")
  [[ -n "${NOVEL_OS_APPROVAL:-}" ]] && args+=(--approval "$NOVEL_OS_APPROVAL")
  echo "Retrying: $RUN_PROJECT / $RUN_ID / $phase${chapter:+ / chapter $chapter}"
  "${args[@]}"
}

command="${1:-up}"
if [[ "$command" != "help" && "$command" != "-h" && "$command" != "--help" \
      && !( "$command" == "novel" && ( "${2:-}" == "help" || "${2:-}" == "-h" || "${2:-}" == "--help" ) ) ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed or is not on PATH." >&2
    exit 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose is unavailable. Install the Docker Compose plugin." >&2
    exit 1
  fi
  mkdir -p "$DATA_DIR/projects" "$DATA_DIR/media"
fi

case "$command" in
  up)
    docker compose up --detach --build --wait --wait-timeout 180
    docker compose ps
    show_url
    ;;
  down)
    docker compose down
    ;;
  restart)
    docker compose down
    docker compose up --detach --build --wait --wait-timeout 180
    docker compose ps
    show_url
    ;;
  build)
    docker compose build
    ;;
  logs)
    docker compose logs --follow --tail=200 "${@:2}"
    ;;
  status)
    docker compose ps
    show_url
    ;;
  config)
    docker compose config
    ;;
  novel)
    case "${2:-}" in
      help|-h|--help) novel_usage ;;
      *) run_novel "${2:-}" ;;
    esac
    ;;
  novel-status)
    novel_status "${2:-}" "${3:-}"
    ;;
  novel-resume)
    novel_resume "${2:-}" "${3:-}"
    ;;
  novel-retry)
    novel_retry "${2:-}" "${3:-}"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: $command" >&2
    usage >&2
    exit 2
    ;;
esac
