"""NEW2 discovery engine v2 — core library.

REBUILD notice: this module is a from-scratch reimplementation of the
strategy-discovery engine (the original lives in the private
``new2-strategy-archive`` repo which is inaccessible from this
environment, see run/audit/AUDIT_REMAINING_ISSUES.md).  It is NOT a
patch of the original and it starts a NEW ledger; old experiments are
never imported, merged, or rewritten.

Design rules enforced here (directive §3-§14):
- explicit candidate state machine with documented transitions
- append-only ledger as the authoritative record (JSONL)
- strategy_id / evaluation_id / experiment_context_id separation
- Holm within candidate, BH-FDR across candidates, trial ledger
- multi-symbol verdicts never pass on a single-symbol pass
- Stouffer without double sqrt(n) weighting and without a rho grid max
- locked TEST periods are read-only and single-use for final tests
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import platform
import socket
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# versions (code identity for experiment_context_id)
# ---------------------------------------------------------------------------

CODE_VERSION = "discovery-v2.2.0"          # human label only — identity is CODE_HASH
VALIDATION_CFG_VERSION = "valcfg-v2.1.0"
LEDGER_SCHEMA = "discovery_ledger_v2"
STATE_SCHEMA = "discovery_state_v2"

# ---------------------------------------------------------------------------
# candidate states and the transition table
# ---------------------------------------------------------------------------


class CandidateState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL_COST = "FAIL_COST"
    FAIL_STAT = "FAIL_STAT"
    UNDETERMINED_POWER = "UNDETERMINED_POWER"
    DATA_MISSING = "DATA_MISSING"
    ERROR = "ERROR"
    REVERIFY_PENDING = "REVERIFY_PENDING"
    #: dev/validation evaluated and dev-PASS, but the cross-candidate FDR
    #: decision of its search generation is not final yet (P0-1).  The
    #: locked TEST is never touched in this state.
    AWAITING_FDR = "AWAITING_FDR"


#: allowed (from, to) transitions.  Any transition not listed (and not the
#: identity) raises InvalidTransition — states cannot be forced by hand.
ALLOWED_TRANSITIONS: dict[CandidateState, set[CandidateState]] = {
    CandidateState.PENDING: {CandidateState.RUNNING},
    CandidateState.RUNNING: {
        CandidateState.PASS,
        CandidateState.FAIL_COST,
        CandidateState.FAIL_STAT,
        CandidateState.UNDETERMINED_POWER,
        CandidateState.DATA_MISSING,
        CandidateState.ERROR,
        CandidateState.AWAITING_FDR,
        # crash recovery: a RUNNING candidate whose process died without a
        # ledger record is moved to the reverify queue by reconcile()
        CandidateState.REVERIFY_PENDING,
    },
    # FDR finalization resolves a waiting candidate to its final state; a
    # context change before finalization sends it back to re-evaluation
    CandidateState.AWAITING_FDR: {
        CandidateState.PASS,
        CandidateState.FAIL_COST,
        CandidateState.FAIL_STAT,
        CandidateState.UNDETERMINED_POWER,
        CandidateState.DATA_MISSING,
        CandidateState.ERROR,
        CandidateState.REVERIFY_PENDING,
    },
    # reverify eligibility is checked by reverify_eligible(), not by the table
    CandidateState.UNDETERMINED_POWER: {CandidateState.REVERIFY_PENDING},
    CandidateState.DATA_MISSING: {CandidateState.REVERIFY_PENDING},
    CandidateState.ERROR: {CandidateState.REVERIFY_PENDING},
    CandidateState.FAIL_COST: {CandidateState.REVERIFY_PENDING},
    CandidateState.FAIL_STAT: {CandidateState.REVERIFY_PENDING},
    CandidateState.REVERIFY_PENDING: {CandidateState.PENDING},
    CandidateState.PASS: set(),  # terminal: a pass is never silently reused
}


class InvalidTransition(ValueError):
    pass


def assert_transition(current: CandidateState, nxt: CandidateState) -> None:
    if current == nxt:
        return
    if nxt not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"transition {current.value} -> {nxt.value} is not allowed")


#: error classes for retry policy.  Transient errors may be retried (limited
#: times); permanent ones never are.
TRANSIENT_ERROR_CODES = {
    "DATA_DOWNLOAD_TEMP",
    "PROCESS_INTERRUPTED",
    "DISK_TEMP",
    "RATE_LIMIT",
}
PERMANENT_ERROR_CODES = {
    "LOGIC_ERROR",
    "CONFIG_ERROR",
    "DATA_CORRUPT",
}


def reverify_eligible(
    state: CandidateState,
    *,
    error_code: Optional[str] = None,
    attempt_count: int = 0,
    max_attempts: int = 3,
    cost_model_version_changed: bool = False,
    new_data_beyond_dev: bool = False,
    validation_design_changed: bool = False,
    data_now_available: bool = False,
    power_condition_met: bool = False,
    context_changed: bool = False,
) -> tuple[bool, str]:
    """Central policy for when a non-final state may be re-verified.

    Returns (eligible, reason).  This is the single place the rules of
    directive §3-2 live so the queue cannot invent its own policy.
    """

    if state in (CandidateState.PASS, CandidateState.PENDING, CandidateState.RUNNING,
                 CandidateState.REVERIFY_PENDING):
        return False, f"state {state.value} is not re-verifiable"
    if context_changed:
        # P0-1/P1-7: a NEW experiment context (data, cost, code, config) is a
        # new experiment, not a retry of the old one — the per-experiment
        # attempt limit does not carry over.  PASS stays terminal (above).
        return True, "context_changed"
    if state == CandidateState.AWAITING_FDR:
        return False, "awaiting the generation FDR decision"
    if attempt_count >= max_attempts:
        return False, f"max attempts reached ({attempt_count}/{max_attempts})"
    if state == CandidateState.UNDETERMINED_POWER:
        return (True, "power_condition_met") if power_condition_met else (False, "power condition not met")
    if state == CandidateState.DATA_MISSING:
        return (True, "data_refresh_check") if data_now_available else (False, "required data still missing")
    if state == CandidateState.ERROR:
        if error_code in TRANSIENT_ERROR_CODES:
            return True, f"transient error retry ({attempt_count}/{max_attempts})"
        return False, f"permanent error {error_code!r} is never retried"
    if state == CandidateState.FAIL_COST:
        return (True, "cost_model_version_changed") if cost_model_version_changed \
            else (False, "cost model unchanged")
    if state == CandidateState.FAIL_STAT:
        if new_data_beyond_dev:
            return True, "new_data_beyond_dev_window"
        if validation_design_changed:
            return True, "validation_design_changed"
        return False, "no new data and validation design unchanged"
    return False, f"unhandled state {state.value}"


# ---------------------------------------------------------------------------
# retry bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class RetryRecord:
    """Everything directive §3-2 requires to be recorded per candidate."""

    attempt_count: int = 0
    max_attempts: int = 3
    last_attempt_at: Optional[str] = None
    next_retry_after: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    reverify_reason: Optional[str] = None
    data_version: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "last_attempt_at": self.last_attempt_at,
            "next_retry_after": self.next_retry_after,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "reverify_reason": self.reverify_reason,
            "data_version": self.data_version,
        }

    @classmethod
    def from_json(cls, obj: Mapping) -> "RetryRecord":
        keys = ("attempt_count", "max_attempts", "last_attempt_at",
                "next_retry_after", "error_code", "error_message",
                "reverify_reason", "data_version")
        return cls(**{k: obj[k] for k in keys if k in obj})


def backoff_seconds(attempt: int, base: int = 60) -> int:
    """Exponential backoff, capped at 1 hour."""
    return min(base * (2 ** max(0, attempt - 1)), 3600)


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# atomic file helpers
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, obj: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path: Path, default: object = None) -> object:
    path = Path(path)
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(path: Path, record: Mapping) -> None:
    """Append one JSON line with fsync — the ledger is append-only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def iter_jsonl(path: Path) -> Iterable[dict]:
    path = Path(path)
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # a torn trailing line after a crash is recoverable: skip it
                    # but only ever the LAST line may be torn
                    continue


# ---------------------------------------------------------------------------
# IDs  (directive §4-1)
# ---------------------------------------------------------------------------

#: parameters that describe the *evaluation environment*, not the strategy.
#: These must never enter strategy_id.
ENV_KEYS = ("dataset_id", "data_sha256", "cost_model", "code_version",
            "validation_cfg_version")


def canonical_json(obj: object) -> str:
    """Canonical JSON: sorted keys, tight separators.

    Normalization happens before this: parameters equal to family defaults
    are dropped and numerics are rounded so 0.5 and 0.50 hash identically.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _round_floats(obj: object, sig: int = 10) -> object:
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise ValueError("non-finite float in strategy definition")
        return float(f"{obj:.{sig}g}")
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, Mapping):
        return {k: _round_floats(v, sig) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, sig) for v in obj]
    raise TypeError(f"unsupported type {type(obj)!r} in strategy definition")


def normalize_strategy(defn: Mapping) -> dict:
    """Canonical strategy definition.

    - drops keys whose value equals the family default (explicit-vs-default
      differences must not create different strategies)
    - sorts keys, rounds floats
    - keeps only strategy-defining keys: family, params, symbols, timeframe,
      entry, exit, holding, risk (data/cost versions are environment)
    """
    allowed = ("family", "params", "symbols", "symbol", "timeframe", "entry",
               "exit", "holding", "risk", "direction", "primary_cell")
    out: dict = {}
    defaults = defn.get("_defaults") or {}
    for k in sorted(defn.keys()):
        if k not in allowed:
            continue  # anything not strategy-defining is excluded by design
        v = defn[k]
        if k == "params" and isinstance(v, Mapping):
            v = {pk: pv for pk, pv in v.items()
                 if pk not in defaults or defaults[pk] != pv}
        out[k] = _round_floats(v)
    if "family" not in out:
        raise ValueError("strategy definition requires 'family'")
    return out


def strategy_id(defn: Mapping) -> str:
    canon = canonical_json(normalize_strategy(defn))
    return "S-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def experiment_context_id(*, dataset_id: str, data_sha256: str, cost_model: str,
                          code_version: str = CODE_VERSION,
                          validation_cfg_version: str = VALIDATION_CFG_VERSION) -> str:
    """LEGACY (v2.0) context id — kept so old ledger rows stay readable.
    It does not cover funding/FNG data, cost parameters or real code
    changes; new evaluations use :func:`experiment_context` instead."""
    canon = canonical_json({
        "dataset_id": dataset_id,
        "data_sha256": data_sha256,
        "cost_model": cost_model,
        "code_version": code_version,
        "validation_cfg_version": validation_cfg_version,
    })
    return "C-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


#: every component that can change an evaluation result (P1-7).  Missing
#: components are an error — a context can never silently omit an input.
CONTEXT_COMPONENTS = ("data", "data_policy", "cost", "code", "validation", "runtime")


def _hash_floats_ok(obj: object) -> object:
    """Canonical form for hashing: floats via repr (exact), NaN/inf refused."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("non-finite value in experiment context")
        return repr(obj)
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, Mapping):
        return {str(k): _hash_floats_ok(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_hash_floats_ok(v) for v in obj]
    raise TypeError(f"unsupported type {type(obj)!r} in experiment context")


def experiment_context(components: Mapping) -> tuple[str, dict]:
    """Deterministic experiment-environment id from normalized components.

    The id identifies the experimental CONDITIONS — never the run: no clock,
    no uuid, no host name enters it (each run has its own run_id /
    evaluation_id).  Returns (context_id, normalized components).
    """
    missing = [k for k in CONTEXT_COMPONENTS if k not in components]
    if missing:
        raise ValueError(f"experiment context missing components {missing}")
    extra = sorted(set(components) - set(CONTEXT_COMPONENTS))
    if extra:
        raise ValueError(f"unknown experiment context components {extra}")
    norm = _hash_floats_ok({k: components[k] for k in CONTEXT_COMPONENTS})
    canon = canonical_json(norm)
    return "C2-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:20], norm


#: source files whose content defines engine behaviour.  CODE_VERSION is a
#: hand-maintained label; this hash is what actually identifies the code.
CODE_FILES = ("engine_lib.py", "runner.py", "evaluator.py", "portfolio.py",
              "generator.py", "generation.py", "data_quality.py",
              "costs_bridge.py", "store.py", "promotion.py")


def code_identity(base: Optional[Path] = None, files: Sequence[str] = CODE_FILES) -> dict:
    base = Path(base) if base else Path(__file__).resolve().parent
    h = hashlib.sha256()
    per: dict = {}
    for name in sorted(files):
        p = base / name
        data = p.read_bytes() if p.exists() else b"<MISSING>"
        fh = hashlib.sha256(data).hexdigest()
        per[name] = fh
        h.update(name.encode() + b"\0" + fh.encode() + b"\n")
    return {"code_hash": h.hexdigest(), "files": per, "label": CODE_VERSION}


def runtime_identity() -> dict:
    """Interpreter identity.  The engine is stdlib-only, so the Python
    implementation/version is the only library version that can change
    numeric results (math.erfc, lgamma, float formatting)."""
    return {"python_implementation": platform.python_implementation(),
            "python_version": "%d.%d.%d" % sys.version_info[:3]}


def git_commit(base: Optional[Path] = None) -> Optional[str]:
    """Informational only (recorded, NOT hashed: a doc-only commit must not
    create a new experiment)."""
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(base or Path(__file__).parent),
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def new_run_id() -> str:
    return "R-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]


def evaluation_id(strategy: str, context: str) -> str:
    """A fresh id for every evaluation run — re-evaluations of the same
    strategy never overwrite the old record, they get a new evaluation_id."""
    raw = f"{strategy}|{context}|{time.time_ns()}|{uuid.uuid4()}"
    return "E-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------------------
# statistics (stdlib only)
# ---------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float, itmax: int = 300, eps: float = 3e-12) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                + a * math.log(x) + b * math.log(1.0 - x))
    front = math.exp(ln_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """One-sided survival function P(T > t) for Student's t."""
    if df <= 0:
        raise ValueError("df must be positive")
    if t < 0:
        return 1.0 - t_sf(-t, df)
    x = df / (df + t * t)
    return 0.5 * betainc_reg(df / 2.0, 0.5, x)


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def norm_isf(p: float) -> float:
    """Inverse survival: z such that P(N > z) = p (bisection, 1e-10)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    lo, hi = -12.0, 12.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if norm_sf(mid) > p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def norm_ppf(p: float) -> float:
    """Inverse CDF (quantile): z such that P(N <= z) = p."""
    return norm_isf(1.0 - p)  # sf(z)=1-p ⇔ CDF(z)=p


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def stdev(xs: Sequence[float], ddof: int = 1) -> float:
    n = len(xs)
    if n - ddof <= 0:
        return float("nan")
    mu = mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - ddof))


def t_stat(xs: Sequence[float]) -> tuple[float, int]:
    """One-sample t statistic for H0: mean <= 0 (tested one-sided)."""
    n = len(xs)
    if n < 2:
        return float("nan"), n
    s = stdev(xs)
    if not (s > 0) or math.isnan(s):
        return float("nan"), n
    return mean(xs) / (s / math.sqrt(n)), n


def p_one_sided_from_t(t: float, df: int) -> float:
    return t_sf(t, df)


def holm_test(pvalues: Sequence[float], alpha: float = 0.05) -> list[bool]:
    """Holm-Bonferroni step-down.  Returns rejected flags in input order."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    rejected = [False] * m
    for rank, idx in enumerate(order):
        thresh = alpha / (m - rank)
        if pvalues[idx] <= thresh:
            rejected[idx] = True
        else:
            break
    return rejected


def bh_fdr(pvalues: Sequence[float], q: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg FDR control across candidates."""
    return bh_fdr_family(pvalues, len(pvalues), q)


def bh_fdr_family(pvalues: Sequence[float], m_family: int, q: float = 0.05) -> list[bool]:
    """BH step-up over a PRE-REGISTERED family of ``m_family`` hypotheses of
    which ``pvalues`` are the ones that produced a valid test statistic.

    Hypotheses of the family without a valid statistic (insufficient power,
    data missing) are counted in m and are never rejected.  No p-value is
    invented for them: the thresholds q*k/m simply use the full family size,
    which is exactly BH on the whole family with those hypotheses retained
    (and can only be stricter than BH on the testable subset).
    """
    k_ = len(pvalues)
    if m_family < k_:
        raise ValueError(f"family size {m_family} < number of p-values {k_}")
    if any((not isinstance(p, (int, float))) or not math.isfinite(p) or not 0.0 <= p <= 1.0
           for p in pvalues):
        raise ValueError("BH-FDR requires finite p-values in [0, 1]")
    if k_ == 0:
        return []
    order = sorted(range(k_), key=lambda i: pvalues[i])
    rejected = [False] * k_
    k = -1
    for rank in range(k_ - 1, -1, -1):
        idx = order[rank]
        if pvalues[idx] <= q * (rank + 1) / m_family:
            k = rank
            break
    if k >= 0:
        for rank in range(k + 1):
            rejected[order[rank]] = True
    return rejected


def select_non_overlapping(events: Sequence[Mapping], horizon: int) -> list[Mapping]:
    """Greedy time selection of non-overlapping events.

    Each event must carry ``entry_index``; two events overlap when their
    [entry, entry+horizon) windows intersect.  Overlapping returns must
    never be treated as independent observations (directive §5-3).
    """
    chosen: list[Mapping] = []
    last_end = -1
    for ev in sorted(events, key=lambda e: e["entry_index"]):
        start = int(ev["entry_index"])
        if start >= last_end:
            chosen.append(ev)
            last_end = start + horizon
    return chosen


def nw_tstat(xs: Sequence[float], lag: Optional[int] = None) -> tuple[float, int]:
    """Newey-West HAC t-statistic for H0: mean <= 0."""
    n = len(xs)
    if n < 3:
        return float("nan"), n
    if lag is None:
        lag = int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    mu = mean(xs)
    u = [x - mu for x in xs]
    s0 = sum(v * v for v in u)
    s_lag = 0.0
    for l in range(1, min(lag, n - 1) + 1):
        w = 1.0 - l / (lag + 1.0)
        s_lag += w * sum(u[i] * u[i - l] for i in range(l, n))
    var = (s0 + 2.0 * s_lag) / n
    if var <= 0:
        return float("nan"), n
    return mu / math.sqrt(var / n), n


def block_bootstrap_pvalue(xs: Sequence[float], block: int = 8,
                           n_boot: int = 1000, seed: int = 0) -> float:
    """Moving-block bootstrap p-value for H0: mean <= 0 (A-26 fix).

    The data are centred on the observed mean (imposing the H0), the
    bootstrap distribution of the mean is built from centred blocks, and
    p = P(mean* >= observed mean).  The previous version compared bootstrap
    means with the observed mean itself — that measures nothing about H0.
    """
    n = len(xs)
    if n < block * 2:
        return float("nan")
    rng = random.Random(seed)
    obs = mean(xs)
    centred = [x - obs for x in xs]
    blocks = [centred[i:i + block] for i in range(0, n - block + 1, block)]
    nb = len(blocks)
    count = 0
    for _ in range(n_boot):
        sample = [x for _ in range(0, nb) for x in blocks[rng.randrange(nb)]]
        if mean(sample[:n]) >= obs:
            count += 1
    return (count + 1) / (n_boot + 1)


import random  # noqa: E402  (used by bootstrap; kept next to its only users)


def deflated_sharpe(sr: float, n: int, skew: float, kurtosis: float,
                    n_trials: int, var_sr: float) -> float:
    """Bailey & López de Prado Deflated Sharpe Ratio.

    ``var_sr`` is the cross-trial variance of the Sharpe estimates (the
    dispersion across the trials that were actually run), NOT the sampling
    variance of one SR.
    """
    if n_trials < 1 or n < 2:
        return float("nan")
    g = 0.5772156649015329  # Euler-Mascheroni
    e = math.e
    n_trials = max(2, n_trials)
    # Φ⁻¹ here is the standard-normal QUANTILE (inverse CDF)
    sr0 = math.sqrt(max(var_sr, 1e-300)) * (
        (1.0 - g) * norm_ppf(1.0 - 1.0 / n_trials)
        + g * norm_ppf(1.0 - 1.0 / (n_trials * e))
    )
    denom = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return 0.0
    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)
    return 1.0 - norm_sf(z)


def pbo_cscv(returns_matrix: Sequence[Sequence[float]], n_partitions: int = 16,
             max_combos: int = 12870, seed: int = 0) -> tuple[float, int]:
    """Probability of Backtest Overfitting via CSCV.

    ``returns_matrix`` is T x N per-period returns for N candidate configs.
    Returns (pbo, n_combinations_used).
    """
    T = len(returns_matrix)
    N = len(returns_matrix[0]) if T else 0
    if T < n_partitions or N < 2:
        return float("nan"), 0
    idx = list(range(T))
    block = T // n_partitions
    blocks = [idx[i * block:(i + 1) * block] for i in range(n_partitions)]
    if len(blocks[-1]) < block:
        blocks[-2].extend(blocks.pop())
    combos = list(__import__("itertools").combinations(range(len(blocks)), len(blocks) // 2))
    if len(combos) > max_combos:
        rng = random.Random(seed)
        combos = rng.sample(combos, max_combos)
    logits = []
    for combo in combos:
        test = [b for b in range(len(blocks)) if b not in combo]
        train_rows = [r for b in combo for r in blocks[b]]
        test_rows = [r for b in test for r in blocks[b]]
        train_mean = [mean([returns_matrix[r][c] for r in train_rows]) for c in range(N)]
        test_mean = [mean([returns_matrix[r][c] for r in test_rows]) for c in range(N)]
        best = max(range(N), key=lambda c: train_mean[c])
        rank = sorted(test_mean).index(test_mean[best])
        omega = (rank + 1) / (N + 1)
        if not 0.0 < omega < 1.0:
            omega = min(max(omega, 1e-6), 1 - 1e-6)
        logits.append(math.log(omega / (1.0 - omega)))
    pbo = sum(1 for l in logits if l <= 0) / len(logits)
    return pbo, len(logits)


# ---------------------------------------------------------------------------
# Stouffer combination (directive §7) — fixed version
# ---------------------------------------------------------------------------


def stouffer_independent(zs: Sequence[float], weights: Optional[Sequence[float]] = None) -> float:
    """Weighted Stouffer Z for *independent* z-scores.

    Z = sum(w_i z_i) / sqrt(sum(w_i^2)).

    IMPORTANT: the inputs are z-scores (e.g. one-sided z from a cell's
    p-value).  Weighting t-statistics AGAIN by sqrt(n) here would double
    count sample size because t already scales with sqrt(n); the old
    engine's sqrt(n)-weighted t combination was therefore removed.
    """
    if not zs:
        raise ValueError("no z-scores")
    if any(not math.isfinite(z) for z in zs):
        raise ValueError("non-finite z-score")
    w = list(weights) if weights is not None else [1.0] * len(zs)
    if len(w) != len(zs):
        raise ValueError("weights length mismatch")
    if any(wi < 0 for wi in w):
        raise ValueError("negative weight")
    if all(wi == 0 for wi in w):
        raise ValueError("all weights zero")
    num = sum(wi * zi for wi, zi in zip(w, zs))
    den = math.sqrt(sum(wi * wi for wi in w))
    return num / den


def stouffer_correlated(zs: Sequence[float], corr: Sequence[Sequence[float]]) -> float:
    """Stouffer Z under a *known/estimated* correlation matrix R.

    Z = 1'R^-1 z / sqrt(1'R^-1 1).   Callers must estimate R on development
    data only and treat estimation failure as UNDETERMINED — there is no
    rho-grid maximum here (that was selection bias, directive §7-2).
    """
    n = len(zs)
    if len(corr) != n or any(len(row) != n for row in corr):
        raise ValueError("correlation matrix shape mismatch")
    # invert R (Gauss-Jordan); raise if singular
    a = [list(map(float, row)) for row in corr]
    inv = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            raise ValueError("correlation matrix is singular")
        a[col], a[piv] = a[piv], a[col]
        inv[col], inv[piv] = inv[piv], inv[col]
        pv = a[col][col]
        for j in range(n):
            a[col][j] /= pv
            inv[col][j] /= pv
        for r in range(n):
            if r == col:
                continue
            f = a[r][col]
            for j in range(n):
                a[r][j] -= f * a[col][j]
                inv[r][j] -= f * inv[col][j]
    ones = [1.0] * n
    rinv_1 = [sum(inv[i][j] for j in range(n)) for i in range(n)]
    denom = sum(ones[i] * rinv_1[i] for i in range(n))
    if denom <= 0:
        raise ValueError("correlation matrix is not positive definite")
    num = sum(rinv_1[i] * zs[i] for i in range(n))
    return num / math.sqrt(denom)


def estimate_corr_dev(returns_a: Sequence[float], returns_b: Sequence[float],
                      *, min_n: int = 30, max_ci_width: float = 0.6,
                      n_boot: int = 200, seed: int = 0) -> Optional[float]:
    """Pearson correlation on ALIGNED per-event returns, dev data only.

    Returns None (→ UNDETERMINED upstream) when alignment is too short or
    the bootstrap CI is too wide to trust.  Never guesses a value.
    """
    n = min(len(returns_a), len(returns_b))
    if n < min_n:
        return None
    a = list(returns_a[:n])
    b = list(returns_b[:n])
    ma, mb = mean(a), mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    rho = cov / math.sqrt(va * vb)
    rng = random.Random(seed)
    rhos = []
    for _ in range(n_boot):
        ii = [rng.randrange(n) for _ in range(n)]
        aa = [a[i] for i in ii]
        bb = [b[i] for i in ii]
        ma2, mb2 = mean(aa), mean(bb)
        c2 = sum((x - ma2) * (y - mb2) for x, y in zip(aa, bb))
        v2a = sum((x - ma2) ** 2 for x in aa)
        v2b = sum((y - mb2) ** 2 for y in bb)
        if v2a > 0 and v2b > 0:
            rhos.append(c2 / math.sqrt(v2a * v2b))
    if len(rhos) < n_boot * 0.9:
        return None
    rhos.sort()
    lo, hi = rhos[int(0.025 * len(rhos))], rhos[int(0.975 * len(rhos)) - 1]
    if hi - lo > max_ci_width:
        return None
    return rho


# ---------------------------------------------------------------------------
# verdicts (directive §4, §6, §8)
# ---------------------------------------------------------------------------

MIN_VALID_N = 30  # preregistered minimum events per cell


@dataclass
class CellResult:
    symbol: str
    cell_id: str
    n: int
    mean_bp: float
    t: float
    p: float
    status: str  # "PASS" | "FAIL_STAT" | "UNDETERMINED_POWER" | "DATA_MISSING" | "FAIL_COST"
    holm_rejected: bool = False
    note: str = ""

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol, "cell_id": self.cell_id, "n": self.n,
            "mean_bp": self.mean_bp, "t": self.t, "p": self.p,
            "status": self.status, "holm_rejected": self.holm_rejected,
            "note": self.note,
        }


@dataclass
class SymbolResult:
    symbol: str
    cells: list[CellResult]
    status: str  # aggregated per symbol, same vocabulary
    data_available: bool = True
    note: str = ""

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol, "status": self.status,
            "data_available": self.data_available, "note": self.note,
            "cells": [c.to_json() for c in self.cells],
        }


#: precedence for the OVERALL label when required symbols disagree.
#: "not proven" states outrank explicit failures so missing data can never
#: be laundered into a clean FAIL (and failures still block PASS).
VERDICT_PRECEDENCE = [
    CandidateState.ERROR.value,          # P2: a computation error outranks all
    CandidateState.DATA_MISSING.value,
    CandidateState.UNDETERMINED_POWER.value,
    CandidateState.FAIL_COST.value,
    CandidateState.FAIL_STAT.value,
    CandidateState.PASS.value,
]


def _symbol_status(cells: Sequence[CellResult], alpha: float = 0.05) -> str:
    if any(c.status == "ERROR" for c in cells):
        return "ERROR"
    if any(c.status == "DATA_MISSING" for c in cells):
        return "DATA_MISSING"
    if any(c.status == "UNDETERMINED_POWER" for c in cells):
        return "UNDETERMINED_POWER"
    if any(c.status == "FAIL_COST" for c in cells):
        return "FAIL_COST"
    # Holm across the pre-registered cell family of THIS symbol
    ps = [c.p for c in cells if math.isfinite(c.p)]
    flags = holm_test(ps, alpha) if ps else []
    it = iter(flags)
    for c in cells:
        if math.isfinite(c.p):
            c.holm_rejected = next(it)
    if any(c.holm_rejected and c.mean_bp > 0 for c in cells):
        return "PASS"
    return "FAIL_STAT"


def verdict_from_cells(symbol_results: Sequence[SymbolResult],
                       required_symbols: Sequence[str],
                       alpha: float = 0.05) -> tuple[str, dict]:
    """Overall candidate verdict — the §8 rules in code.

    1. per-symbol results are recorded individually (never collapsed)
    2. a symbol with missing data is UNDETERMINED, not a failure
    3. a multi-symbol candidate PASSES only if every required symbol passes
    4. (single-symbol-only effect ⇒ caller may split the candidate)
    """
    by_symbol = {r.symbol: r for r in symbol_results}
    missing_required = [s for s in required_symbols if s not in by_symbol]
    statuses: dict[str, str] = {}
    for s in required_symbols:
        if s in missing_required:
            statuses[s] = "DATA_MISSING"
            continue
        r = by_symbol[s]
        if not r.cells:
            statuses[s] = "DATA_MISSING"
            continue
        statuses[s] = r.status if r.status else _symbol_status(r.cells, alpha)
        if statuses[s] not in VERDICT_PRECEDENCE:
            # P2: an unknown label can never be ranked into a pass
            statuses[s] = CandidateState.ERROR.value
        r.status = statuses[s]
    extra = {r.symbol: r.status for r in symbol_results if r.symbol not in required_symbols}
    rank = VERDICT_PRECEDENCE.index
    overall = min(statuses.values(), key=rank)
    passed = sorted(s for s, v in statuses.items() if v == "PASS")
    detail = {
        "per_symbol": statuses,
        "extra_symbols_observed": extra,
        "required_symbols": list(required_symbols),
        # §8-4 hint: a multi-symbol candidate that failed overall while exactly
        # one required symbol passed may be SPLIT into a symbol-restricted
        # NEW candidate (never a retro-edit of this one).
        "single_symbol_pass_hint": passed if (overall != "PASS" and len(statuses) > 1
                                              and len(passed) == 1) else [],
    }
    return overall, detail


# ---------------------------------------------------------------------------
# locked TEST windows (directive §6, §12) — read-only, single-use
# ---------------------------------------------------------------------------

#: documented locked TEST windows (worklog Task 32/34 records).  These are
#: the ONLY final-test windows and they are never touched by selection.
LOCKED_TEST_PERIODS: dict[str, tuple[str, str]] = {
    "BTCUSDT": ("2024-03-25", "2024-06-18"),
    "ETHUSDT": ("2026-04-28", "2026-08-31"),
}

SYMBOL_DATA_START: dict[str, str] = {
    "BTCUSDT": "2021-04-01",
    "ETHUSDT": "2021-04-01",
    "BNBUSDT": "2021-04-01",
    "XRPUSDT": "2021-04-01",
    "SOLUSDT": "2021-08-01",
    "TRXUSDT": "2021-08-01",
    "ZECUSDT": "2021-04-01",
    "DOGEUSDT": "2021-04-01",
}


class LockedWindowError(RuntimeError):
    pass


def _iso(d: str) -> float:
    return time.mktime(time.strptime(d, "%Y-%m-%d"))


def assert_no_locked_overlap(symbol: str, data_start: str, data_end: str,
                             purpose: str) -> None:
    """Selection/parameter steps must never read locked-window data."""
    if symbol not in LOCKED_TEST_PERIODS:
        return
    lo, hi = LOCKED_TEST_PERIODS[symbol]
    if _iso(data_start) <= _iso(hi) and _iso(lo) <= _iso(data_end):
        raise LockedWindowError(
            f"{purpose} may not read {symbol} locked TEST window [{lo},{hi}] "
            f"(requested data [{data_start},{data_end}])"
        )


def locked_window_for(symbol: str) -> Optional[tuple[str, str]]:
    return LOCKED_TEST_PERIODS.get(symbol)


def symbols_without_locked_test(symbols: Sequence[str]) -> list[str]:
    return [s for s in symbols if s not in LOCKED_TEST_PERIODS]


class LockState(str, Enum):
    """Locked-TEST attempt states (P1-6).

    AVAILABLE            no attempt ever exposed the window to this strategy
    RESERVED             a process holds the right to run it; data not yet
                         loaded / not yet shown to the strategy
    EVALUATION_STARTED   locked bars passed every check and are being fed to
                         the strategy — from here on the window is USED
    CONSUMED             evaluation finished; the result is stored, immutable
    BLOCKED_DATA_MISSING data missing / quality failed BEFORE exposure —
                         recorded, not consumed, may be retried
    FAILED               the attempt died; consumed iff it had started
    """
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    EVALUATION_STARTED = "EVALUATION_STARTED"
    CONSUMED = "CONSUMED"
    BLOCKED_DATA_MISSING = "BLOCKED_DATA_MISSING"
    FAILED = "FAILED"


LOCK_REGISTRY_SCHEMA = "lock_test_registry_v2"
_LOCK_ACTIVE = (LockState.RESERVED.value, LockState.EVALUATION_STARTED.value)


class LockRegistryError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, owned by someone else
    except (ValueError, TypeError, OverflowError):
        return False


class LockTestRegistry:
    """Single-use registry for final locked tests, with explicit attempt
    state transitions and cross-process concurrency control.

    Storage: one JSON file, every read-modify-write under an exclusive
    ``fcntl.flock`` on ``<file>.lock`` and written atomically.  The v1 format
    ({sid: {sym: [{"context_id","ts"}]}}) is preserved verbatim under
    ``legacy_v1`` and every legacy entry counts as an exposed use — old
    consumption is never forgotten or deleted.
    """

    def __init__(self, path: Path, *, owner_pid: Optional[int] = None):
        self.path = Path(path)
        self.guard = self.path.with_name(self.path.name + ".lock")
        self.owner = {"pid": owner_pid or os.getpid(), "host": socket.gethostname()}

    # -- storage -----------------------------------------------------------
    def _load(self) -> dict:
        raw = load_json(self.path, default=None)
        if raw is None:
            return {"schema": LOCK_REGISTRY_SCHEMA, "legacy_v1": {}, "attempts": {}}
        if isinstance(raw, dict) and raw.get("schema") == LOCK_REGISTRY_SCHEMA:
            raw.setdefault("legacy_v1", {})
            raw.setdefault("attempts", {})
            return raw
        if isinstance(raw, dict):
            # v1 layout: keep every byte of history under legacy_v1
            return {"schema": LOCK_REGISTRY_SCHEMA, "legacy_v1": raw, "attempts": {}}
        raise LockRegistryError(f"{self.path}: unreadable lock registry")

    def _txn(self):
        reg = self

        class _T:
            def __enter__(self_inner):
                reg.path.parent.mkdir(parents=True, exist_ok=True)
                self_inner.fd = os.open(str(reg.guard), os.O_CREAT | os.O_RDWR, 0o644)
                fcntl.flock(self_inner.fd, fcntl.LOCK_EX)
                self_inner.data = reg._load()
                self_inner.dirty = False
                return self_inner

            def __exit__(self_inner, et, ev, tb):
                try:
                    if et is None and self_inner.dirty:
                        atomic_write_json(reg.path, self_inner.data)
                finally:
                    fcntl.flock(self_inner.fd, fcntl.LOCK_UN)
                    os.close(self_inner.fd)
                return False
        return _T()

    @staticmethod
    def _attempts(data: dict, sid: str, sym: str) -> list:
        return data["attempts"].setdefault(sid, {}).setdefault(sym, [])

    @staticmethod
    def _legacy_uses(data: dict, sid: str, sym: str) -> int:
        return len((data.get("legacy_v1") or {}).get(sid, {}).get(sym, []) or [])

    def _recover_list(self, atts: list) -> list:
        """Stale-owner recovery for one (sid, sym) list.  Returns changes."""
        changed = []
        for a in atts:
            if a["state"] not in _LOCK_ACTIVE:
                continue
            own = a.get("owner") or {}
            if own.get("host") != self.owner["host"]:
                continue  # cannot verify a foreign host's process — leave it
            if own.get("pid") == self.owner["pid"] or _pid_alive(own.get("pid")):
                continue
            if a["state"] == LockState.RESERVED.value:
                a["state"] = LockState.FAILED.value
                a["reason"] = "owner died before evaluation started (data never exposed)"
            else:
                a["state"] = LockState.FAILED.value
                a["reason"] = "owner died during evaluation — window counts as used"
            a["finished_at"] = iso_now()
            changed.append(a["attempt_id"])
        return changed

    # -- queries -------------------------------------------------------------
    def uses(self, strategy_id: str, symbol: str) -> int:
        """Exposed uses: legacy records + attempts that reached evaluation."""
        data = self._load()
        atts = data["attempts"].get(strategy_id, {}).get(symbol, [])
        return self._legacy_uses(data, strategy_id, symbol) + sum(
            1 for a in atts if a.get("exposed"))

    def state(self, strategy_id: str, symbol: str) -> LockState:
        data = self._load()
        atts = data["attempts"].get(strategy_id, {}).get(symbol, [])
        if any(a["state"] == LockState.CONSUMED.value for a in atts) or \
                self._legacy_uses(data, strategy_id, symbol):
            return LockState.CONSUMED
        if not atts:
            return LockState.AVAILABLE
        return LockState(atts[-1]["state"])

    def entry_status(self, strategy_id: str, symbol: str) -> dict:
        """Read-only: may a NEW locked-TEST attempt start for this strategy+symbol?

        Not enterable when the window was exposed before (legacy v1 use, or an
        attempt that reached EVALUATION_STARTED/CONSUMED/exposed-FAILED) or when
        another attempt is active.  BLOCKED_DATA_MISSING and never-exposed FAILED
        attempts do not consume the window.  Mutates nothing."""
        data = self._load()
        atts = data["attempts"].get(strategy_id, {}).get(symbol, [])
        uses = self._legacy_uses(data, strategy_id, symbol) + sum(1 for a in atts if a.get("exposed"))
        active = any(a["state"] in _LOCK_ACTIVE for a in atts)
        if uses:
            reason = "ALREADY_CONSUMED"
        elif active:
            reason = "IN_PROGRESS"
        else:
            reason = "AVAILABLE"
        return {"symbol": symbol, "state": self.state(strategy_id, symbol).value,
                "uses": uses, "enterable": reason == "AVAILABLE", "reason": reason}

    def attempts(self, strategy_id: str, symbol: str) -> list[dict]:
        return list(self._load()["attempts"].get(strategy_id, {}).get(symbol, []))

    def result(self, strategy_id: str, symbol: str) -> Optional[dict]:
        for a in self.attempts(strategy_id, symbol):
            if a["state"] == LockState.CONSUMED.value:
                return a.get("result")
        return None

    # -- transitions ----------------------------------------------------------
    def reserve(self, strategy_id: str, symbol: str, context_id: str) -> dict:
        """AVAILABLE → RESERVED.  Refused when another live process holds an
        active attempt or when the window was already exposed once."""
        with self._txn() as t:
            atts = self._attempts(t.data, strategy_id, symbol)
            if self._recover_list(atts):
                t.dirty = True
            uses = self._legacy_uses(t.data, strategy_id, symbol) + sum(
                1 for a in atts if a.get("exposed"))
            if uses:
                return {"ok": False, "reason": "ALREADY_USED", "uses": uses}
            if any(a["state"] in _LOCK_ACTIVE for a in atts):
                return {"ok": False, "reason": "IN_PROGRESS", "uses": uses}
            att = {
                "attempt_id": "L-" + uuid.uuid4().hex[:16],
                "state": LockState.RESERVED.value,
                "context_id": context_id,
                "owner": dict(self.owner),
                "reserved_at": iso_now(),
                "exposed": False,
            }
            atts.append(att)
            t.dirty = True
            return {"ok": True, "token": {"strategy_id": strategy_id, "symbol": symbol,
                                          "attempt_id": att["attempt_id"]}, "uses": 0}

    def _transition(self, token: Mapping, allowed_from: Sequence[str], to: LockState,
                    **fields) -> dict:
        with self._txn() as t:
            atts = self._attempts(t.data, token["strategy_id"], token["symbol"])
            for a in atts:
                if a["attempt_id"] != token["attempt_id"]:
                    continue
                if a["state"] not in allowed_from:
                    raise LockRegistryError(
                        f"attempt {a['attempt_id']} is {a['state']}, cannot move to {to.value}")
                if (a.get("owner") or {}).get("pid") != self.owner["pid"]:
                    raise LockRegistryError("attempt owned by another process")
                a["state"] = to.value
                a.update(fields)
                t.dirty = True
                return dict(a)
            raise LockRegistryError(f"unknown attempt {token['attempt_id']}")

    def start(self, token: Mapping) -> dict:
        """RESERVED → EVALUATION_STARTED: called only after the locked data
        loaded and passed every quality check, right before exposure."""
        return self._transition(token, (LockState.RESERVED.value,),
                                LockState.EVALUATION_STARTED,
                                started_at=iso_now(), exposed=True)

    def complete(self, token: Mapping, result: Mapping) -> dict:
        """EVALUATION_STARTED → CONSUMED with an immutable result."""
        return self._transition(token, (LockState.EVALUATION_STARTED.value,),
                                LockState.CONSUMED, finished_at=iso_now(),
                                result=dict(result))

    def block(self, token: Mapping, reason: str) -> dict:
        """RESERVED → BLOCKED_DATA_MISSING (window never exposed)."""
        return self._transition(token, (LockState.RESERVED.value,),
                                LockState.BLOCKED_DATA_MISSING,
                                finished_at=iso_now(), reason=reason)

    def fail(self, token: Mapping, reason: str) -> dict:
        """RESERVED/EVALUATION_STARTED → FAILED (exposure flag unchanged)."""
        return self._transition(token, _LOCK_ACTIVE, LockState.FAILED,
                                finished_at=iso_now(), reason=reason)

    def recover_stale(self) -> list[str]:
        with self._txn() as t:
            changed: list[str] = []
            for sid, syms in t.data["attempts"].items():
                for sym, atts in syms.items():
                    changed += self._recover_list(atts)
            t.dirty = bool(changed)
            return changed

    def record_use(self, strategy_id: str, symbol: str, context_id: str) -> dict:
        """v1-compatible one-shot use (reserve → start → complete without a
        result).  The runner no longer calls this: it records a use BEFORE
        any data check, which is exactly the defect P1-6 removes."""
        r = self.reserve(strategy_id, symbol, context_id)
        if not r["ok"]:
            if r["reason"] == "IN_PROGRESS":
                raise LockRegistryError("locked test in progress elsewhere")
            # consumed before: record the extra (non-independent) exposure
            with self._txn() as t:
                atts = self._attempts(t.data, strategy_id, symbol)
                atts.append({"attempt_id": "L-" + uuid.uuid4().hex[:16],
                             "state": LockState.CONSUMED.value, "context_id": context_id,
                             "owner": dict(self.owner), "reserved_at": iso_now(),
                             "started_at": iso_now(), "finished_at": iso_now(),
                             "exposed": True, "result": {"reuse": True}})
                t.dirty = True
            n = self.uses(strategy_id, symbol)
            return {"use_number": n, "independent": False}
        self.start(r["token"])
        self.complete(r["token"], {"legacy_record_use": True})
        n = self.uses(strategy_id, symbol)
        return {"use_number": n, "independent": n == 1}


# ---------------------------------------------------------------------------
# process lock (directive §13)
# ---------------------------------------------------------------------------


class ProcessLock:
    """PID-file lock with stale detection (dead PID → reclaimable).

    The check-and-write runs under an exclusive flock on ``<path>.guard`` so
    two starters can never both conclude the lock is free (the previous
    exists()/write_text() sequence was racy)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.guard = self.path.with_name(self.path.name + ".guard")
        self._held = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.guard), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if self.path.exists():
                try:
                    pid = int(self.path.read_text().strip())
                except ValueError:
                    pid = None
                if pid is not None and _pid_alive(pid):
                    return False  # live owner
                # stale (dead / unreadable pid) — reclaim
            tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            tmp.write_text(str(os.getpid()))
            os.replace(tmp, self.path)
            self._held = True
            return True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def release(self) -> None:
        if self._held:
            try:
                if self.path.read_text().strip() == str(os.getpid()):
                    self.path.unlink()
            except FileNotFoundError:
                pass
            self._held = False

    def __enter__(self) -> "ProcessLock":
        if not self.acquire():
            raise BlockingIOError(f"another process holds {self.path}")
        return self

    def __exit__(self, *exc) -> None:
        self.release()
