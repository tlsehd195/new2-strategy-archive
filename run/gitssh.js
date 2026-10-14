#!/usr/bin/env bun
// GIT_SSH 대체 전송기 (ssh2 라이브러리 기반)
// git은 GIT_SSH_COMMAND를 ssh처럼 호출: <본 스크립트> [옵션들] [user@]host 'git-upload-pack ...'
// 개인키: GITSSH_KEY 환경변수 또는 기본 경로. 호스트키는 TOFU(첫 접속 시 핑거프린트 파일에 고정).
const fs = require('fs');
const crypto = require('crypto');
const { Client } = require('/home/z/my-project/NEW2_run/gitssh/node_modules/ssh2');

const args = process.argv.slice(2);
const opts = [], rest = [];
for (const a of args) { if (a.startsWith('-')) opts.push(a); else rest.push(a); }
if (rest.length < 1) { console.error('gitssh: 인수 부족'); process.exit(255); }
const hostPart = rest[0];
const cmd = rest.slice(1).join(' ');
let user = 'git', host = hostPart;
if (hostPart.includes('@')) { [user, host] = hostPart.split('@'); }

const keyPath = process.env.GITSSH_KEY || '/home/z/my-project/NEW2_run/gitssh/deploy_key';
const khPath = '/home/z/my-project/NEW2_run/gitssh/known_hosts_fp';

const conn = new Client();
conn.on('error', (e) => { console.error('SSH_ERR: ' + e.message); process.exit(255); });
conn.on('ready', () => {
  conn.exec(cmd, (err, stream) => {
    if (err) { console.error('EXEC_ERR: ' + err.message); process.exit(255); }
    process.stdin.on('end', () => { try { stream.end(); } catch {} });
    process.stdin.on('data', (d) => { try { stream.write(d); } catch {} });
    stream.on('data', (d) => process.stdout.write(d));
    stream.stderr.on('data', (d) => process.stderr.write(d));
    stream.on('exit', (code) => { conn.end(); process.exit(code ?? 0); });
    stream.on('close', () => { try { conn.end(); } catch {}; process.exit(0); });
  });
}).connect({
  host,
  port: 22,
  username: user,
  privateKey: fs.readFileSync(keyPath),
  hostHash: 'sha256',
  hostVerifier: (hkeyHex) => {
    let prev = '';
    try { prev = fs.readFileSync(khPath, 'utf8').trim(); } catch {}
    const cur = host + ' ' + hkeyHex;
    if (!prev) { fs.writeFileSync(khPath, cur + '\n', { mode: 0o600 }); return true; }
    return prev === cur;
  },
  readyTimeout: 30000,
  keepaliveInterval: 10000,
});
