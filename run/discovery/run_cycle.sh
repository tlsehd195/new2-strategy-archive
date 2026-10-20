#!/usr/bin/env bash
# ============================================================
# NEW2 자율탐색 루프 — 1사이클 실행 래퍼 v3 (잔여결함 수정 P1-10)
# ============================================================
# 사용:  bash run/discovery/run_cycle.sh [BATCH]
#        BATCH = 양의 정수(1~999999). 미지정 시 $NEW2_DISC_BATCH, 그것도 없으면 10.
#        BATCH는 runner.py --batch 로 그대로 전달된다.
#        어느 작업 디렉터리에서 실행해도 된다(스크립트 위치 기준으로 runner.py를 찾음).
#
# 종료코드(크론 명세):
#   0 = CYCLE_OK        (runner 0)
#   2 = CYCLE_PARTIAL   (runner 2: 후보 단위 오류율 ≥ 30%)
#   3 = CYCLE_SKIPPED   (runner 3: 다른 프로세스가 state/run.lock 보유)
#   4 = CYCLE_BLOCKED_DATA (runner 4: evolution 모드에서 사용할 수 있는 시장 데이터 없음 — 아무것도 평가하지 않음)
#   1 = CYCLE_FAILED    (runner 1/기타 비정상 종료, 잘못된 인자, 로그 기록 실패와 runner 성공/부분 처리의 조합)
#
# 로그 기록(tee) 실패 정책 (runner 종료코드 × 로그 결과):
#   runner 0 + 로그 실패 -> 1  (성공 기록이 남지 않은 사이클은 성공이 아니다)
#   runner 1+ + 로그 실패 -> 1  (runner 실패와 로그 실패를 모두 stderr/감사 로그에 기록)
#   runner 2 + 로그 실패 -> 1  (부분 처리 결과의 기록이 유실됨 -> FAILED로 승격)
#   runner 3 + 로그 실패 -> 3  (아무것도 실행되지 않음 -> SKIPPED 유지, 로그 오류는 기록)
#   runner 종료코드는 어떤 경우에도 로그 실패로 덮어쓰이지 않고 stderr/감사 로그에 별도 기록된다.
#
# 중복 실행 방지는 runner.py가 state/run.lock(ProcessLock, PID+flock)으로 단일 구현한다.
# 이전 판의 /tmp mkdir 락과 하드코딩 경로(/home/z/...)는 제거되었다.
#
# 환경변수(모두 선택):
#   NEW2_DISCOVERY_ROOT  state/, results/, cycle_log.txt 위치 (기본: 이 디렉터리)
#   NEW2_DATA_DIR        데이터 디렉터리 (runner가 fail-closed 검사)
#   NEW2_DISC_LOG        로그 파일 경로 (기본: $NEW2_DISCOVERY_ROOT/cycle_log.txt)
#   NEW2_DISC_AUDIT      감사 로그 경로 (기본: $NEW2_DISCOVERY_ROOT/cycle_audit.log, append)
#   NEW2_DISC_TEE        로그 기록 명령 (기본: tee; 테스트에서 가짜 기록기로 교체)
#   PYTHON               파이썬 실행 파일 (기본: python3)
#   NEW2_DISC_MODE       legacy(기본) | evolve | full — runner.py --mode 로 전달.
#                        legacy = v2 후보 엔진(state/state.json, state/discovery_ledger.jsonl),
#                        evolve = 자동 진화 엔진(state/evolution/*), full = 둘 다 순서대로.
#                        미지정이면 legacy로 실행하고 stderr에 경고한다(ADR 0010).
#                        모든 결과 줄(CYCLE_OK 등)에 mode=가 붙는다. 이 줄은 실행 결과일 뿐,
#                        전략 검증 통과를 뜻하지 않는다.
#   NEW2_COST_PROFILE    new2_default(기본) | assumed_public_v1 | public_estimate:<기록.json> — 모두 미검증, 승격 불가
#   REFRESH=1            미지원 — runner가 경고하고 요약에 기록한다
# ============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || { echo "CYCLE_FAILED cannot resolve script dir"; exit 1; }

usage() {
  echo "usage: run_cycle.sh [BATCH]   (BATCH: positive integer, default \${NEW2_DISC_BATCH:-10})" >&2
  echo "       NEW2_DISC_MODE=legacy|evolve|full (default legacy: the v2 candidate engine, not evolution)" >&2
}

if [ "$#" -gt 1 ]; then
  usage
  echo "CYCLE_FAILED usage: too many arguments"
  exit 1
fi
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

if [ "$#" -eq 1 ]; then BATCH="$1"; else BATCH="${NEW2_DISC_BATCH:-10}"; fi
if ! [[ "$BATCH" =~ ^[1-9][0-9]{0,5}$ ]]; then
  usage
  echo "CYCLE_FAILED usage: invalid batch '$BATCH'"
  exit 1
fi

MODE="${NEW2_DISC_MODE:-legacy}"
case "$MODE" in
  legacy|evolve|full) ;;
  *) usage; echo "CYCLE_FAILED usage: invalid NEW2_DISC_MODE '$MODE'"; exit 1 ;;
esac
if [ -z "${NEW2_DISC_MODE:-}" ]; then
  echo "CYCLE_WARNING NEW2_DISC_MODE not set: running mode=legacy (v2 candidate engine). Set NEW2_DISC_MODE=evolve for the evolution engine." >&2
fi

ROOT="${NEW2_DISCOVERY_ROOT:-$SCRIPT_DIR}"
LOG="${NEW2_DISC_LOG:-$ROOT/cycle_log.txt}"
AUDIT="${NEW2_DISC_AUDIT:-$ROOT/cycle_audit.log}"
PY="${PYTHON:-python3}"
TEE="${NEW2_DISC_TEE:-tee}"

# 감사 로그: 한 줄 key=value. 감사 로그 자체가 쓰기 실패해도 stderr에는 항상 남긴다.
audit() {
  local line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) batch=${BATCH} $*"
  echo "CYCLE_AUDIT $line" >&2
  { printf '%s\n' "$line" >>"$AUDIT"; } 2>/dev/null \
    || echo "CYCLE_AUDIT_WRITE_FAILED audit=$AUDIT" >&2
}

mkdir -p "$ROOT" 2>/dev/null || { audit "event=FAILED reason=root_dir_create_failed path=$ROOT"; echo "CYCLE_FAILED cannot create $ROOT"; exit 1; }
mkdir -p "$(dirname "$LOG")" 2>/dev/null || { audit "event=FAILED reason=log_dir_create_failed log=$LOG"; echo "CYCLE_FAILED cannot create log directory for $LOG"; exit 1; }

# ── 실행: runner / 로그 기록기 / 출력 축약기 종료코드를 각각 보존 ──
# 파이프라인은 백그라운드 서브셸에서 돌리고 PIPESTATUS를 파일로 받는다. 그래야
# 이 스크립트가 SIGTERM/SIGINT/SIGHUP을 받았을 때 바로 반응해 runner에 SIGTERM을
# 전달하고(runner는 CYCLE_FAILED를 기록하고 잠금을 풀고 끝난다), 끝날 때까지
# 기다린 뒤 CYCLE_FAILED(1)로 끝낼 수 있다. 신호로 끊긴 사이클은 어떤 경우에도
# 성공으로 보고되지 않는다. 임시 파일은 종료 시 지운다(2026-10-20 지시 §7).
TMP_RC="$(mktemp "${TMPDIR:-/tmp}/run_cycle_rc.XXXXXX")" || { audit "event=FAILED reason=mktemp_failed"; echo "CYCLE_FAILED cannot create temp file"; exit 1; }
TMP_PID="$(mktemp "${TMPDIR:-/tmp}/run_cycle_pid.XXXXXX")" || { rm -f "$TMP_RC"; audit "event=FAILED reason=mktemp_failed"; echo "CYCLE_FAILED cannot create temp file"; exit 1; }
# shellcheck disable=SC2317,SC2329  # invoked by the EXIT trap
cleanup() { rm -f "$TMP_RC" "$TMP_PID"; }
trap cleanup EXIT
interrupted=""
# shellcheck disable=SC2317,SC2329  # invoked by the signal traps
on_signal() {
  interrupted="$1"
  local rpid
  rpid="$(cat "$TMP_PID" 2>/dev/null)"
  if [ -n "$rpid" ]; then kill -TERM "$rpid" 2>/dev/null; fi
}
trap 'on_signal TERM' TERM
trap 'on_signal INT' INT
trap 'on_signal HUP' HUP

(
  { "$PY" "$SCRIPT_DIR/runner.py" --batch "$BATCH" --mode "$MODE" & echo "$!" >"$TMP_PID"; wait "$!"; } 2>&1 \
    | "$TEE" -a "$LOG" | tail -n 15
  # PIPESTATUS는 파이프라인 직후 첫 명령에서만 유효하므로 곧바로 기록한다.
  echo "${PIPESTATUS[@]}" >"$TMP_RC"
) &
child=$!
while kill -0 "$child" 2>/dev/null; do
  wait "$child"
done
read -r -a st <"$TMP_RC" 2>/dev/null || st=()
rc="${st[0]:-1}"
tee_rc="${st[1]:-1}"
tail_rc="${st[2]:-1}"
if [ -n "$interrupted" ]; then
  audit "event=CYCLE_FAILED reason=signal sig=$interrupted runner_rc=$rc"
  echo "CYCLE_FAILED signal=$interrupted runner_rc=$rc mode=$MODE"
  exit 1
fi

log_failed=0
if [ "$tee_rc" -ne 0 ] || [ "$tail_rc" -ne 0 ]; then
  log_failed=1
  echo "CYCLE_LOG_ERROR log pipeline failed (writer rc=$tee_rc, tail rc=$tail_rc, log=$LOG, runner rc=$rc)" >&2
fi
audit "event=RUN mode=$MODE runner_rc=$rc log_writer_rc=$tee_rc tail_rc=$tail_rc log=$LOG"

case "$rc" in
  0)
    if [ "$log_failed" -ne 0 ]; then
      audit "event=CYCLE_FAILED reason=log_failure_after_runner_ok"
      echo "CYCLE_FAILED log pipeline failed (runner rc=0, writer rc=$tee_rc, log=$LOG)"
      exit 1
    fi
    echo "CYCLE_OK batch=$BATCH mode=$MODE"
    exit 0 ;;
  2)
    if [ "$log_failed" -ne 0 ]; then
      audit "event=CYCLE_FAILED reason=log_failure_after_partial runner_rc=2"
      echo "CYCLE_FAILED partial cycle but log pipeline failed (writer rc=$tee_rc, log=$LOG)"
      exit 1
    fi
    echo "CYCLE_PARTIAL batch=$BATCH mode=$MODE"; exit 2 ;;
  3)
    [ "$log_failed" -ne 0 ] && audit "event=LOG_ERROR_ON_SKIP runner_rc=3"
    echo "CYCLE_SKIPPED mode=$MODE"; exit 3 ;;
  4)
    if [ "$log_failed" -ne 0 ]; then
      audit "event=CYCLE_FAILED reason=log_failure_after_blocked_data runner_rc=4"
      echo "CYCLE_FAILED rc=4 blocked-data cycle but log pipeline failed (writer rc=$tee_rc, log=$LOG)"
      exit 1
    fi
    echo "CYCLE_BLOCKED_DATA batch=$BATCH mode=$MODE"; exit 4 ;;
  *)
    [ "$log_failed" -ne 0 ] && audit "event=LOG_ERROR_ON_FAILURE runner_rc=$rc"
    echo "CYCLE_FAILED rc=$rc mode=$MODE"; exit 1 ;;
esac
