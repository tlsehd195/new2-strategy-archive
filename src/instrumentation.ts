/**
 * Next.js instrumentation hook — P3 인프라 감독 스폰 (Task 37-a/37-d).
 *
 * 배경: 에이전트 세션에서 시작한 백그라운드 프로세스는 세션 종료 시 회수되며,
 * 수집기(3020)·F&G 감시기(3021)의 장기 누적은 영속 프로세스 트리(Next.js dev 서버)에 붙어야 한다.
 *
 * 구조(3중 방어):
 *  1) 부팅 스폰: register()가 서버 부팅 시 각 포트 헬스체크 → 비어 있으면 detached 자식으로 스폰.
 *  2) 주기 워치독: 5분마다 헬스체크 → 자식이 죽어 있으면 재스폰 (서버가 살아 있는 한 자기 치유).
 *  3) 로그 파일: 자식 stdout/stderr를 서비스 폴더의 service.log로 기록 — 무음 사망 진단 가능.
 *
 * 중복 스폰 방지: 포트 헬스체크(500ms)로 살아 있으면 스킵. detached+unref로 서버와 독립 수명.
 * 주의: register() 내 조기 return 금지 — 뒤 서비스 스폰이 차단되는 버그(Task 37-d 발견) 방지.
 */
const SERVICES = [
  { name: "p3-liq-collector", port: 3020, dir: "/home/z/my-project/mini-services/liq-collector" },
  { name: "p3-fng-watcher", port: 3021, dir: "/home/z/my-project/mini-services/fng-watcher" },
  { name: "p3-maker-fill-monitor", port: 3022, dir: "/home/z/my-project/mini-services/maker-fill-monitor" },
] as const;

async function ensureService(
  svc: (typeof SERVICES)[number],
  spawnFn: (cmd: string, args: string[], opts: any) => any,
  dynImport: (m: string) => Promise<any>
) {
  const alive = await fetch(`http://127.0.0.1:${svc.port}/stats`, { signal: AbortSignal.timeout(500) })
    .then((r) => r.ok)
    .catch(() => false);
  if (alive) return "alive";
  try {
    const { open } = await dynImport("node:fs/promises");
    const logPath = `${svc.dir}/service.log`;
    // append 플래그로 기존 로그 보존 — 사망 이력 진단 가능
    const fh = await open(logPath, "a");
    const child = spawnFn("bun", ["run", "dev"], {
      cwd: svc.dir,
      detached: true,
      stdio: ["ignore", fh.fd, fh.fd],
    });
    child.unref();
    await fh.close(); // 부모 쪽 핸들만 닫음 — 자식은 fd를 상속해 계속 기록
    console.log(`[instrumentation] ${svc.name} spawned (pid ${child.pid}) — ${logPath}`);
    return "spawned";
  } catch (e) {
    console.error(`[instrumentation] ${svc.name} spawn failed:`, e);
    return "failed";
  }
}

export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  try {
    // 정적 분석 차단: node: 모듈을 edge 번들에서 경고 없이 사용하기 위한 간접 import
    const dynImport = new Function("m", "return import(m)") as (m: string) => Promise<any>;
    const { spawn } = await dynImport("node:child_process");

    for (const svc of SERVICES) {
      await ensureService(svc, spawn, dynImport);
    }

    // 주기 워치독 — 서버 프로세스 수명 동안 자기 치유
    const watcher = setInterval(async () => {
      for (const svc of SERVICES) {
        try {
          const st = await ensureService(svc, spawn, dynImport);
          if (st === "spawned") console.log(`[instrumentation] watchdog revived ${svc.name}`);
        } catch {}
      }
    }, 5 * 60 * 1000);
    watcher.unref();
  } catch (e) {
    console.error("[instrumentation] register failed:", e);
  }
}
