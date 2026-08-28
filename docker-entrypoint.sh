#!/bin/sh
set -eu

# Loopback inside a container is the container itself. Preserve the developer's
# host-side .env while making local OpenAI-compatible endpoints reachable.
case "${NOVEL_OS_BASE_URL:-}" in
  http://127.0.0.1:*)
    NOVEL_OS_BASE_URL="http://host.docker.internal:${NOVEL_OS_BASE_URL#http://127.0.0.1:}"
    export NOVEL_OS_BASE_URL
    ;;
  https://127.0.0.1:*)
    NOVEL_OS_BASE_URL="https://host.docker.internal:${NOVEL_OS_BASE_URL#https://127.0.0.1:}"
    export NOVEL_OS_BASE_URL
    ;;
  http://localhost:*)
    NOVEL_OS_BASE_URL="http://host.docker.internal:${NOVEL_OS_BASE_URL#http://localhost:}"
    export NOVEL_OS_BASE_URL
    ;;
  https://localhost:*)
    NOVEL_OS_BASE_URL="https://host.docker.internal:${NOVEL_OS_BASE_URL#https://localhost:}"
    export NOVEL_OS_BASE_URL
    ;;
esac

exec "$@"
