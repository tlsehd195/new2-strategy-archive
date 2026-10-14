#!/bin/bash
# ============================================================
# NEW2 TIER1 오프사이트 백업 — GitHub tlsehd195/new2-strategy-archive
# - 인증: Deploy Key (ssh2 기반 GIT_SSH 전송기, 개인키 채팅/외부 미노출)
# - 내용: 수집 장부 3종(VACUUM 일관성) + results + research + PREREG + 문서 전체 + 소스
# - 성공 시 출력: GITBK_OK, 실패 시: GITBK_FAILED
# 사용: bash /home/z/my-project/NEW2_run/git_backup_push.sh
# ============================================================
set -u
cd /home/z/my-project || exit 1

TS=$(date -u +%Y%m%dT%H%M%SZ)
REPO=/home/z/my-project/NEW2_gitbackup
BLOG=NEW2_run/backup/git_log.md
export GIT_SSH_COMMAND="bun /home/z/my-project/NEW2_run/gitssh/gitssh.js"
export GITSSH_KEY=/home/z/my-project/NEW2_run/gitssh/deploy_key
FAIL=0

# 0) repo 초기화 (최초 1회: 원격 main이 있으면 그 위에 적층)
if [ ! -d "$REPO/.git" ]; then
  mkdir -p "$REPO"
  git init -b main "$REPO" > /dev/null || FAIL=1
  git -C "$REPO" remote add origin git@github.com:tlsehd195/new2-strategy-archive.git 2>/dev/null
  if git -C "$REPO" fetch origin main > /dev/null 2>&1; then
    git -C "$REPO" reset --hard FETCH_HEAD > /dev/null 2>&1
  fi
fi

# 1) SQLite 일관성 스냅샷 (활성 writer와 무관)
mkdir -p "$REPO/ledgers" "$REPO/results" "$REPO/research" "$REPO/prereg" "$REPO/docs" "$REPO/src/mini_services"
if ! bun -e "const {Database}=require('bun:sqlite'); const d=new Database('mini-services/liq-collector/data/liquidations.db',{readonly:true}); d.exec(\"VACUUM INTO 'NEW2_gitbackup/ledgers/liquidations.db'\"); console.log(d.query('SELECT COUNT(*) n FROM liquidations').get().n)" > /tmp/gitbk_rows.txt 2>&1; then
  echo "ERROR: liquidations.db VACUUM INTO 실패" >&2; FAIL=1
fi
ROWS=$(cat /tmp/gitbk_rows.txt 2>/dev/null || echo UNKNOWN)
cp -f mini-services/fng-watcher/data/fng_ledger.jsonl "$REPO/ledgers/" 2>/dev/null || FAIL=1
cp -f mini-services/maker-fill-monitor/data/fill_ledger.jsonl "$REPO/ledgers/" 2>/dev/null

# 2) 트리 동기화 (TIER1 전부)
cp -rf NEW2_run/results/.   "$REPO/results/"   2>/dev/null || FAIL=1
cp -rf NEW2_run/research/.  "$REPO/research/"  2>/dev/null || FAIL=1
cp -f  NEW2_run/PREREG_*.md "$REPO/prereg/"    2>/dev/null || FAIL=1
cp -f  NEW2_run/*.md        "$REPO/docs/"      2>/dev/null
cp -f  worklog.md HANDOVER_NEXT_SESSION.md NEW2_전략탐색_종합정리.md NEW2_전략연구_보고서_v*.md NEW2_분석보고서.md 전략리서치_보고서.md strategy_research_v2.md "$REPO/docs/" 2>/dev/null
cp -f  src/instrumentation.ts prisma/schema.prisma "$REPO/src/" 2>/dev/null
for s in liq-collector fng-watcher maker-fill-monitor; do
  mkdir -p "$REPO/src/mini_services/$s"
  cp -f mini-services/$s/index.ts mini-services/$s/package.json "$REPO/src/mini_services/$s/" 2>/dev/null
done
cp -f NEW2_run/backup_snapshot.sh NEW2_run/git_backup_push.sh "$REPO/src/" 2>/dev/null
# 자율탐색 엔진 (engine-discovery-v1) — 상태·장부·문법 전체 포함
mkdir -p "$REPO/run/discovery"
cp -rf NEW2_run/discovery/. "$REPO/run/discovery/" 2>/dev/null || FAIL=1

# 3) 커밋 + push
cd "$REPO" || exit 1
git add -A
MSG="TIER1 backup $TS (liq=$ROWS fng=$(wc -l < ledgers/fng_ledger.jsonl 2>/dev/null || echo 0) fill=$(wc -l < ledgers/fill_ledger.jsonl 2>/dev/null || echo 0))"
if git diff --cached --quiet; then
  COMMIT="NO_CHANGES"
else
  git commit -m "$MSG" > /dev/null 2>&1 || FAIL=1
  COMMIT=$(git rev-parse --short HEAD 2>/dev/null)
  if ! git push -u origin main > /dev/null 2>&1; then
    # 원격 선행 커밋 존재 시 rebase 후 재시도 1회
    if git pull --rebase origin main > /dev/null 2>&1 && git push -u origin main > /dev/null 2>&1; then
      COMMIT=$(git rev-parse --short HEAD 2>/dev/null)
    else
      FAIL=1
    fi
  fi
fi

# 4) 로그 + 결과
if [ "$FAIL" = "0" ]; then
  echo "$TS push=OK commit=$COMMIT $MSG" >> "/home/z/my-project/$BLOG"
  echo "GITBK_OK $TS commit=$COMMIT"
else
  echo "$TS push=FAILED commit=${COMMIT:-NONE} $MSG" >> "/home/z/my-project/$BLOG"
  echo "GITBK_FAILED $TS"
fi
