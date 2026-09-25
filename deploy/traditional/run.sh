#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# FastH3 Comfy Server - traditional (non-systemd) process manager
#
# For instances without systemd (containers, WSL, minimal images) or when you
# simply prefer plain background processes + log files over service units.
#
#   ./run.sh start             start ComfyUI + the API server (backgrounded)
#   ./run.sh start api         start only the API server
#   ./run.sh start comfy       start only the ComfyUI engine
#   ./run.sh stop [api|comfy]  stop (SIGTERM, then SIGKILL after a grace period)
#   ./run.sh restart [svc]     stop + start
#   ./run.sh status [svc]      pids + HTTP health (svc helps in the per-user layout)
#   ./run.sh logs [api|comfy]  tail -f the log file(s)
#   ./run.sh foreground        run both attached to this terminal (Ctrl+C stops)
#
# Everything is configurable by environment variable; the defaults match the
# bare-metal layout produced by deploy/bare-metal/install.sh.
#
# NOTE: services run as the user invoking this script (no privilege dropping).
# To match the systemd hardening, run it as the dedicated users, e.g.:
#   sudo -u comfy   ./run.sh start comfy
#   sudo -u fasth3  ./run.sh start api
# ---------------------------------------------------------------------------
set -euo pipefail

FASTH3_HOME=${FASTH3_HOME:-/opt/fasth3}
COMFY_HOME=${COMFY_HOME:-/home/comfy/ComfyUI}
API_PY=${API_PY:-$FASTH3_HOME/.venv/bin/python}
COMFY_PY=${COMFY_PY:-/home/comfy/.venv/bin/python}
API_HOST=${API_HOST:-127.0.0.1}
API_PORT=${API_PORT:-8000}
COMFY_HOST=${COMFY_HOST:-127.0.0.1}
COMFY_PORT=${COMFY_PORT:-8188}
ENV_FILE=${ENV_FILE:-/etc/fasth3/fasth3.env}
RUN_DIR=${RUN_DIR:-/run/fasth3}
LOG_DIR=${LOG_DIR:-/var/log/fasth3}
READY_TIMEOUT=${READY_TIMEOUT:-180}   # seconds to wait for a service to answer
STOP_TIMEOUT=${STOP_TIMEOUT:-30}      # seconds to wait for a graceful stop

API_PID_FILE=$RUN_DIR/fasth3-api.pid
COMFY_PID_FILE=$RUN_DIR/comfyui.pid
API_LOG=$LOG_DIR/fasth3-api.log
COMFY_LOG=$LOG_DIR/comfyui.log

log() { printf '[run.sh] %s\n' "$*"; }
die() { printf '[run.sh] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '4,23p' "$0" | sed 's/^# \{0,1\}//'
    printf '\nOverridable environment variables:\n'
    printf '  FASTH3_HOME=%s\n  COMFY_HOME=%s\n  API_PY=%s\n  COMFY_PY=%s\n' \
        "$FASTH3_HOME" "$COMFY_HOME" "$API_PY" "$COMFY_PY"
    printf '  API_HOST=%s  API_PORT=%s  COMFY_HOST=%s  COMFY_PORT=%s\n' \
        "$API_HOST" "$API_PORT" "$COMFY_HOST" "$COMFY_PORT"
    printf '  ENV_FILE=%s  RUN_DIR=%s  LOG_DIR=%s\n' "$ENV_FILE" "$RUN_DIR" "$LOG_DIR"
    printf '  READY_TIMEOUT=%s  STOP_TIMEOUT=%s\n' "$READY_TIMEOUT" "$STOP_TIMEOUT"
}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# pid_of <pidfile>: print the pid if the process is alive (exit 1 otherwise)
pid_of() {
    local f=$1 pid
    [ -f "$f" ] || return 1
    pid=$(cat "$f" 2>/dev/null || true)
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    printf '%s' "$pid"
}

# ensure_writable_dir <preferred> <fallback>
ensure_writable_dir() {
    local preferred=$1 fallback=$2
    if mkdir -p "$preferred" 2>/dev/null && [ -w "$preferred" ]; then
        printf '%s' "$preferred"
        return 0
    fi
    mkdir -p "$fallback"
    printf '%s' "$fallback"
}

prep_dirs() {
    RUN_DIR=$(ensure_writable_dir "$RUN_DIR" "${HOME:-/tmp}/.fasth3/run")
    LOG_DIR=$(ensure_writable_dir "$LOG_DIR" "${HOME:-/tmp}/.fasth3/log")
    API_PID_FILE=$RUN_DIR/fasth3-api.pid
    COMFY_PID_FILE=$RUN_DIR/comfyui.pid
    API_LOG=$LOG_DIR/fasth3-api.log
    COMFY_LOG=$LOG_DIR/comfyui.log
}

# wait_http <url> <label> <timeout-seconds>
wait_http() {
    local url=$1 label=$2 seconds=${3:-60} i=0
    while [ "$i" -lt "$seconds" ]; do
        if curl -fsS -m 2 "$url" >/dev/null 2>&1; then
            log "$label is ready ($url)"
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done
    log "WARNING: $label not ready after ${seconds}s — check the log ($LOG_DIR)"
    return 1
}


# ---------------------------------------------------------------------------
# child modes (internal): exec the real service so the recorded pid is the
# service pid, not a wrapper's
# ---------------------------------------------------------------------------

__comfy_child() {
    echo $$ > "$COMFY_PID_FILE"
    cd "$COMFY_HOME" || die "COMFY_HOME not found: $COMFY_HOME"
    exec "$COMFY_PY" main.py --listen "$COMFY_HOST" --port "$COMFY_PORT" --fast
}

__api_child() {
    echo $$ > "$API_PID_FILE"
    if [ -f "$ENV_FILE" ]; then
        set -a
        # shellcheck disable=SC1090
        . "$ENV_FILE"
        set +a
    else
        log "env file $ENV_FILE not found — using the current environment"
    fi
    export FASTH3_COMFY_URL="${FASTH3_COMFY_URL:-http://$COMFY_HOST:$COMFY_PORT}"
    cd "$FASTH3_HOME/server" || die "FASTH3_HOME/server not found: $FASTH3_HOME"
    exec "$API_PY" -m uvicorn app.main:app \
        --host "$API_HOST" --port "$API_PORT" --proxy-headers
}

# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

start_comfy() {
    local pid
    if pid=$(pid_of "$COMFY_PID_FILE"); then
        log "ComfyUI already running (pid $pid)"
        return 0
    fi
    [ -x "$COMFY_PY" ] || die "ComfyUI interpreter not found: $COMFY_PY (set COMFY_PY)"
    [ -d "$COMFY_HOME" ] || die "ComfyUI home not found: $COMFY_HOME (set COMFY_HOME)"
    log "starting ComfyUI engine -> $COMFY_LOG"
    nohup "$0" __comfy-child >>"$COMFY_LOG" 2>&1 &
    sleep 1
    pid=$(pid_of "$COMFY_PID_FILE" || true)
    if [ -n "${pid:-}" ]; then
        log "ComfyUI pid $pid"
        wait_http "http://$COMFY_HOST:$COMFY_PORT/system_stats" "ComfyUI" "$READY_TIMEOUT" || true
    else
        log "WARNING: ComfyUI did not write a pid file — see $COMFY_LOG"
    fi
}

start_api() {
    local pid
    if pid=$(pid_of "$API_PID_FILE"); then
        log "API server already running (pid $pid)"
        return 0
    fi
    [ -x "$API_PY" ] || die "API interpreter not found: $API_PY (set API_PY)"
    log "starting API server -> $API_LOG"
    nohup "$0" __api-child >>"$API_LOG" 2>&1 &
    sleep 1
    pid=$(pid_of "$API_PID_FILE" || true)
    if [ -n "${pid:-}" ]; then
        log "API pid $pid"
        wait_http "http://$API_HOST:$API_PORT/health" "API server" "$READY_TIMEOUT" \
            || log "hint: check $API_LOG and that ComfyUI is running"
    else
        log "WARNING: API did not write a pid file — see $API_LOG"
    fi
}

# stop_one <name> <pidfile>
stop_one() {
    local name=$1 pidfile=$2 pid waited=0
    if ! pid=$(pid_of "$pidfile"); then
        log "$name is not running"
        rm -f "$pidfile"
        return 0
    fi
    log "stopping $name (pid $pid)"
    kill -TERM "$pid" 2>/dev/null || true
    while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt "$STOP_TIMEOUT" ]; do
        sleep 1
        waited=$((waited + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        log "$name ignored SIGTERM after ${STOP_TIMEOUT}s — sending SIGKILL"
        kill -KILL "$pid" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$pidfile"
    log "$name stopped"
}

stop_all() {
    case "${1:-all}" in
        api)   stop_one "API server" "$API_PID_FILE" ;;
        comfy) stop_one "ComfyUI" "$COMFY_PID_FILE" ;;
        all)
            # API first: stop accepting work before killing the engine.
            stop_one "API server" "$API_PID_FILE"
            stop_one "ComfyUI" "$COMFY_PID_FILE"
            ;;
        *) die "unknown service: $1 (use api|comfy|all)" ;;
    esac
}

# ---------------------------------------------------------------------------
# status / logs / foreground
# ---------------------------------------------------------------------------

status_one() {
    local name=$1 pidfile=$2 url=$3 pid rc=0
    if pid=$(pid_of "$pidfile"); then
        printf '%-12s running  pid %-8s %s\n' "$name" "$pid" "$url"
        if curl -fsS -m 3 "$url" >/dev/null 2>&1; then
            printf '%-12s health   OK\n' ""
        else
            printf '%-12s health   NOT RESPONDING (%s)\n' "" "$url"
            rc=1
        fi
    else
        printf '%-12s stopped\n' "$name"
        rc=1
    fi
    return $rc
}

status_all() {
    local rc=0
    printf 'run dir: %s\nlog dir: %s\n\n' "$RUN_DIR" "$LOG_DIR"
    status_one "API" "$API_PID_FILE" "http://$API_HOST:$API_PORT/health" || rc=1
    status_one "ComfyUI" "$COMFY_PID_FILE" "http://$COMFY_HOST:$COMFY_PORT/system_stats" || rc=1
    return $rc
}

status_cmd() {
    case "${1:-all}" in
        api)   status_one "API" "$API_PID_FILE" "http://$API_HOST:$API_PORT/health" ;;
        comfy) status_one "ComfyUI" "$COMFY_PID_FILE" "http://$COMFY_HOST:$COMFY_PORT/system_stats" ;;
        all)   status_all ;;
        *) die "unknown service: $1 (use api|comfy|all)" ;;
    esac
}

logs_cmd() {
    case "${1:-all}" in
        api)   tail -n 50 -f "$API_LOG" ;;
        comfy) tail -n 50 -f "$COMFY_LOG" ;;
        all)   tail -n 30 -f "$API_LOG" "$COMFY_LOG" ;;
        *) die "unknown service: $1 (use api|comfy|all)" ;;
    esac
}

foreground() {
    log "running both services in the foreground — Ctrl+C stops both"
    log "logs are also appended to $LOG_DIR"
    local rc=0
    trap 'rc=130; kill 0 2>/dev/null || true; exit $rc' INT TERM
    "$0" __comfy-child > >(sed 's/^/[comfyui] /') 2>&1 &
    "$0" __api-child > >(sed 's/^/[api] /') 2>&1 &
    wait
}

# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

main() {
    local cmd=${1:-}
    [ -n "$cmd" ] || { usage; exit 2; }

    # Internal child modes need no dir preparation.
    case "$cmd" in
        __comfy-child) __comfy_child; return ;;
        __api-child)   __api_child ;;
    esac

    prep_dirs

    case "$cmd" in
        start)
            case "${2:-all}" in
                api)   start_api ;;
                comfy) start_comfy ;;
                all)   start_comfy; start_api ;;
                *) die "unknown service: $2 (use api|comfy|all)" ;;
            esac
            ;;
        stop)    stop_all "${2:-all}" ;;
        restart)
            stop_all "${2:-all}"
            sleep 2
            case "${2:-all}" in
                api)   start_api ;;
                comfy) start_comfy ;;
                all)   start_comfy; start_api ;;
            esac
            ;;
        status)  status_cmd "${2:-all}" ;;
        logs)    logs_cmd "${2:-all}" ;;
        foreground) foreground ;;
        -h|--help|help) usage ;;
        *) usage; exit 2 ;;
    esac
}

main "$@"

