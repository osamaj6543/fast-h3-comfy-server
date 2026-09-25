#!/usr/bin/env bash
# Smoke test for deploy/traditional/run.sh - exercises the real lifecycle
# (start -> pid files -> health probe -> stop -> error paths) using a fake
# interpreter that just sleeps, so no GPU or real service is needed.
#
# Run it on any POSIX box:   bash deploy/traditional/test-run-sh.sh
# Exit code 0 = all checks passed.
set -uo pipefail

R="$(cd "$(dirname "$0")" && pwd)/run.sh"
T=/tmp/fasth3-lifecycle-test
rm -rf "$T"
mkdir -p "$T/home/server" "$T/comfyhome" "$T/run" "$T/log"

# A fake interpreter: ignores its arguments and just sleeps, so we can exercise
# starting / pid tracking / health probing / stopping without any real service.
cat > "$T/fakepy.sh" <<'EOF'
#!/usr/bin/env bash
exec sleep 300
EOF
chmod +x "$T/fakepy.sh"

export FASTH3_HOME="$T/home"
export COMFY_HOME="$T/comfyhome"
export API_PY="$T/fakepy.sh"
export COMFY_PY="$T/fakepy.sh"
export RUN_DIR="$T/run"
export LOG_DIR="$T/log"
export ENV_FILE="$T/missing.env"
export READY_TIMEOUT=2
export API_PORT=8099
export COMFY_PORT=8199

fail=0
check() { # check <label> <expected> <actual>
    if [ "$2" = "$3" ]; then
        echo "PASS  $1 ($3)"
    else
        echo "FAIL  $1 (expected '$2', got '$3')"; fail=1
    fi
}

echo "== start both =="
"$R" start || true
echo
echo "== pid files written? =="
check "api pid file exists" "yes" "$([ -s "$RUN_DIR/fasth3-api.pid" ] && echo yes || echo no)"
check "comfy pid file exists" "yes" "$([ -s "$RUN_DIR/comfyui.pid" ] && echo yes || echo no)"
api_pid=$(cat "$RUN_DIR/fasth3-api.pid" 2>/dev/null || echo "")
comfy_pid=$(cat "$RUN_DIR/comfyui.pid" 2>/dev/null || echo "")
check "api process alive" "yes" "$(kill -0 "$api_pid" 2>/dev/null && echo yes || echo no)"
check "comfy process alive" "yes" "$(kill -0 "$comfy_pid" 2>/dev/null && echo yes || echo no)"
check "pid values are numeric" "yes" "$([[ "$api_pid" =~ ^[0-9]+$ && "$comfy_pid" =~ ^[0-9]+$ ]] && echo yes || echo no)"

echo
echo "== start again is idempotent =="
out=$("$R" start api)
check "second start says already running" "yes" "$(echo "$out" | grep -q 'already running' && echo yes || echo no)"
check "api pid unchanged" "$api_pid" "$(cat "$RUN_DIR/fasth3-api.pid")"

echo
echo "== status =="
"$R" status; status_rc=$?
check "status exit code non-zero when health fails" "1" "$status_rc"

echo
echo "== stop all =="
"$R" stop || true
sleep 2
check "api process gone" "no" "$(kill -0 "$api_pid" 2>/dev/null && echo yes || echo no)"
check "comfy process gone" "no" "$(kill -0 "$comfy_pid" 2>/dev/null && echo yes || echo no)"
check "api pid file removed" "no" "$([ -f "$RUN_DIR/fasth3-api.pid" ] && echo yes || echo no)"

echo
echo "== stop when already stopped =="
out=$("$R" stop)
check "stop reports not running" "yes" "$(echo "$out" | grep -q 'not running' && echo yes || echo no)"

echo
echo "== unknown service is rejected =="
"$R" start bogus >/dev/null 2>&1; check "bad service -> exit 1" "1" "$?"
"$R" >/dev/null 2>&1; check "no args -> usage exit 2" "2" "$?"

rm -rf "$T"
if [ "$fail" -eq 0 ]; then echo; echo "ALL LIFECYCLE CHECKS PASSED"; else echo; echo "SOME CHECKS FAILED"; fi
exit "$fail"
