// ssh2 네이티브 키 생성 (OpenSSH 형식, 변환 불필요)
const fs = require('fs');
const ssh2 = require('/home/z/my-project/NEW2_run/gitssh/node_modules/ssh2');

try {
  const pair = ssh2.utils.generateKeyPairSync('ed25519', { comment: 'new2-t1-backup' });
  const priv = typeof pair.private === 'string' ? pair.private : pair.private.toString('utf8');
  const pub = typeof pair.public === 'string' ? pair.public : pair.public.toString('utf8');

  // 개인키는 절대 출력하지 않음 — 헤더만 확인
  console.log('PRIV_HEADER=' + priv.split('\n')[0]);
  fs.writeFileSync('/home/z/my-project/NEW2_run/gitssh/deploy_key', priv);
  fs.chmodSync('/home/z/my-project/NEW2_run/gitssh/deploy_key', 0o600);
  fs.writeFileSync('/home/z/my-project/NEW2_run/gitssh/deploy_key.pub', pub.endsWith('\n') ? pub : pub + '\n');

  // 파싱·키쌍 일치 검증
  const parsed = ssh2.utils.parseKey(fs.readFileSync('/home/z/my-project/NEW2_run/gitssh/deploy_key'));
  if (parsed instanceof Error) { console.log('PARSE_FAIL: ' + parsed.message); process.exit(1); }
  const derived = Buffer.from(parsed.getPublicSSH());
  const parts = pub.trim().split(' ');
  const diskPubBlob = Buffer.from(parts[1], 'base64');
  console.log('PARSE_OK type=' + parsed.type);
  console.log('KEYPAIR_MATCH=' + derived.equals(diskPubBlob));
  console.log('PUBKEY_LINE_REBUILD=ssh-ed25519 ' + derived.toString('base64') + ' new2-t1-backup');
} catch (e) {
  console.log('GEN_FAIL: ' + (e && e.message ? e.message : String(e)));
}
