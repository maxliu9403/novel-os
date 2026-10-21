#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

DATA_DIR="${NOVEL_OS_DATA_DIR:-$ROOT_DIR/docker-data}"
WEB_PORT="${NOVEL_OS_WEB_PORT:-5174}"

usage() {
  cat <<'EOF'
Usage: ./deploy.sh <command>

Commands:
  up       Sync bundled Skills, then build and start both services
  down     Stop and remove the containers (persistent data is retained)
  restart  Sync bundled Skills, then rebuild and restart both services
  build    Build both images without starting containers
  logs     Follow service logs
  status   Show container and health status
  config   Validate and print the resolved Compose configuration
  novel [PROMPT]
           Start the full-book runner with an interactive setup
  novel-status [RUN_ID]
           Show a run; without arguments, use the most recent run
  novel-resume [RUN_ID]
           Resume a run; without arguments, use the most recent run
  novel-retry [RUN_ID]
           Retry the current stage of a failed or blocked run
  novel-cover [PROMPT]
           Generate or manage portrait 2:3 cover candidates without restarting services
  cover-rollback capture|status|restore
           Save or switch cover-only source checkpoints; no deployment or data rollback
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
  ./deploy.sh novel-status [RUN_ID]
      Show status, current phase, chapter, and error. Without arguments, uses
      the most recently updated run across all persistent projects. With a
      RUN_ID, searches all persistent projects, so PROJECT is not required.

  ./deploy.sh novel-resume [RUN_ID]
      Resume a paused or interrupted run from its durable checkpoints. Without
      arguments, uses the most recent run. With a RUN_ID, searches all projects.

  ./deploy.sh novel-retry [RUN_ID]
      Retry the current failed or blocked stage. Without arguments, uses the
      most recent run and reads its phase and chapter automatically. With a
      RUN_ID, searches all projects.

  The legacy form [PROJECT] [RUN_ID] remains accepted for existing scripts.

  ./deploy.sh novel --help
      Show this complete command reference. Also accepts -h or help.

Deployment and diagnostics:
  ./deploy.sh up
      Update bundled Codex Skills, build and start both services, and wait for health checks.

  ./deploy.sh down
      Stop containers. Manuscripts, media, settings, and the database remain
      under the configured persistent data directory (docker-data/ by default).

  ./deploy.sh restart
      Update bundled Codex Skills, rebuild and restart both services. Run this after changing
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
  NOVEL_OS_TITLE              Explicit title override (otherwise infer from prompt)
  NOVEL_OS_GENRE              Explicit genre override (otherwise infer from prompt)
  NOVEL_OS_CHAPTERS           Positive integer chapter count
  NOVEL_OS_WORDS              Positive integer target word count
  NOVEL_OS_EDIT_MODE          line, developmental, pacing, dialogue, or tension
  NOVEL_OS_APPROVAL           auto or review_required
  NOVEL_OS_QUALITY_POLICY     legacy or evidence_v1
  NOVEL_OS_METHOD_MODE        off or advisory; omitted inherits project review policy
  NOVEL_OS_OUTPUT             Space-separated formats: markdown html docx epub pdf
  NOVEL_OS_MAX_RETRIES        Transient failure retry count
  NOVEL_OS_MAX_QUALITY_REPAIRS
                              Automatic quality repair count
  NOVEL_OS_DRY_RUN=1          Persist intake only; do not call the LLM
  NOVEL_OS_WEB_PORT           Studio host port; default 5174
  NOVEL_OS_DATA_DIR           Host data directory; default ./docker-data
  NOVEL_OS_CODEX_AUTH_FILE    Host Codex auth file (default ~/.codex/auth.json)
  NOVEL_OS_CODEX_CONFIG_FILE  Host Codex config (default ~/.codex/config.toml)
  CODEX_HOME                 Host Skill destination (default ~/.codex); up/restart only

Examples:
  ./deploy.sh novel
  ./deploy.sh novel './prompt/my-novel.md'
  NOVEL_OS_OUTPUT='epub pdf' ./deploy.sh novel './prompt/my-novel.md'
  NOVEL_OS_APPROVAL=auto ./deploy.sh novel-resume
  ./deploy.sh novel-status RUN_ID
  ./deploy.sh logs backend

Runtime behavior:
  Novel commands reuse a healthy backend container and do not manage the
  frontend. Existing containers are never recreated by novel commands. Project
  artifacts are stored under the configured data directory at
  projects/PROJECT/outputs/.
EOF
}

cover_usage() {
  cat <<'EOF'
Novel OS cover commands

Generate portrait 2:3 cover candidates (the provider's native resolution is preserved):
  ./deploy.sh novel-cover PROMPT
  ./deploy.sh novel-cover generate PROMPT [PROJECT]
      Parse the approved COVER_HANDOFF block, create 3-5 distinct concepts,
      and generate one image per concept. PROJECT defaults to the Prompt filename.

Manage persisted candidates:
  ./deploy.sh novel-cover list PROJECT
  ./deploy.sh novel-cover select PROJECT COVER_SET CANDIDATE REVISION ACTIVE_REVISION
  ./deploy.sh novel-cover reject PROJECT COVER_SET CANDIDATE REVISION
  ./deploy.sh novel-cover retry PROJECT COVER_SET CANDIDATE REVISION

  Add --confirm-stale to the select form only when intentionally selecting a
  cover derived from an older Prompt or story foundation.

Optional environment overrides:
  NOVEL_OS_PROJECT_NAME          Project name for direct Prompt generation
  NOVEL_OS_COVER_COUNT           Candidate count from 3 through 5; default 4
  NOVEL_OS_COVER_MODEL           Image model; default gpt-image-2
  NOVEL_OS_COVER_SIZE            Preferred request size (default 2048x3072); must be portrait 2:3
  NOVEL_OS_COVER_QUALITY         low, medium, high, or auto; default high
  NOVEL_OS_COVER_FORMAT          jpeg or png; default jpeg
  NOVEL_OS_COVER_TIMEOUT_SECONDS Provider timeout; default 180
  NOVEL_OS_COVER_DIRECTOR_PROVIDER Optional planning provider; defaults to writing provider
  NOVEL_OS_COVER_DIRECTOR_MODEL    Optional planning model; defaults to writing model
  NOVEL_OS_COVER_DIRECTOR_BASE_URL Optional planning endpoint
  NOVEL_OS_COVER_DIRECTOR_API_KEY  Optional planning key
  NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS
                                  Planning timeout; default 180
  NOVEL_OS_COVER_DIRECTION_ID     Approved v2 direction id
  NOVEL_OS_COVER_APPROVED_DIRECTION_SHA256
                                  Exact approved direction content hash

Provider URL and key are configured in ignored .env values or Studio Settings.
Cover commands reuse a healthy backend, never restart services, and write to:
  <data-dir>/projects/PROJECT/outputs/deliverables/covers/
  <data-dir>/projects/PROJECT/outputs/deliverables/book-package.zip

The selection workspace is:
  http://localhost:5174/projects/PROJECT/covers
EOF
}

require_docker() {
  local compose_environment key value
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed or is not on PATH." >&2
    return 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose is unavailable. Install the Docker Compose plugin." >&2
    return 1
  fi

  # Let Compose handle .env quotes, comments, interpolation, and precedence.
  # Only read the two values used on the host; never evaluate or print the
  # complete environment, which can include provider credentials.
  compose_environment="$(docker compose config --environment)" || return 1
  while IFS='=' read -r key value; do
    case "$key" in
      NOVEL_OS_DATA_DIR) DATA_DIR="${value:-$ROOT_DIR/docker-data}" ;;
      NOVEL_OS_WEB_PORT) WEB_PORT="${value:-5174}" ;;
    esac
  done <<< "$compose_environment"

  mkdir -p "$DATA_DIR/projects" "$DATA_DIR/media"
  # Compose resolves relative volume paths from the repository root. Export an
  # absolute path so custom data directories behave identically for Compose,
  # run discovery, and the paths printed by this launcher.
  DATA_DIR="$(cd "$DATA_DIR" && pwd)"
  export NOVEL_OS_DATA_DIR="$DATA_DIR"
  export NOVEL_OS_WEB_PORT="$WEB_PORT"
}

show_url() {
  local binding port
  binding="$(docker compose port frontend 80 2>/dev/null | tail -n 1 || true)"
  port="${binding##*:}"
  if [[ -n "$binding" && "$port" != "$binding" ]]; then
    echo "Novel OS: http://localhost:${port}"
  else
    echo "Novel OS: http://localhost:${WEB_PORT}"
  fi
}

sync_project_skills() {
  local python_bin="python3"
  [[ ! -x "$ROOT_DIR/venv/bin/python" ]] || python_bin="$ROOT_DIR/venv/bin/python"
  if ! command -v "$python_bin" >/dev/null 2>&1; then
    echo "Python 3 is required to sync bundled Skills before deployment." >&2
    return 1
  fi
  # Do this before service recreation: an unwritable host Skill directory must
  # not leave the current application stopped. The helper backs up changed copies.
  "$python_bin" "$ROOT_DIR/scripts/sync_skills.py" \
    --source "$ROOT_DIR/skills" --codex-home "${CODEX_HOME:-$HOME/.codex}"
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

validate_run_id() {
  local value="$1"
  if [[ -z "$value" || ! "$value" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "Run ID must contain only letters, numbers, dots, underscores, or hyphens." >&2
    return 1
  fi
}

run_novel() {
  local prompt_file project_name title genre chapters words edit_mode approval quality output dry_run answer
  local -a command_args output_formats

  prompt_file="$(choose_prompt "${1:-}")"
  prompt_file="$(cd "$(dirname "$prompt_file")" && pwd)/$(basename "$prompt_file")"
  project_name="${NOVEL_OS_PROJECT_NAME:-$(basename "$prompt_file")}"
  project_name="${project_name%.*}"
  title="${NOVEL_OS_TITLE:-}"
  genre="${NOVEL_OS_GENRE:-}"
  chapters="${NOVEL_OS_CHAPTERS:-}"
  words="${NOVEL_OS_WORDS:-}"
  edit_mode="${NOVEL_OS_EDIT_MODE:-line}"
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
  if [[ "$edit_mode" != "line" && "$edit_mode" != "developmental" && "$edit_mode" != "pacing" && "$edit_mode" != "dialogue" && "$edit_mode" != "tension" ]]; then
    echo "Edit mode must be line, developmental, pacing, dialogue, or tension." >&2
    return 1
  fi
  if [[ -n "${NOVEL_OS_METHOD_MODE:-}" && "$NOVEL_OS_METHOD_MODE" != "off" && "$NOVEL_OS_METHOD_MODE" != "advisory" ]]; then
    echo "Method mode must be off or advisory." >&2
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
  [[ -n "$title" ]] && printf '  Title:   %s\n' "$title"
  [[ -n "$genre" ]] && printf '  Genre:   %s\n' "$genre"
  printf '  Chapters: %s\n' "${chapters:-infer}"
  printf '  Words:    %s\n' "${words:-infer}"
  printf '  Edit:     %s\n' "$edit_mode"
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
    --edit-mode "$edit_mode"
    --approval "$approval"
    --quality-policy "$quality"
    --max-retries "${NOVEL_OS_MAX_RETRIES:-5}"
    --max-quality-repairs "${NOVEL_OS_MAX_QUALITY_REPAIRS:-2}"
  )
  [[ -n "$title" ]] && command_args+=(--title "$title")
  [[ -n "$genre" ]] && command_args+=(--genre "$genre")
  [[ -n "$chapters" ]] && command_args+=(--chapters "$chapters")
  [[ -n "$words" ]] && command_args+=(--words "$words")
  [[ -n "${NOVEL_OS_METHOD_MODE:-}" ]] && command_args+=(--method-mode "$NOVEL_OS_METHOD_MODE")
  [[ "$dry_run" == "1" ]] && command_args+=(--dry-run)
  command_args+=(--output "${output_formats[@]}")

  "${command_args[@]}" < "$prompt_file"
  printf '\nProject files: %s/projects/%s/outputs\n' "$DATA_DIR" "$project_name"
}

run_novel_cover_generate() {
  local prompt_file project_name count direction_id direction_sha
  local -a args
  prompt_file="$(choose_prompt "${1:-}")"
  prompt_file="$(cd "$(dirname "$prompt_file")" && pwd)/$(basename "$prompt_file")"
  project_name="${2:-${NOVEL_OS_PROJECT_NAME:-$(basename "$prompt_file")}}"
  project_name="${project_name%.*}"
  count="${NOVEL_OS_COVER_COUNT:-4}"
  direction_id="${NOVEL_OS_COVER_DIRECTION_ID:-}"
  direction_sha="${NOVEL_OS_COVER_APPROVED_DIRECTION_SHA256:-}"

  validate_project_name "$project_name"
  if [[ ! "$count" =~ ^[3-5]$ ]]; then
    echo "Cover candidate count must be between 3 and 5." >&2
    return 1
  fi

  require_docker
  ensure_backend
  args=(
    docker compose exec -T backend novel-os-entrypoint
    python core/orchestrator.py cover generate
    --project "/data/projects/$project_name"
    --prompt - --count "$count"
  )
  if [[ -n "$direction_id" || -n "$direction_sha" ]]; then
    if [[ -z "$direction_id" || ! "$direction_sha" =~ ^[0-9a-f]{64}$ ]]; then
      echo "V2 cover generation requires NOVEL_OS_COVER_DIRECTION_ID and a 64-character NOVEL_OS_COVER_APPROVED_DIRECTION_SHA256." >&2
      return 1
    fi
    args+=(--direction-id "$direction_id" --approved-direction-sha256 "$direction_sha")
  fi
  "${args[@]}" < "$prompt_file"
  printf '\nCover candidates: %s/projects/%s/outputs/deliverables/covers/pending\n' "$DATA_DIR" "$project_name"
  printf 'Delivery package: %s/projects/%s/outputs/deliverables/book-package.zip\n' "$DATA_DIR" "$project_name"
  printf 'Cover Studio: http://localhost:%s/projects/%s/covers\n' "$WEB_PORT" "$project_name"
}

run_novel_cover_manage() {
  local action="$1" project_name="${2:-}" cover_set="${3:-}" candidate="${4:-}"
  local revision="${5:-}" active_revision="${6:-}" confirm="${7:-}"
  local -a args

  if [[ -z "$project_name" ]]; then
    echo "Project is required. Run ./deploy.sh novel-cover --help for command forms." >&2
    return 1
  fi
  validate_project_name "$project_name"
  args=(
    docker compose exec -T backend novel-os-entrypoint
    python core/orchestrator.py cover "$action"
    --project "/data/projects/$project_name"
  )
  case "$action" in
    list) ;;
    select)
      if [[ -z "$cover_set" || -z "$candidate" || ! "$revision" =~ ^[0-9]+$ || ! "$active_revision" =~ ^[0-9]+$ ]]; then
        echo "Select requires PROJECT COVER_SET CANDIDATE REVISION ACTIVE_REVISION." >&2
        return 1
      fi
      args+=(--cover-set "$cover_set" --candidate "$candidate" --expected-revision "$revision" --expected-active-revision "$active_revision")
      if [[ -n "$confirm" && "$confirm" != "--confirm-stale" ]]; then
        echo "Unknown select option: $confirm" >&2
        return 1
      fi
      [[ "$confirm" == "--confirm-stale" ]] && args+=(--confirm-stale)
      ;;
    reject|retry)
      if [[ -z "$cover_set" || -z "$candidate" || ! "$revision" =~ ^[0-9]+$ ]]; then
        echo "$action requires PROJECT COVER_SET CANDIDATE REVISION." >&2
        return 1
      fi
      args+=(--cover-set "$cover_set" --candidate "$candidate" --expected-revision "$revision")
      ;;
  esac

  require_docker
  ensure_backend
  "${args[@]}"
  printf '\nDelivery package: %s/projects/%s/outputs/deliverables/book-package.zip\n' "$DATA_DIR" "$project_name"
  printf 'Cover Studio: http://localhost:%s/projects/%s/covers\n' "$WEB_PORT" "$project_name"
}

run_novel_cover() {
  case "${1:-}" in
    help|-h|--help) cover_usage ;;
    generate) run_novel_cover_generate "${2:-}" "${3:-}" ;;
    list|select|reject|retry) run_novel_cover_manage "$@" ;;
    "")
      echo "Pass a Prompt file: ./deploy.sh novel-cover ./prompt/your-novel.md" >&2
      return 1
      ;;
    *) run_novel_cover_generate "$1" "${2:-}" ;;
  esac
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
  local requested_project="${1:-}" requested_run="${2:-}" manifest relative file
  local -a matches=()
  if [[ -n "$requested_run" ]]; then
    # Backward-compatible form: PROJECT RUN_ID.
    validate_project_name "$requested_project" || return 1
    validate_run_id "$requested_run" || return 1
    manifest="$DATA_DIR/projects/$requested_project/outputs/runs/$requested_run/run.json"
    if [[ ! -f "$manifest" ]]; then
      echo "Run not found: $requested_project / $requested_run" >&2
      return 1
    fi
  elif [[ -n "$requested_project" ]]; then
    # Preferred form: RUN_ID. Run IDs are generated independently of projects,
    # so locate the matching manifest across the persistent project root.
    validate_run_id "$requested_project" || return 1
    while IFS= read -r file; do
      matches+=("$file")
    done < <(find "$DATA_DIR/projects" -type f -path "*/outputs/runs/$requested_project/run.json" -print 2>/dev/null)
    if [[ ${#matches[@]} -eq 0 ]]; then
      # Keep the old one-argument PROJECT form useful while it is not advertised.
      if [[ -d "$DATA_DIR/projects/$requested_project" ]]; then
        manifest="$(latest_run_manifest "$requested_project")" || return 1
      else
        echo "Run not found: $requested_project" >&2
        return 1
      fi
    elif [[ ${#matches[@]} -gt 1 ]]; then
      echo "Run ID is ambiguous across projects: $requested_project" >&2
      printf 'Matching manifests:\n' >&2
      printf '  %s\n' "${matches[@]}" >&2
      echo "Use the legacy form: ./deploy.sh novel-status PROJECT RUN_ID" >&2
      return 1
    else
      manifest="${matches[0]}"
    fi
  else
    if ! manifest="$(latest_run_manifest)"; then
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
  if [[ "$command" != "novel-cover" && "$command" != "cover-rollback" ]]; then
    require_docker
  fi
fi

case "$command" in
  up)
    sync_project_skills
    docker compose up --detach --build --wait --wait-timeout 180
    docker compose ps
    show_url
    ;;
  down)
    docker compose down
    ;;
  restart)
    sync_project_skills
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
  novel-cover)
    run_novel_cover "${@:2}"
    ;;
  cover-rollback)
    python_bin="python3"
    [[ ! -x "$ROOT_DIR/venv/bin/python" ]] || python_bin="$ROOT_DIR/venv/bin/python"
    "$python_bin" "$ROOT_DIR/scripts/cover_rollback.py" "${@:2}"
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
