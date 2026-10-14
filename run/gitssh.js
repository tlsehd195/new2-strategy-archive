#!/usr/bin/env bun
// GIT_SSH 호환 전송기 (ssh2 기반 — openssh-client 없는 샌드박스용)
// git은 이 스크립트를 `gitssh.js <[user@]host> "<git-command>"` 형태로 호출한다.
// 환경변수:
//   GITSSH_KEY  : 개인키 경로 (OpenSSH ed25519 형식)
//   KNOWN_FP    : github.com Ed25519 호스트키 SHA256 핑거프린트(TOFU 고정값)
// 보안: 호스트키 핑거프린트 불일치 시 즉시 종료(exit 255) — MITM 차단
import { Client } from "ssh2";
import { readFileSync } from "fs";
import { createHash } from "crypto";

const target = process.argv[2] || "";
const cmd = process.argv.slice(3).join(" ");
const keyPath = process.env.GITSSH_KEY;
const fpPin = (process.env.KNOWN_FP || "").trim();

if (!target || !cmd || !keyPath || !fpPin) {
  console.error("gitssh: usage gitssh.js <[user@]host> <cmd> (+GITSSH_KEY, KNOWN_FP)");
  process.exit(255);
}
const at = target.indexOf("@");
const username = at >= 0 ? target.slice(0, at) : "git";
const host = at >= 0 ? target.slice(at + 1) : target;
const privateKey = readFileSync(keyPath);

const conn = new Client({
  hostVerifier: (hk) => {
    // ssh2가 넘겨주는 호스트키 버퍼의 SHA256 base64 핑거프린트 비교
    const fp = "SHA256:" + createHash("sha256").update(hk).digest("base64");
    if (fp !== fpPin) {
      console.error(`gitssh: HOST KEY MISMATCH got=${fp} want=${fpPin}`);
      return false;
    }
    return true;
  },
});

conn.on("error", (e) => {
  console.error("gitssh: connect error: " + (e && e.message ? e.message : e));
  process.exit(255);
});

conn.on("ready", () => {
  conn.exec(cmd, { pty: false }, (err, stream) => {
    if (err) {
      console.error("gitssh: exec error: " + err.message);
      process.exit(255);
    }
    let code = 0;
    // stdin → 원격
    process.stdin.on("data", (d) => stream.write(d));
    process.stdin.on("end", () => stream.end());
    // 원격 → stdout/stderr (backpressure는 bun/node 버퍼가 처리)
    stream.on("data", (d) => process.stdout.write(d));
    stream.stderr.on("data", (d) => process.stderr.write(d));
    // exit: 종료 코드 기록 / close: 모든 데이터 플러시 완료 후 종료
    stream.on("exit", (c, signal) => {
      code = signal ? 128 : (c == null ? 0 : c);
    });
    stream.on("close", () => {
      conn.end();
      setTimeout(() => process.exit(code), 200); // stdout 플러시 유예
    });
  });
});

conn.connect({ host, username, privateKey, readyTimeout: 30000, keepaliveInterval: 10000 });
