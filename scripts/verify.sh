#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'USAGE'
Usage: ./scripts/verify.sh [--list|quick|full]

  --list  Print configured offline verification commands.
  quick   Run focused release, stability, privacy, and security tests.
  full    Run all tests, release gate checks, and isolated wheel/self-check smoke.
USAGE
}

print_commands() {
  cat <<'COMMANDS'
Quick commands:
  - PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_release_readiness.py tests/test_stability.py tests/test_privacy_flags.py tests/test_security_audit.py
Full-only commands:
  - PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
  - python scripts/check_release_ready.py
  - python scripts/check_release_ready.py --target 1.0.0 (expect exit 3)
  - isolated no-build-isolation wheel install and doctor --self-check --json
COMMANDS
}

run_quick() {
  cd "$ROOT"
  PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
    tests/test_release_readiness.py \
    tests/test_stability.py \
    tests/test_privacy_flags.py \
    tests/test_security_audit.py
}

check_blocked_release_gate() {
  local output
  local rc
  set +e
  output="$(cd "$ROOT" && python scripts/check_release_ready.py --target 1.0.0 2>&1)"
  rc=$?
  set -e
  if [[ $rc -ne 3 ]] || ! printf '%s' "$output" | grep -Fq 'release readiness: blocked'; then
    printf '%s\n' "$output" >&2
    echo "ERROR: expected blocked v1 gate with exit 3" >&2
    return 1
  fi
  printf '%s\n' "$output"
}

wheel_smoke() {
  local temp_root
  local python_bin
  temp_root="$(mktemp -d "${TMPDIR:-/tmp}/aicg-aiwf-wheel.XXXXXX")"
  python_bin="$(command -v python)"
  cleanup_wheel_smoke() {
    rm -rf "$temp_root"
  }
  trap cleanup_wheel_smoke RETURN

  mkdir -p \
    "$temp_root/wheel" \
    "$temp_root/site" \
    "$temp_root/run" \
    "$temp_root/bin" \
    "$temp_root/codex-home" \
    "$temp_root/claude-home"
  (
    cd "$ROOT"
    python -m pip wheel . --no-build-isolation --no-deps -w "$temp_root/wheel"
  )
  local wheel
  wheel="$(find "$temp_root/wheel" -maxdepth 1 -name '*.whl' -type f -print -quit)"
  [[ -n "$wheel" ]] || { echo "ERROR: wheel was not produced" >&2; return 1; }
  python -m pip install --no-deps --target "$temp_root/site" "$wheel"
  (
    cd "$temp_root/run"
    PYTHONPATH="$temp_root/site" \
      AICG_HOME="$temp_root/home" \
      CODEX_HOME="$temp_root/codex-home" \
      CLAUDE_CONFIG_DIR="$temp_root/claude-home" \
      PATH="$temp_root/bin" \
      "$python_bin" -m aicg init
    PYTHONPATH="$temp_root/site" \
      AICG_HOME="$temp_root/home" \
      CODEX_HOME="$temp_root/codex-home" \
      CLAUDE_CONFIG_DIR="$temp_root/claude-home" \
      PATH="$temp_root/bin" \
      "$python_bin" -m aicg doctor --self-check --json
  )
  cleanup_wheel_smoke
  trap - RETURN
}

run_full() {
  cd "$ROOT"
  PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
  python scripts/check_release_ready.py
  check_blocked_release_gate
  wheel_smoke
}

mode="${1:-quick}"
case "$mode" in
  --list|list)
    print_commands
    ;;
  quick)
    run_quick
    ;;
  full)
    run_full
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 64
    ;;
esac
