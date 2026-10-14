#!/usr/bin/env bun
// Deploy Key 생성기 (ssh2 네이티브 — PKCS#8 미지원 문제 회피, OpenSSH 형식 출력)
// 사용: bun genkey_ssh2.js [comment]
import { createRequire } from "module";
import { writeFileSync } from "fs";
const require = createRequire(import.meta.url);
const { generateKeyPairSync } = require("ssh2").utils;

const comment = process.argv[2] || "new2-t1-backup-r2";
const { public: pub, private: priv } = generateKeyPairSync("ed25519");

writeFileSync("deploy_key", priv.endsWith("\n") ? priv : priv + "\n", { mode: 0o600 });
// 공개키는 "ssh-ed25519 AAAA... [comment]" 형태로 정규화
const pubBody = pub.trim().split(/\s+/).slice(0, 2).join(" ");
writeFileSync("deploy_key.pub", `${pubBody} ${comment}\n`, { mode: 0o644 });
console.log("KEYGEN_OK ed25519 comment=" + comment);
