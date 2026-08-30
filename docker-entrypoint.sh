#!/bin/sh
set -eu

# Loopback inside a container is the container itself. Preserve the developer's
# host-side .env while making local OpenAI-compatible endpoints reachable.
translate_loopback() {
  case "$1" in
    http://127.0.0.1:*) printf 'http://host.docker.internal:%s' "${1#http://127.0.0.1:}" ;;
    https://127.0.0.1:*) printf 'https://host.docker.internal:%s' "${1#https://127.0.0.1:}" ;;
    http://localhost:*) printf 'http://host.docker.internal:%s' "${1#http://localhost:}" ;;
    https://localhost:*) printf 'https://host.docker.internal:%s' "${1#https://localhost:}" ;;
    *) printf '%s' "$1" ;;
  esac
}

if [ -n "${NOVEL_OS_BASE_URL:-}" ]; then
  NOVEL_OS_BASE_URL="$(translate_loopback "$NOVEL_OS_BASE_URL")"
  export NOVEL_OS_BASE_URL
fi
if [ -n "${NOVEL_OS_COVER_BASE_URL:-}" ]; then
  NOVEL_OS_COVER_BASE_URL="$(translate_loopback "$NOVEL_OS_COVER_BASE_URL")"
  export NOVEL_OS_COVER_BASE_URL
fi
if [ -n "${NOVEL_OS_COVER_DIRECTOR_BASE_URL:-}" ]; then
  NOVEL_OS_COVER_DIRECTOR_BASE_URL="$(translate_loopback "$NOVEL_OS_COVER_DIRECTOR_BASE_URL")"
  export NOVEL_OS_COVER_DIRECTOR_BASE_URL
fi

exec "$@"
