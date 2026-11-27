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
 *
 * ADR 0116 (지시문 §6-2): 특정 개발 환경의 홈 디렉터리 절대경로(옛 mini-services 위치)를 없앴다.
 *  - 서비스 폴더의 부모 = NEW2_MINI_SERVICES_DIR(절대 또는 작업 디렉터리 기준 상대경로),
 *    없으면 <작업 디렉터리>/mini-services. 서비스별로 NEW2_<NAME>_DIR로 따로 줄 수도 있다.
 *    경로 조합은 node:path로 해 Linux와 Windows 모두에서 맞다.
 *  - 스폰 전에 폴더·index.ts·package.json이 있는지 확인한다. 없으면 "missing"으로 기록하고 스폰하지 않는다.
 *  - 실행 파일(bun)이 없으면 spawn의 error 이벤트를 잡아 "failed"로 기록한다(서버를 죽이지 않음).
 *    실행 파일은 NEW2_BUN_BIN으로 바꿀 수 있다.
 */
import { existsSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";

export const SERVICE_DEFS = [
  { name: "p3-liq-collector", port: 3020, folder: "liq-collector", env: "NEW2_LIQ_COLLECTOR_DIR" },
  { name: "p3-fng-watcher", port: 3021, folder: "fng-watcher", env: "NEW2_FNG_WATCHER_DIR" },
  { name: "p3-maker-fill-monitor", port: 3022, folder: "maker-fill-monitor", env: "NEW2_MAKER_FILL_MONITOR_DIR" },
] as const;

export interface Service { name: string; port: number; dir: string; }

type Env = Record<string, string | undefined>;

function absolute(p: string, cwd: string): string {
  return isAbsolute(p) ? p : resolve(cwd, p);
}

/** 설정과 작업 디렉터리로부터 서비스 폴더를 정한다 (하드코딩 경로 없음). */
export function resolveServices(env: Env, cwd: string): Service[] {
  const root = absolute(env.NEW2_MINI_SERVICES_DIR?.trim() || "mini-services", cwd);
  return SERVICE_DEFS.map((d) => ({
    name: d.name, port: d.port,
    dir: env[d.env]?.trim() ? absolute(env[d.env]!.trim(), cwd) : join(root, d.folder),
  }));
}

/** 스폰할 수 있는 서비스 폴더인가. 문제가 있으면 이유(문자열)를 돌려준다. */
export function checkServiceDir(dir: string, exists: (p: string) => boolean = existsSync): string | null {
  if (!exists(dir)) return `service directory not found: ${dir}`;
  for (const f of ["index.ts", "package.json"]) {
    if (!exists(join(dir, f))) return `missing ${f} in ${dir}`;
  }
  return null;
}

export async function ensureService(
  svc: Service,
  spawnFn: (cmd: string, args: string[], opts: any) => any,
  dynImport: (m: string) => Promise<any>,
  opts: { bunBin?: string; fetchFn?: typeof fetch; exists?: (p: string) => boolean } = {},
): Promise<"alive" | "spawned" | "missing" | "failed"> {
  const fetchFn = opts.fetchFn ?? fetch;
  const alive = await fetchFn(`http://127.0.0.1:${svc.port}/stats`, { signal: AbortSignal.timeout(500) })
    .then((r) => r.ok)
    .catch(() => false);
  if (alive) return "alive";
  const problem = checkServiceDir(svc.dir, opts.exists);
  if (problem) {
    console.error(`[instrumentation] ${svc.name} not spawned: ${problem}`);
    return "missing";
  }
  try {
    const { open } = await dynImport("node:fs/promises");
    const logPath = join(svc.dir, "service.log");
    // append 플래그로 기존 로그 보존 — 사망 이력 진단 가능
    const fh = await open(logPath, "a");
    const child = spawnFn(opts.bunBin ?? "bun", ["run", "dev"], {
      cwd: svc.dir,
      detached: true,
      stdio: ["ignore", fh.fd, fh.fd],
    });
    // 실행 파일이 없으면(ENOENT) spawn은 던지지 않고 error 이벤트를 낸다 — 잡아서 기록
    child.on?.("error", (e: unknown) => console.error(`[instrumentation] ${svc.name} spawn error:`, e));
    child.unref();
    await fh.close(); // 부모 쪽 핸들만 닫음 — 자식은 fd를 상속해 계속 기록
    if (!child.pid) {
      console.error(`[instrumentation] ${svc.name} spawn failed: no pid (is ${opts.bunBin ?? "bun"} installed?)`);
      return "failed";
    }
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
    const services = resolveServices(process.env, process.cwd());
    const bunBin = process.env.NEW2_BUN_BIN?.trim() || "bun";

    for (const svc of services) {
      await ensureService(svc, spawn, dynImport, { bunBin });
    }

    // 주기 워치독 — 서버 프로세스 수명 동안 자기 치유
    const watcher = setInterval(async () => {
      for (const svc of services) {
        try {
          const st = await ensureService(svc, spawn, dynImport, { bunBin });
          if (st === "spawned") console.log(`[instrumentation] watchdog revived ${svc.name}`);
        } catch (e) {
          console.error(`[instrumentation] watchdog error for ${svc.name}:`, e);
        }
      }
    }, 5 * 60 * 1000);
    watcher.unref();
  } catch (e) {
    console.error("[instrumentation] register failed:", e);
  }
}
