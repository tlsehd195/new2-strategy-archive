#!/bin/bash
# ============================================================
# NEW2 영속 저장소 TIER1 스냅샷 (Task 42)
# - TIER1 = 대체 불가 데이터 (실측 수집 장부·측정 결과·PREREG·보고서·소스)
# - TIER2 = NEW2_run/data 시세 원천 326MB (거래소에서 재다운로드 가능 → 제외)
# - SQLite는 VACUUM INTO로 WAL 포함 일관성 스냅샷
# - 스냅샷 7개 회전 + SHA256 manifest + log.md 1행 기록
# 사용: bash /home/z/my-project/NEW2_run/backup_snapshot.sh
# ============================================================
set -u
cd /home/z/my-project || exit 1

TS=$(date -u +%Y%m%dT%H%M%SZ)
BK="NEW2_run/backup"
STAGE="$BK/stage"
mkdir -p "$STAGE"

FAIL=0

# 0) SQLite 일관성 스냅샷 (활성 writer와 무관하게 읽기 일관 보장)
if ! bun -e "const {Database}=require('bun:sqlite'); const d=new Database('mini-services/liq-collector/data/liquidations.db',{readonly:true}); d.exec(\"VACUUM INTO 'NEW2_run/backup/stage/liquidations.db'\"); const n=d.query('SELECT COUNT(*) n FROM liquidations').get().n; console.log('DB_SNAPSHOT_ROWS='+n);" > "$STAGE/db_meta.txt" 2>&1; then
  echo "ERROR: liquidations.db VACUUM INTO 실패" >&2
  FAIL=1
fi

# 1) JSONL 장부 (append-only — 마지막 미완결 행 가능성 있음, manifest에 명기)
cp -f mini-services/fng-watcher/data/fng_ledger.jsonl        "$STAGE/fng_ledger.jsonl"        2>/dev/null || { echo "WARN: fng_ledger 없음" >&2; FAIL=1; }
cp -f mini-services/maker-fill-monitor/data/fill_ledger.jsonl "$STAGE/fill_ledger.jsonl"      2>/dev/null || echo "INFO: fill_ledger 아직 없음(신설)" >&2

# 2) results / research
tar -cf "$STAGE/results.tar"  NEW2_run/results  2>/dev/null || FAIL=1
tar -cf "$STAGE/research.tar" NEW2_run/research 2>/dev/null || FAIL=1

# 3) 문서 + PREREG + 소스 (측정·복구에 필요한 전부)
tar -cf "$STAGE/docs_and_code.tar" \
  NEW2_run/PREREG_*.md \
  NEW2_run/*.md \
  worklog.md \
  NEW2_전략탐색_종합정리.md \
  NEW2_전략연구_보고서_v*.md \
  HANDOVER_NEXT_SESSION.md \
  NEW2_분석보고서.md \
  전략리서치_보고서.md \
  strategy_research_v2.md \
  src/instrumentation.ts \
  prisma/schema.prisma \
  mini-services/liq-collector/index.ts \
  mini-services/liq-collector/package.json \
  mini-services/fng-watcher/index.ts \
  mini-services/fng-watcher/package.json \
  mini-services/maker-fill-monitor/index.ts \
  mini-services/maker-fill-monitor/package.json \
  NEW2_run/backup_snapshot.sh \
  NEW2_run/git_backup_push.sh \
  NEW2_run/discovery/engine_lib.py \
  NEW2_run/discovery/generator.py \
  NEW2_run/discovery/runner.py \
  NEW2_run/discovery/run_cycle.sh \
  NEW2_run/discovery/state.json \
  NEW2_run/discovery/discovery_ledger.jsonl \
  NEW2_run/discovery/cycles.md \
  NEW2_run/discovery/promotion_queue.md \
  2>/dev/null || FAIL=1

# 4) manifest
{
  echo "# NEW2 TIER1 SNAPSHOT $TS"
  echo "sqlite: $(grep -o 'DB_SNAPSHOT_ROWS=[0-9]*' "$STAGE/db_meta.txt" 2>/dev/null || echo 'UNKNOWN')"
  echo "fng_ledger_rows: $(wc -l < "$STAGE/fng_ledger.jsonl" 2>/dev/null || echo 0)"
  echo "fill_ledger_rows: $(wc -l < "$STAGE/fill_ledger.jsonl" 2>/dev/null || echo 0)"
  echo "note: jsonl은 append-only 실시간 파일 — 스냅샷 시점 이후 마지막 미플러시 행은 제외될 수 있음"
  echo "--- sha256 ---"
  sha256sum "$STAGE"/liquidations.db "$STAGE"/*.jsonl "$STAGE"/*.tar 2>/dev/null
} > "$BK/manifest_$TS.txt" || FAIL=1

# 5) 번들링 + 스테이지 정리
tar -czf "$BK/NEW2_tier1_$TS.tar.gz" -C "$STAGE" . || FAIL=1
SIZE=$(stat -c%s "$BK/NEW2_tier1_$TS.tar.gz" 2>/dev/null || echo 0)
rm -rf "$STAGE"

# 6) 회전 (최근 7개 유지)
ls -1t "$BK"/NEW2_tier1_*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
ls -1t "$BK"/manifest_*.txt      2>/dev/null | tail -n +8 | xargs -r rm -f

# 7) 로그 1행
echo "$TS size=${SIZE} fail=${FAIL} kept=$(ls -1 "$BK"/NEW2_tier1_*.tar.gz 2>/dev/null | wc -l)" >> "$BK/log.md"

if [ "$FAIL" != "0" ]; then
  echo "SNAPSHOT_FAILED $TS" >&2
  exit 2
fi
echo "SNAPSHOT_OK $TS size=$SIZE"
