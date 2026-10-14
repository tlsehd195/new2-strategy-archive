#!/bin/bash
# ============================================================
# NEW2 자율탐색 루프 — 1사이클 실행 래퍼
# - 락으로 중복 실행 방지 (동시 실행 시 CYCLE_SKIPPED)
# - 로그: NEW2_run/discovery/cycle_log.txt (append)
# - 성공: CYCLE_OK / 실패: CYCLE_FAILED
# 사용: bash /home/z/my-project/NEW2_run/discovery/run_cycle.sh
# 환경변수: NEW2_DISC_BATCH(기본 10) NEW2_DISC_BUDGET(기본 540초) REFRESH=1(데이터 갱신, 미사용)
# ============================================================
LOCK=/tmp/new2_disc.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "CYCLE_SKIPPED lock held by another cycle"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

cd /home/z/my-project || exit 1
mkdir -p NEW2_run/discovery

if [ "${REFRESH:-0}" = "1" ]; then
  echo "[refresh] 데이터 갱신은 기본 비활성 (engine-discovery-v1: 개발 구간 잠금). 무시."
fi

python3 NEW2_run/discovery/runner.py 2>&1 | tee -a NEW2_run/discovery/cycle_log.txt | tail -15
