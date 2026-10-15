#!/bin/bash
# ============================================================
# NEW2 자율탐색 루프 — 1사이클 실행 래퍼 v2 (감사 Phase 2)
# ============================================================
# 변경(F-04·F-05·F-35·F-11): pipefail+PIPESTATUS 종료코드 전파,
# PID 기반 락 + 스테일 락 회수, 크론 명세 종료코드 준수, NEW2_ROOT 구성화.
# v2-audit3(감사 Phase 3+4): runner.py가 게이트 R1~R7 준수 — R4 심볼 정합(엄격화),
# R5 탐색 수준 BH-FDR 참고(게이트 아님), R6 held-out 진단 참고(게이트 미사용),
# R7 Stouffer ρ 고정. 재검증 큐(F-03)·UNIVERSE_EXHAUSTED·라벨 v2-audit3.
# 본 래퍼의 실행 로직은 Phase 2판 그대로 (방법론 변경 없음).
#
# 종료코드(크론 명세): 0=CYCLE_OK, 2=CYCLE_PARTIAL(후보 단위 오류),
#                      1=CYCLE_FAILED, 3=SKIPPED(락)
# 사용: bash /home/z/my-project/NEW2_run/discovery/run_cycle.sh
# 환경변수: NEW2_DISC_BATCH(기본 10) NEW2_DISC_BUDGET(기본 540초)
#           NEW2_ROOT(기본 /home/z/my-project/NEW2_run)
#           REFRESH=1 → 데이터 갱신 요구는 무시(개발 구간 잠금, v1 동일)
# ============================================================
set -u

NEW2_ROOT="${NEW2_ROOT:-/home/z/my-project/NEW2_run}"
DISC="$NEW2_ROOT/discovery"
LOCK="${TMPDIR:-/tmp}/new2_disc.lock"
LOCK_STALE_S="${NEW2_DISC_LOCK_STALE:-14400}"   # 스테일 판정: 4시간
GRACE_S="${NEW2_DISC_LOCK_GRACE:-300}"          # PID 사망 후 회수 유예: 5분

# ── 락 (F-05: PID 기록 + 스테일 회수) ──
# 정책: ①살아있는 PID 소유 → skip(3) ②PID 기록됨 + 사망 확인 → 즉시 회수
# (죽은 소유자는 락을 쥐고 있지 않음 — PID 재사용 시 kill -0이 살아있음으로 보수적으로 skip)
# ③PID 기록 없음 → 보수적 age 판정(grace 내 skip, stale 초과만 회수)
if mkdir "$LOCK" 2>/dev/null; then
  echo "$$" > "$LOCK/pid"
else
  lpid="$(cat "$LOCK/pid" 2>/dev/null || true)"
  lock_age=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || date +%s) ))
  if [ -n "$lpid" ] && kill -0 "$lpid" 2>/dev/null; then
    echo "CYCLE_SKIPPED lock held by live pid=$lpid"
    exit 3
  fi
  if [ -z "$lpid" ] && [ "$lock_age" -lt "$GRACE_S" ]; then
    echo "CYCLE_SKIPPED unowned lock too fresh (age=${lock_age}s < grace=${GRACE_S}s)"
    exit 3
  fi
  if [ -n "$lpid" ] || [ "$lock_age" -ge "$LOCK_STALE_S" ]; then
    echo "[lock] stale lock reclaimed (pid=${lpid:-none} dead/absent, age=${lock_age}s)" \
      >> "$DISC/cycle_log.txt" 2>/dev/null
    rm -rf "$LOCK"
    if ! mkdir "$LOCK" 2>/dev/null; then
      echo "CYCLE_SKIPPED lock re-acquisition failed"
      exit 3
    fi
    echo "$$" > "$LOCK/pid"
  else
    echo "CYCLE_SKIPPED unowned lock within grace (age=${lock_age}s)"
    exit 3
  fi
fi
trap 'rm -rf "$LOCK" 2>/dev/null' EXIT

cd "$NEW2_ROOT/.." || exit 1
mkdir -p "$DISC"

if [ "${REFRESH:-0}" = "1" ]; then
  echo "[refresh] 데이터 갱신은 기본 비활성 (engine-discovery-v1: 개발 구간 잠금). 무시."
fi

# ── 실행 (F-04: 파이프 종료코드 유실 방지) ──
set -o pipefail
python3 "$DISC/runner.py" 2>&1 | tee -a "$DISC/cycle_log.txt" | tail -15
rc=${PIPESTATUS[0]}

# 종료코드 정규화 (F-35): runner 0/1/2 그대로, 그 외 비정상은 1
case "$rc" in
  0|1|2) exit "$rc" ;;
  *)     exit 1 ;;
esac
