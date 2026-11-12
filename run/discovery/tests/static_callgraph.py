"""Static call graph of the discovery modules (ADR 0079 follow-up, G-03).

Used by ``test_audit_followup_20261112.TestG03SharedSurface`` to compute which
functions the Paper Forward evaluator and its independent replay both reach,
from CALL nodes of the source — not from names that merely appear in it.

What it resolves (each one exercised by the tests):

- ``f(...)`` where ``f`` is defined in the module, imported with
  ``from m import f [as g]`` (module level or inside the function), or a class
  (construction: the edge goes to the class, and from it to ``__init__`` /
  ``__post_init__`` when defined);
- ``alias.f(...)`` where ``alias`` is an imported module (``import m as alias``);
- ``self.m(...)`` / ``cls.m(...)`` / ``Class.m(...)`` (methods of the class and of
  its bases defined in the same repository);
- ``x.m(...)`` where ``x`` is a parameter annotated with a repository class, or
  a local assigned from exactly one repository-class construction;
- ``self.p.cost.m(...)`` and the other receivers listed in ``receiver_types``
  (a binding the CALLER declares; ``None`` keeps the call as a named dynamic
  node ``dyn:<receiver>.<method>`` whose code is not known statically);
- a repository function passed as an argument (``key=f``, ``exit_rule=g``):
  an edge of kind "ref" (it may be called by the callee).

The body of a nested ``def`` / ``lambda`` belongs to the function that defines
it (closures such as the one ``stop_exit_rule`` returns are counted where they
are written).

What it does NOT see (the limits stated in ADR 0079):

- calls through a value only known at run time: a callable parameter
  (``sig(...)``, ``exit_rule(...)``, ``refusal(...)``), a dict of functions,
  ``getattr``, a method of an unannotated local (``e.to_json()``); these are
  recorded in ``untracked`` and never guessed;
- calls inside the standard library and third-party packages (not followed);
- which branch runs: a reachable call is not a call that happens on every
  input.  Static reachability over-approximates, the untracked calls
  under-approximate: the result is evidence about the source, not a proof of
  the run-time behaviour.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Mapping, Optional

FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _dotted(expr) -> Optional[str]:
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        parts.append(expr.id)
        return ".".join(reversed(parts))
    return None


class CallGraph:
    def __init__(self, root: Path, receiver_types: Mapping[str, Optional[str]] = None):
        self.root = Path(root)
        self.receiver_types = dict(receiver_types or {})
        self._mods: dict = {}
        self._edges: dict = {}
        self.consts: dict = {}          # node -> {module.CONST}
        self.untracked: dict = {}       # node -> {call text}
        self.kinds: dict = {}           # (caller, callee) -> "call" | "ref"

    # -- modules -------------------------------------------------------------
    @staticmethod
    def _imports(node, table: dict) -> None:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    table[a.asname] = ("module", a.name)
                else:
                    table[a.name.split(".")[0]] = ("module", a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for a in node.names:
                table[a.asname or a.name] = ("from", node.module, a.name)

    def module(self, name: str) -> Optional[dict]:
        if name not in self._mods:
            f = self.root / f"{name}.py"
            if not f.is_file():
                self._mods[name] = None
                return None
            tree = ast.parse(f.read_text(encoding="utf-8"))
            info = {"name": name, "funcs": {}, "classes": {}, "imports": {}, "consts": set()}
            for n in tree.body:
                if isinstance(n, FUNC):
                    info["funcs"][n.name] = n
                elif isinstance(n, ast.ClassDef):
                    info["classes"][n.name] = {
                        "node": n, "bases": n.bases,
                        "methods": {m.name: m for m in n.body if isinstance(m, FUNC)}}
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    self._imports(n, info["imports"])
                elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                    for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                        if isinstance(t, ast.Name):
                            info["consts"].add(t.id)
                elif isinstance(n, ast.Try):        # optional imports
                    for s in n.body:
                        if isinstance(s, (ast.Import, ast.ImportFrom)):
                            self._imports(s, info["imports"])
            self._mods[name] = info
        return self._mods[name]

    def resolve_symbol(self, mod: str, name: str, imports: Optional[dict] = None, depth: int = 0):
        """('func'|'class'|'const'|'module', id) of ``name`` seen from ``mod``."""
        if depth > 8:
            return None
        info = self.module(mod)
        if info is None:
            return None
        table = imports if imports is not None else info["imports"]
        if imports is not None and name in imports:
            pass                                    # a function-local import wins
        elif name in info["funcs"]:
            return ("func", f"{mod}.{name}")
        elif name in info["classes"]:
            return ("class", f"{mod}.{name}")
        elif name in info["consts"] and name not in table:
            return ("const", f"{mod}.{name}")
        imp = table.get(name)
        if imp is None:
            return None
        if imp[0] == "module":
            return ("module", imp[1]) if self.module(imp[1]) is not None else None
        _, src, orig = imp
        if self.module(src) is None:
            return None
        return self.resolve_symbol(src, orig, None, depth + 1)

    def method(self, class_id: str, attr: str, depth: int = 0) -> Optional[str]:
        if depth > 8:
            return None
        mod, cls = class_id.rsplit(".", 1)
        info = self.module(mod)
        c = (info or {}).get("classes", {}).get(cls)
        if c is None:
            return None
        if attr in c["methods"]:
            return f"{class_id}.{attr}"
        for b in c["bases"]:
            if isinstance(b, ast.Name):
                r = self.resolve_symbol(mod, b.id)
                if r and r[0] == "class":
                    got = self.method(r[1], attr, depth + 1)
                    if got:
                        return got
        return None

    def definition(self, node_id: str):
        """(ast node, module name, class id or None) of a function / method / class id."""
        parts = node_id.split(".")
        for cut in (2, 1):
            if len(parts) <= cut:
                continue
            mod = ".".join(parts[:-cut])
            info = self.module(mod)
            if info is None:
                continue
            if cut == 1:
                name = parts[-1]
                if name in info["funcs"]:
                    return info["funcs"][name], mod, None
                if name in info["classes"]:
                    return info["classes"][name]["node"], mod, f"{mod}.{name}"
            else:
                cls, name = parts[-2], parts[-1]
                c = info["classes"].get(cls)
                if c and name in c["methods"]:
                    return c["methods"][name], mod, f"{mod}.{cls}"
        raise KeyError(node_id)

    # -- edges ---------------------------------------------------------------
    def edges(self, node_id: str) -> set:
        if node_id in self._edges:
            return self._edges[node_id]
        if node_id.startswith("dyn:"):
            self._edges[node_id] = set()
            return self._edges[node_id]
        fn, mod, cls = self.definition(node_id)
        out: set = set()
        self._edges[node_id] = out
        if isinstance(fn, ast.ClassDef):              # construction
            for m in ("__init__", "__post_init__"):
                got = self.method(node_id, m)
                if got:
                    out.add(got)
                    self.kinds[(node_id, got)] = "call"
            return out
        imports = dict(self.module(mod)["imports"])
        nested = set()
        for n in ast.walk(fn):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                self._imports(n, imports)
            if isinstance(n, FUNC) and n is not fn:
                nested.add(n.name)
        local_types = self._local_types(fn, mod, imports)
        consts = self.consts.setdefault(node_id, set())
        untracked = self.untracked.setdefault(node_id, set())
        params = {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)}

        def sym(name):
            if name in nested or (name in params and name not in imports):
                return None
            return self.resolve_symbol(mod, name, imports)

        def callee(expr):
            if isinstance(expr, ast.Name):
                r = sym(expr.id)
                if r and r[0] in ("func", "class"):
                    return r[1]
                return None
            if not isinstance(expr, ast.Attribute):
                return None
            recv, attr = expr.value, expr.attr
            text = _dotted(recv)
            if text in self.receiver_types:
                t = self.receiver_types[text]
                if not t:
                    return f"dyn:{text}.{attr}"
                r = self.resolve_symbol(*t.rsplit(".", 1))      # "pipeline.SpecStore" -> its definition
                return self.method(r[1], attr) if r and r[0] == "class" else None
            if isinstance(recv, ast.Name):
                if recv.id in ("self", "cls") and cls:
                    return self.method(cls, attr)
                if recv.id in local_types:
                    return self.method(local_types[recv.id], attr)
                r = sym(recv.id)
                if r and r[0] == "module":
                    rr = self.resolve_symbol(r[1], attr)
                    return rr[1] if rr and rr[0] in ("func", "class") else None
                if r and r[0] == "class":
                    return self.method(r[1], attr)
            return None

        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                c = callee(n.func)
                if c:
                    out.add(c)
                    self.kinds.setdefault((node_id, c), "call")
                else:
                    untracked.add(ast.unparse(n.func))
                for a in list(n.args) + [k.value for k in n.keywords]:
                    if isinstance(a, ast.Name):
                        r = sym(a.id)
                        if r and r[0] == "func":
                            out.add(r[1])
                            self.kinds.setdefault((node_id, r[1]), "ref")
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id.isupper():
                r = sym(n.id)
                if r and r[0] == "const":
                    consts.add(r[1])
        return out

    def _local_types(self, fn, mod: str, imports: dict) -> dict:
        """{name: class id} for annotated parameters and locals assigned from one
        repository-class construction (``x = C(...)``, also inside ``a if b else c``)."""
        out: dict = {}

        def cls_of(expr) -> Optional[str]:
            name = _dotted(expr)
            if name is None:
                return None
            head, _, rest = name.partition(".")
            r = self.resolve_symbol(mod, head, imports)
            if r and r[0] == "class" and not rest:
                return r[1]
            if r and r[0] == "module" and rest and "." not in rest:
                rr = self.resolve_symbol(r[1], rest)
                return rr[1] if rr and rr[0] == "class" else None
            return None

        for a in ast.walk(fn):
            if isinstance(a, ast.arg) and a.annotation is not None:
                ann = a.annotation
                if isinstance(ann, ast.Subscript) and _dotted(ann.value) == "Optional":
                    ann = ann.slice
                c = cls_of(ann)
                if c:
                    out[a.arg] = c
        def returned(call) -> Optional[str]:
            """The repository class a called function is annotated to return."""
            if not isinstance(call.func, ast.Name):
                return None
            r = self.resolve_symbol(mod, call.func.id, imports)
            if not r or r[0] != "func":
                return None
            f, fmod, _ = self.definition(r[1])
            if f.returns is None:
                return None
            got = self.resolve_symbol(fmod, _dotted(f.returns) or "")
            return got[1] if got and got[0] == "class" else None

        def element(cls_id: str, attr: str) -> Optional[str]:
            """C of a dataclass field ``attr: list[C] / Sequence[C] / tuple[C, ...]``."""
            cmod, cname = cls_id.rsplit(".", 1)
            c = self.module(cmod)["classes"][cname]["node"]
            for st in c.body:
                if (isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)
                        and st.target.id == attr and isinstance(st.annotation, ast.Subscript)):
                    sl = st.annotation.slice
                    sl = sl.elts[0] if isinstance(sl, ast.Tuple) else sl
                    got = self.resolve_symbol(cmod, _dotted(sl) or "")
                    return got[1] if got and got[0] == "class" else None
            return None

        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                made = {cls_of(c.func) for c in ast.walk(n.value) if isinstance(c, ast.Call)}
                made.discard(None)
                if not made and isinstance(n.value, ast.Call):
                    made = {returned(n.value)} - {None}
                if len(made) == 1:
                    out[n.targets[0].id] = made.pop()
        for n in ast.walk(fn):              # for x in <typed local>.<list field>
            if (isinstance(n, ast.For) and isinstance(n.target, ast.Name) and isinstance(n.iter, ast.Attribute)
                    and isinstance(n.iter.value, ast.Name) and n.iter.value.id in out):
                el = element(out[n.iter.value.id], n.iter.attr)
                if el:
                    out[n.target.id] = el
        return out

    # -- reachability ----------------------------------------------------------
    def reach(self, *roots: str) -> set:
        seen, todo = set(), list(roots)
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            todo.extend(self.edges(n) - seen)
        return seen

    def callers(self, nodes: set) -> dict:
        """{callee: {caller}} restricted to ``nodes``."""
        out: dict = {}
        for c in nodes:
            for e in self.edges(c):
                out.setdefault(e, set()).add(c)
        return out


def shared_surface(g: CallGraph, ev_root: str, rp_root: str, home: str) -> dict:
    """Split what two roots reach.

    - ``shared``: every node reachable from both roots (the closure);
    - ``shared_entry``: shared nodes that BOTH sides enter directly, i.e. with a
      caller outside the shared code on the evaluator side and one on the
      replay side (below an entry everything is shared by construction);
    - ``evaluator_only`` / ``replay_only``: nodes reachable from one side only
      that a ``home``-module function of that side calls directly;
    - ``shared_constants``: upper-case constants of ``home`` read directly by
      ``home`` functions of both sides."""
    ev, rp = g.reach(ev_root), g.reach(rp_root)
    shared = (ev & rp) - {ev_root, rp_root}
    ev_side, rp_side = (ev - rp) | {ev_root}, (rp - ev) | {rp_root}
    callers = g.callers(ev | rp)
    entry = {n for n in shared if callers.get(n, set()) & ev_side and callers.get(n, set()) & rp_side}
    mine = lambda c: c.startswith(home + ".")              # noqa: E731
    direct = lambda only, side: {n for n in only if any(c in side and mine(c) for c in callers.get(n, ()))}  # noqa: E731

    def consts(side):
        return set().union(*[g.consts.get(n, set()) for n in side if mine(n)] or [set()])
    return {"evaluator": ev, "replay": rp, "shared": shared, "shared_entry": entry,
            "evaluator_only": direct(ev - rp, ev_side), "replay_only": direct(rp - ev, rp_side),
            "shared_constants": {c for c in consts(ev_side) & consts(rp_side) if c.startswith(home + ".")}}
