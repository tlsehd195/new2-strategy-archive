#!/bin/bash
# ============================================================
# NEW2 GitHub 오프사이트 백업 push (Task 45)
# - Deploy Key 인증 (개인키는 샌드박스에만, gitssh.js 전송기 사용)
# - TIER1(대체 불가 데이터)만 allow-list 방식으로 수집 — 비밀물(deploy_key 등) 제외
# - 매일 자동 실행: 백업 cron(Job)이 본 스크립트를 호출
# 사용: bash /home/z/my-project/NEW2_run/git_backup_push.sh
# ============================================================
set -u
cd /home/z/my-project || exit 1

REPO=/home/z/my-project/NEW2_gitbackup
STAGE=/tmp/gitbk_stage
REMOTE="git@github.com:tlsehd195/new2-strategy-archive.git"
TS=$(date -u +%Y%m%dT%H%M%SZ)

export GIT_SSH_COMMAND="bun /home/z/my-project/NEW2_run/gitssh/gitssh.js"
export GITSSH_KEY=/home/z/my-project/NEW2_run/gitssh/deploy_key

FAIL=0

# 0) SQLite 일관성 스냅샷 (활성 writer와 무관)
rm -rf "$STAGE"; mkdir -p "$STAGE"
if ! bun -e "const {Database}=require('bun:sqlite'); const d=new Database('mini-services/liq-collector/data/liquidations.db',{readonly:true}); d.exec(\"VACUUM INTO '/tmp/gitbk_stage/liquidations.db'\"); console.log('ROWS='+d.query('SELECT COUNT(*) n FROM liquidations').get().n);" > "$STAGE/db_meta.txt" 2>&1; then
  echo "GITBK_ERR: liquidations.db 스냅샷 실패" >&2; exit 2
fi
DBROWS=$(grep -o 'ROWS=[0-9]*' "$STAGE/db_meta.txt" | cut -d= -f2)

# 1) 리포 트리 재구성 (allow-list — 무엇을 넣을지 명시. node_modules·개인키·토큰 원천 차단)
mkdir -p "$REPO"/{data,results,research,run,docs,src}
cp -f "$STAGE/liquidations.db" "$REPO/data/" || FAIL=1
cp -f mini-services/fng-watcher/data/fng_ledger.jsonl         "$REPO/data/" 2>/dev/null || FAIL=1
cp -f mini-services/maker-fill-monitor/data/fill_ledger.jsonl "$REPO/data/" 2>/dev/null
cp -rf NEW2_run/results/.   "$REPO/results/"   2>/dev/null || FAIL=1
cp -rf NEW2_run/research/.  "$REPO/research/"  2>/dev/null || FAIL=1
cp -f NEW2_run/PREREG_*.md  "$REPO/run/"       2>/dev/null || FAIL=1
cp -f NEW2_run/*.md         "$REPO/run/"       2>/dev/null
cp -f NEW2_run/backup_snapshot.sh NEW2_run/git_backup_push.sh NEW2_run/gitssh/gitssh.js NEW2_run/gitssh/deploy_key.pub NEW2_run/gitssh/genkey_ssh2.js "$REPO/run/" 2>/dev/null
cp -f worklog.md NEW2_전략탐색_종합정리.md NEW2_분석보고서.md 전략리서치_보고서.md strategy_research_v2.md "$REPO/docs/" 2>/dev/null || FAIL=1
cp -f NEW2_전략연구_보고서_v*.md "$REPO/docs/"  2>/dev/null || FAIL=1
cp -f src/instrumentation.ts prisma/schema.prisma "$REPO/src/" 2>/dev/null || FAIL=1
cp -f mini-services/liq-collector/index.ts         "$REPO/src/liq_collector_index.ts"         2>/dev/null
cp -f mini-services/liq-collector/package.json     "$REPO/src/liq_collector_package.json"     2>/dev/null
cp -f mini-services/fng-watcher/index.ts           "$REPO/src/fng_watcher_index.ts"           2>/dev/null
cp -f mini-services/fng-watcher/package.json       "$REPO/src/fng_watcher_package.json"       2>/dev/null
cp -f mini-services/maker-fill-monitor/index.ts    "$REPO/src/maker_fill_monitor_index.ts"    2>/dev/null
cp -f mini-services/maker-fill-monitor/package.json "$REPO/src/maker_fill_monitor_package.json" 2>/dev/null
rm -rf "$STAGE"

# 개인키·민감물 이중 차단 (만약 섞이면 즉시 중단)
if [ -f "$REPO/run/deploy_key" ] || [ -f "$REPO/data/deploy_key" ] || ls "$REPO"/.github_token >/dev/null 2>&1; then
  echo "GITBK_ERR: 민감물 혼입 감지 — 중단" >&2; exit 3
fi

# 2) git init / 커밋
cd "$REPO" || exit 2
if [ ! -d .git ]; then git init -b main -q; fi
git config user.name "NEW2 Backup Bot"
git config user.email "new2-backup@local"
git remote remove origin 2>/dev/null
git remote add origin "$REMOTE"
git add -A
NFILES=$(git diff --cached --name-only HEAD 2>/dev/null | wc -l)
if ! git commit -q -m "TIER1 backup $TS (db=$DBROWS rows)" --allow-empty; then FAIL=1; fi

# 3) push (빈 원격 → 직행; 원격에 히스토리가 생긴 경우 병합 후 재시도)
PUSHOUT=$(git push -u origin main 2>&1)
if ! echo "$PUSHOUT" | grep -qE "main|up-to-date"; then
  git fetch origin main 2>/dev/null && \
  git merge -q --allow-unrelated-histories -X ours origin/main -m "merge remote" 2>/dev/null && \
  PUSHOUT=$(git push -u origin main 2>&1) || FAIL=1
fi
echo "$PUSHOUT" | tail -2

if [ "$FAIL" != "0" ]; then
  echo "GITBK_FAILED $TS" >&2
  echo "$TS push=FAIL fail=1 db=$DBROWS" >> NEW2_run/backup/git_log.md
  exit 2
fi

# 4) 로그 1행 + 요약
NCOMMIT=$(git rev-list --count HEAD)
echo "$TS push=OK db=$DBROWS commits=$NCOMMIT" >> NEW2_run/backup/git_log.md
echo "GITBK_OK $TS db=$DBROWS files_changed=$NFILES commits=$NCOMMIT"
