"""Download Binance SPOT 15m monthly klines for 2017-08..2019-12 (public, keyless).

Purpose (ADR 0059): a block of real market data that this archive has never
read (engine READ_START is 2021-01-01).  This tool ONLY downloads, verifies the
published SHA-256 CHECKSUM, and counts rows / gaps / duplicates.  It computes no
return, volatility or correlation statistic, so the block stays unread.

  python3 fetch_spot_pre2020.py --out DIR [--symbols A,B] [--workers 8]
"""
from __future__ import annotations
import argparse, time, concurrent.futures as cf, hashlib, io, json, re, subprocess, sys, zipfile
from pathlib import Path

B = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
PRE = "data/spot/monthly/klines/"
LAST = "2019-12"
STABLES = {"TUSDUSDT", "PAXUSDT", "USDCUSDT", "USDSUSDT", "USDSBUSDT", "BUSDUSDT"}
DEAD = {"BCCUSDT", "VENUSDT", "BCHABCUSDT", "BCHSVUSDT", "MITHUSDT", "NPXSUSDT", "STORMUSDT", "HCUSDT", "MCOUSDT"}


def curl(url: str, binary: bool = False):
    err = ""
    for k in range(5):  # transient proxy resets: retry with backoff
        r = subprocess.run(["curl", "-sS", "-L", "-m", "60", url], capture_output=True)
        if r.returncode == 0:
            return r.stdout if binary else r.stdout.decode()
        err = r.stderr.decode()[:200]
        time.sleep(2 ** k)
    raise RuntimeError(err)


def list_months(sym: str) -> list[str]:
    x = curl(f"{B}?prefix={PRE}{sym}/15m/&max-keys=1000")
    ms = sorted(set(re.findall(rf"{sym}-15m-(\d{{4}}-\d{{2}})\.zip<", x)))
    return [m for m in ms if m <= LAST]


def one(args):
    sym, month, out = args
    name = f"{sym}-15m-{month}"
    dst = out / "klines" / f"{name}.csv"
    if dst.exists():
        return sym, month, "EXISTS", None
    z = curl(f"{B}/{PRE}{sym}/15m/{name}.zip", True)
    chk = curl(f"{B}/{PRE}{sym}/15m/{name}.zip.CHECKSUM").split()
    if not chk or hashlib.sha256(z).hexdigest() != chk[0]:
        return sym, month, "CHECKSUM_FAIL", None
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = zf.namelist()
        if len(names) != 1:
            return sym, month, "BAD_ZIP", None
        raw = zf.read(names[0])
    # integrity only: row count, 15-minute spacing gaps, duplicates (open time = column 0)
    ts = [int(l.split(b",", 1)[0]) for l in raw.splitlines() if l.strip()]
    ts = [t // 1000 if t > 10**14 else t for t in ts]  # us -> ms, none expected pre-2020
    gaps = sum(1 for a, b in zip(ts, ts[1:]) if b - a != 900_000)
    dup = len(ts) - len(set(ts))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(raw)
    info = {"rows": len(ts), "gaps": gaps, "dups": dup, "sha256": hashlib.sha256(raw).hexdigest()}
    return sym, month, "OK", info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--first-month-max", default="2019-03", help="include symbols whose first spot month is <= this")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    out = Path(a.out)
    if a.symbols:
        syms = a.symbols.split(",")
    else:
        allsyms = [s.rstrip("/").split("/")[-1] for s in re.findall(rf"<Prefix>({PRE}[A-Z0-9]+USDT/)</Prefix>", curl(f"{B}?delimiter=/&prefix={PRE}&max-keys=1000"))]
        raise SystemExit("pass --symbols (listing is paged; see syms list in the ADR)")
    jobs = []
    for s in syms:
        for m in list_months(s):
            jobs.append((s, m, out))
    print(f"{len(syms)} symbols, {len(jobs)} files", file=sys.stderr)
    rows = []
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for sym, month, st, info in ex.map(one, jobs):
            rows.append({"symbol": sym, "month": month, "status": st, **(info or {})})
    (out / "_dq_pre2020_spot.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    bad = [r for r in rows if r["status"] not in ("OK", "EXISTS")]
    print(json.dumps({"files": len(rows), "bad": len(bad)}))
    for r in bad[:20]:
        print(r, file=sys.stderr)


if __name__ == "__main__":
    main()
