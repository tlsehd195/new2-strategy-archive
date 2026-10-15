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
#   COINTRADER_SRC       비용모델 단일 소스(공개 NEW2 checkout의 src)
#   NEW2_DISC_LOG        로그 파일 경로 (기본: $NEW2_DISCOVERY_ROOT/cycle_log.txt)
#   NEW2_DISC_AUDIT      감사 로그 경로 (기본: $NEW2_DISCOVERY_ROOT/cycle_audit.log, append)
#   NEW2_DISC_TEE        로그 기록 명령 (기본: tee; 테스트에서 가짜 기록기로 교체)
#   PYTHON               파이썬 실행 파일 (기본: python3)
#   REFRESH=1            미지원 — runner가 경고하고 요약에 기록한다
# ============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || { echo "CYCLE_FAILED cannot resolve script dir"; exit 1; }

usage() {
  echo "usage: run_cycle.sh [BATCH]   (BATCH: positive integer, default \${NEW2_DISC_BATCH:-10})" >&2
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
# PIPESTATUS는 파이프라인 직후 첫 명령에서만 유효하므로 곧바로 복사한다.
"$PY" "$SCRIPT_DIR/runner.py" --batch "$BATCH" 2>&1 | "$TEE" -a "$LOG" | tail -n 15
st=("${PIPESTATUS[@]}")
rc="${st[0]}"
tee_rc="${st[1]:-1}"
tail_rc="${st[2]:-1}"

log_failed=0
if [ "$tee_rc" -ne 0 ] || [ "$tail_rc" -ne 0 ]; then
  log_failed=1
  echo "CYCLE_LOG_ERROR log pipeline failed (writer rc=$tee_rc, tail rc=$tail_rc, log=$LOG, runner rc=$rc)" >&2
fi
audit "event=RUN runner_rc=$rc log_writer_rc=$tee_rc tail_rc=$tail_rc log=$LOG"

case "$rc" in
  0)
    if [ "$log_failed" -ne 0 ]; then
      audit "event=CYCLE_FAILED reason=log_failure_after_runner_ok"
      echo "CYCLE_FAILED log pipeline failed (runner rc=0, writer rc=$tee_rc, log=$LOG)"
      exit 1
    fi
    echo "CYCLE_OK batch=$BATCH"
    exit 0 ;;
  2)
    if [ "$log_failed" -ne 0 ]; then
      audit "event=CYCLE_FAILED reason=log_failure_after_partial runner_rc=2"
      echo "CYCLE_FAILED partial cycle but log pipeline failed (writer rc=$tee_rc, log=$LOG)"
      exit 1
    fi
    echo "CYCLE_PARTIAL batch=$BATCH"; exit 2 ;;
  3)
    [ "$log_failed" -ne 0 ] && audit "event=LOG_ERROR_ON_SKIP runner_rc=3"
    echo "CYCLE_SKIPPED"; exit 3 ;;
  *)
    [ "$log_failed" -ne 0 ] && audit "event=LOG_ERROR_ON_FAILURE runner_rc=$rc"
    echo "CYCLE_FAILED rc=$rc"; exit 1 ;;
esac
