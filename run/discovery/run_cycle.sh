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
#   1 = CYCLE_FAILED    (runner 1/기타 비정상 종료, 잘못된 인자, 로그 파이프라인 실패)
#
# 중복 실행 방지는 runner.py가 state/run.lock(ProcessLock, PID+flock)으로 단일 구현한다.
# 이전 판의 /tmp mkdir 락과 하드코딩 경로(/home/z/...)는 제거되었다.
#
# 환경변수(모두 선택):
#   NEW2_DISCOVERY_ROOT  state/, results/, cycle_log.txt 위치 (기본: 이 디렉터리)
#   NEW2_DATA_DIR        데이터 디렉터리 (runner가 fail-closed 검사)
#   COINTRADER_SRC       비용모델 단일 소스(공개 NEW2 checkout의 src)
#   NEW2_DISC_LOG        로그 파일 경로 (기본: $NEW2_DISCOVERY_ROOT/cycle_log.txt)
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

BATCH="${1:-${NEW2_DISC_BATCH:-10}}"
if ! [[ "$BATCH" =~ ^[1-9][0-9]{0,5}$ ]]; then
  usage
  echo "CYCLE_FAILED usage: invalid batch '$BATCH'"
  exit 1
fi

ROOT="${NEW2_DISCOVERY_ROOT:-$SCRIPT_DIR}"
LOG="${NEW2_DISC_LOG:-$ROOT/cycle_log.txt}"
PY="${PYTHON:-python3}"
mkdir -p "$ROOT" || { echo "CYCLE_FAILED cannot create $ROOT"; exit 1; }

# ── 실행: runner 종료코드와 로그 파이프라인(tee) 실패를 모두 보존 ──
"$PY" "$SCRIPT_DIR/runner.py" --batch "$BATCH" 2>&1 | tee -a "$LOG" | tail -n 15
st=("${PIPESTATUS[@]}")
rc="${st[0]}"
tee_rc="${st[1]:-1}"

case "$rc" in
  0)
    if [ "$tee_rc" -ne 0 ]; then
      echo "CYCLE_FAILED log pipeline failed (tee rc=$tee_rc, log=$LOG)"
      exit 1
    fi
    echo "CYCLE_OK batch=$BATCH"
    exit 0 ;;
  2) echo "CYCLE_PARTIAL batch=$BATCH"; exit 2 ;;
  3) echo "CYCLE_SKIPPED"; exit 3 ;;
  *) echo "CYCLE_FAILED rc=$rc"; exit 1 ;;
esac
