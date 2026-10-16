"""Dataset manifest: provenance, integrity and quality of a data directory.

One JSON document per data directory that answers, file by file and
symbol by symbol: where did it come from (``_provenance.jsonl``), is it the
same file (SHA-256 against provenance and against an earlier manifest), is
it usable (the engine's own quality gate, plus checks that need the whole
directory), and over which time range.  Nothing is repaired, dropped or
overwritten — every problem is reported with its file, symbol and row
counts, and a blocking problem makes the manifest ``ok = false``.

Checks beyond ``data_quality.check_bars`` (per file):

=========================  ========  =========================================
code                       severity  meaning
=========================  ========  =========================================
CLOSE_TIME_MISMATCH        BLOCK     column 7 is not open + 15m - 1 ms (wrong
                                     interval, shifted or spliced file)
MONTH_BOUNDARY_MISMATCH    BLOCK     a monthly file holds bars outside its month
                                     (e.g. a time-zone shifted export)
INCOMPLETE_MONTH           WARN      a finished month without its first/last bar
INCOMPLETE_BAR             BLOCK     a bar whose close time is after "now"
FUTURE_FUNDING             BLOCK     a funding settlement stamped after "now"
FUNDING_OFF_SCHEDULE       WARN      settlement not within 60 s of the 8h grid
PROVENANCE_MISMATCH        BLOCK     file hash differs from its provenance record
NO_PROVENANCE              WARN      file without a provenance record
CHANGED_SINCE_MANIFEST     BLOCK     file hash differs from the reference manifest
CROSS_MONTH_GAP            BLOCK     last bar of month m + 15m != first of m+1
=========================  ========  =========================================

Per directory: symbol ranges, kline/funding coverage per symbol, and a
cross-symbol range comparison (symbols covering different periods are
reported, never silently aligned).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

MANIFEST_SCHEMA = "dataset_manifest_v1"
MS15 = 900_000
H8 = 8 * 3_600_000
_KFILE = re.compile(r"^(?P<sym>[A-Z0-9]+)-15m-(?P<y>\d{4})-(?P<m>\d{2})(?:-(?P<d>\d{2}))?\.csv$")
_FFILE = re.compile(r"^(?P<sym>[A-Z0-9]+)-fundingRate-(?P<y>\d{4})-(?P<m>\d{2})\.csv$")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _month_bounds(y: int, m: int) -> tuple[int, int]:
    a = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    b = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.timezone.utc)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def _code_version() -> str:
    h = hashlib.sha256()
    for name in ("data_manifest.py", "data_quality.py", "data_fetch.py"):
        h.update((Path(__file__).resolve().parent / name).read_bytes())
    return h.hexdigest()[:16]


def _provenance(data_dir: Path) -> dict:
    out: dict[str, dict] = {}
    p = data_dir / "_provenance.jsonl"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        rel = r["path"].replace("\\", "/")
        for sub in ("klines/", "funding/"):
            if f"/{sub}" in rel or rel.startswith(sub):
                rel = sub + rel.rsplit(sub, 1)[1]
        out[rel] = r
    return out


def _close_time_mismatches(path: Path) -> int:
    bad = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(",")
        if len(parts) < 7 or not parts[0].strip().isdigit():
            continue
        try:
            if int(parts[6]) != int(parts[0]) + MS15 - 1:
                bad += 1
        except ValueError:
            continue
    return bad


def build_manifest(data_dir: Path, *, now_ms: Optional[int] = None,
                   reference: Optional[dict] = None) -> dict:
    from data_quality import (QUALITY_POLICY_VERSION, exceptions_identity, funding_quality,
                              load_funding_csv_checked, load_kline_csv)
    data_dir = Path(data_dir)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    prov = _provenance(data_dir)
    ref_files = {f["path"]: f["sha256"] for f in (reference or {}).get("files", [])}
    files, issues = [], []
    sym_k: dict[str, list] = {}
    sym_f: dict[str, list] = {}

    def issue(code, severity, path, symbol, detail, **kw):
        issues.append({"code": code, "severity": severity, "path": path, "symbol": symbol,
                       "detail": detail, **kw})

    for p in sorted((data_dir / "klines").glob("*.csv")) if (data_dir / "klines").exists() else []:
        rel = f"klines/{p.name}"
        m = _KFILE.match(p.name)
        sha = _sha(p)
        entry = {"path": rel, "kind": "klines", "sha256": sha, "bytes": p.stat().st_size}
        if not m:
            issue("UNRECOGNISED_NAME", "BLOCK", rel, None, "file name does not match <SYM>-15m-YYYY-MM[-DD].csv")
            files.append(entry)
            continue
        sym, y, mo = m["sym"], int(m["y"]), int(m["m"])
        bars, rep = load_kline_csv(p, sym)
        entry.update({"symbol": sym, "period": f"{y:04d}-{mo:02d}" + (f"-{m['d']}" if m["d"] else ""),
                      "rows": rep.n_rows, "first_ts": rep.first_ts, "last_ts": rep.last_ts,
                      "verdict": rep.verdict, "codes": sorted({i["code"] for i in rep.issues}),
                      "missing_bars": rep.missing_candles, "duplicates": rep.duplicate_timestamps,
                      "parse_errors": rep.parse_errors, "torn_tail": rep.torn_tail,
                      "zero_volume": rep.zero_volume, "extreme_moves": rep.extreme_moves})
        for i in rep.issues:
            issue(i["code"], i["severity"], rel, sym, i.get("detail", ""))
        ct = _close_time_mismatches(p)
        if ct:
            issue("CLOSE_TIME_MISMATCH", "BLOCK", rel, sym, f"{ct} rows with close != open+15m-1ms", rows=ct)
        if bars:
            if m["d"]:
                lo = int(dt.datetime(y, mo, int(m["d"]), tzinfo=dt.timezone.utc).timestamp() * 1000)
                hi = lo + 86_400_000
            else:
                lo, hi = _month_bounds(y, mo)
            outside = sum(1 for b in bars if not lo <= b.ts < hi)
            if outside:
                issue("MONTH_BOUNDARY_MISMATCH", "BLOCK", rel, sym,
                      f"{outside} bars outside the file's period", rows=outside)
            if hi <= now_ms and (bars[0].ts != lo or bars[-1].ts != hi - MS15):
                issue("INCOMPLETE_MONTH", "WARN", rel, sym,
                      f"first {bars[0].ts} / last {bars[-1].ts} vs period [{lo}, {hi - MS15}]")
            late = sum(1 for b in bars if b.ts + MS15 > now_ms)
            if late:
                issue("INCOMPLETE_BAR", "BLOCK", rel, sym, f"{late} bars not closed at now={now_ms}", rows=late)
            sym_k.setdefault(sym, []).append((bars[0].ts, bars[-1].ts, rel))
        files.append(entry)

    for p in sorted((data_dir / "funding").glob("*.csv")) if (data_dir / "funding").exists() else []:
        rel = f"funding/{p.name}"
        m = _FFILE.match(p.name)
        sha = _sha(p)
        entry = {"path": rel, "kind": "funding", "sha256": sha, "bytes": p.stat().st_size}
        if not m:
            issue("UNRECOGNISED_NAME", "BLOCK", rel, None, "file name does not match <SYM>-fundingRate-YYYY-MM.csv")
            files.append(entry)
            continue
        sym, y, mo = m["sym"], int(m["y"]), int(m["m"])
        pairs, rep = load_funding_csv_checked(p)
        fq = funding_quality(pairs, parse_errors=rep["parse_errors"], torn_tail=rep["torn_tail"],
                             intervals=rep["intervals"])
        entry.update({"symbol": sym, "period": f"{y:04d}-{mo:02d}", "rows": len(pairs),
                      "verdict": "OK" if fq["ok"] else "BLOCK", "issues": fq["issues"],
                      "warnings": fq["warnings"], "first_ts": pairs[0][0] if pairs else None,
                      "last_ts": pairs[-1][0] if pairs else None})
        for t in fq["issues"]:
            issue("FUNDING_QUALITY", "BLOCK", rel, sym, t)
        lo, hi = _month_bounds(y, mo)
        outside = sum(1 for t, _ in pairs if not lo <= t < hi + 60_000)
        if outside:
            issue("MONTH_BOUNDARY_MISMATCH", "BLOCK", rel, sym, f"{outside} settlements outside the month",
                  rows=outside)
        fut = sum(1 for t, _ in pairs if t > now_ms)
        if fut:
            issue("FUTURE_FUNDING", "BLOCK", rel, sym, f"{fut} settlements after now", rows=fut)
        off = 0
        for t, _ in pairs:
            iv = rep["intervals"].get(t, H8)
            r = t % iv if iv in (H8, 4 * 3_600_000, 3_600_000) else t % H8
            if min(r, iv - r) > 60_000:
                off += 1
        if off:
            issue("FUNDING_OFF_SCHEDULE", "WARN", rel, sym, f"{off} settlements off the funding grid", rows=off)
        if pairs:
            sym_f.setdefault(sym, []).append((pairs[0][0], pairs[-1][0], rel))
        files.append(entry)

    for f in files:
        pr = prov.get(f["path"])
        if pr is None:
            issue("NO_PROVENANCE", "WARN", f["path"], f.get("symbol"), "no provenance record")
            f["provenance"] = None
        else:
            want = pr.get("csv_sha256")
            f["provenance"] = {"status": "MATCH" if want == f["sha256"] else "MISMATCH",
                               "url": pr.get("url"), "fetched_at": pr.get("at"),
                               "zip_sha256": pr.get("zip_sha256")}
            if want != f["sha256"]:
                issue("PROVENANCE_MISMATCH", "BLOCK", f["path"], f.get("symbol"),
                      f"file {f['sha256'][:12]} != provenance {str(want)[:12]}")
        if ref_files and f["path"] in ref_files and ref_files[f["path"]] != f["sha256"]:
            issue("CHANGED_SINCE_MANIFEST", "BLOCK", f["path"], f.get("symbol"),
                  "file differs from the reference manifest — kept, not overwritten")
    for path in sorted(set(ref_files) - {f["path"] for f in files}):
        issue("REMOVED_SINCE_MANIFEST", "BLOCK", path, None, "file listed in the reference manifest is gone")

    symbols = {}
    for sym, spans in sorted(sym_k.items()):
        spans.sort()
        for (a0, a1, ra), (b0, b1, rb) in zip(spans, spans[1:]):
            if b0 - a1 != MS15:
                issue("CROSS_MONTH_GAP", "BLOCK", rb, sym, f"{(b0 - a1) // MS15 - 1} bars between {ra} and {rb}")
        fs = sorted(sym_f.get(sym, []))
        symbols[sym] = {"klines_first_ts": spans[0][0], "klines_last_ts": spans[-1][1],
                        "kline_files": len(spans),
                        "funding_first_ts": fs[0][0] if fs else None, "funding_last_ts": fs[-1][1] if fs else None,
                        "funding_files": len(fs)}
        if not fs:
            issue("NO_FUNDING", "WARN", None, sym, "klines without funding settlements")
        elif fs[0][0] > spans[0][0] + H8 or fs[-1][1] < spans[-1][1] - H8:
            issue("FUNDING_COVERAGE", "WARN", None, sym, "funding does not cover the kline range")
    ranges = {(v["klines_first_ts"], v["klines_last_ts"]) for v in symbols.values()}
    if len(ranges) > 1:
        issue("SYMBOL_RANGE_MISMATCH", "WARN", None, None,
              "symbols cover different periods: " + json.dumps(
                  {s: [v["klines_first_ts"], v["klines_last_ts"]] for s, v in symbols.items()}))
    ds = hashlib.sha256("".join(f"{f['path']}:{f['sha256']}\n" for f in files).encode()).hexdigest()
    blocking = [i for i in issues if i["severity"] == "BLOCK"]
    return {"schema": MANIFEST_SCHEMA, "dataset_sha256": ds, "data_dir": str(data_dir),
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "now_ms": now_ms,
            "code_version": _code_version(), "quality_policy": QUALITY_POLICY_VERSION,
            "exceptions": exceptions_identity(), "n_files": len(files),
            "symbols": symbols, "files": files, "issues": issues,
            "counts": {"blocking": len(blocking), "warnings": len(issues) - len(blocking)},
            "ok": not blocking}


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="build / verify a dataset manifest")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", help="write the manifest here (never overwrites an existing file)")
    ap.add_argument("--reference", help="earlier manifest to compare file hashes against")
    a = ap.parse_args(argv)
    ref = json.loads(Path(a.reference).read_text()) if a.reference else None
    man = build_manifest(Path(a.data_dir), reference=ref)
    if a.out:
        out = Path(a.out)
        if out.exists():
            print(f"REFUSED: {out} exists (manifests are write-once)", file=sys.stderr)
            return 1
        out.write_text(json.dumps(man, indent=1, sort_keys=True))
    print(json.dumps({k: man[k] for k in ("dataset_sha256", "n_files", "counts", "ok", "symbols")},
                     indent=1, sort_keys=True))
    for i in man["issues"]:
        if i["severity"] == "BLOCK":
            print(json.dumps(i), file=sys.stderr)
    return 0 if man["ok"] else 4


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
