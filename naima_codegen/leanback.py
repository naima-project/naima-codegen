"""The Lean 4 back end: the refined Core of a model, with its observers, as a
Lean 4 transition system on which its properties can be proved for every size.

What is translated is the linear process mCRL2 itself makes (§3.2.2), read by
`lps.read_lps`: never the model's text. The system is the refined Core of
§3.7.4 with the observers of the model's observers.mcrl2 inserted as for the
refined systems of §3.9.7 (QUIESCENT read as the refined Core's Q), and the
initial state of model/codegen-init.mcrl2. The environment is left out: every
input is free, so what is proved holds for every environment and every
instance. The id sorts stay unbounded.

The translation, in the order the Lean file declares it:

- a structured sort becomes an `inductive`, its projections and recognisers
  functions on it;
- every user map, and every partial operation of mCRL2's library the model
  uses (`head`, `tail`, `rhead`, `rtail`, `.`, `Int2Nat`), becomes a field of
  the structure `Data`; its equations, and the library's equations for the
  partial operations, become the fields of `Laws D`, one per equation, as
  `∀ vars, cond = true → lhs = rhs`. A theorem then holds for every
  interpretation of the maps that satisfies the model's equations: what mCRL2
  leaves unspecified (`head([])`) stays unspecified, and no map needs a
  termination proof;
- the process parameters become the structure `State`, the action labels the
  inductive `Act`, each summand one constructor of the inductive `Step D`:
  its sum variables are the constructor's arguments, its condition a
  hypothesis, its next state `{ st with p := e, ... }`;
- `initial` is the initial state, with the configuration parameters (and the
  ones named `free`) left as arguments;
- a property of the form `[true* . a] false` becomes the proposition that no
  state reachable from `initial`, for any data satisfying the laws and any
  configuration, has an `a` step.

mCRL2's Bool is Lean's `Bool`, Nat and Pos are `Nat`, Int is `Int`, `List(T)`
is `List T`, `FSet(T)` is `T → Bool` (its characteristic function), and a
function sort is a curried Lean function. Equality is classical (`Mcrl2.eqb`),
as mCRL2's `==` is equality of values. Anything else is refused with its name,
never guessed.
"""

from __future__ import annotations

import re
import sys

from . import expr as E
from .errors import Diagnostic, GeneratorError
from .lps import Lps

LEAN_KEYWORDS = frozenset("""
at by do else end fun from have if in let match mut open show then where with for unless return try catch finally
break continue deriving instance class structure inductive theorem def abbrev namespace section variable universe
import export local private protected partial macro syntax calc suffices obtain nomatch nofun forall exists
Type Prop Sort fun λ example axiom opaque noncomputable mutual set_option attribute termination_by decreasing_by
""".split())
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_']*\Z")
_RESERVED = frozenset({"st", "hc", "D", "Data", "Laws", "State", "Act", "Step", "initial", "Mcrl2"})

NAT, POS, INT, BOOL = ("id", "Nat"), ("id", "Pos"), ("id", "Int"), ("id", "Bool")
_NUMERIC = {"Nat", "Pos", "Int"}
PARTIAL = ("head", "tail", "rhead", "rtail", ".")


def ident(name: str) -> str:
    """A Lean identifier for an mCRL2 name."""
    if _IDENT.match(name) and name not in LEAN_KEYWORDS:
        return name
    return "«%s»" % name.replace("»", "_")


def _sort_tag(s: tuple) -> str:
    """A name fragment for a sort: List(Eval) is List_Eval."""
    if s[0] == "id":
        return "Nat" if s[1] == "Pos" else s[1]
    if s[0] == "->":
        return "F_" + "_".join(_sort_tag(d) for d in s[1]) + "_" + _sort_tag(s[2])
    return s[0] + "_" + _sort_tag(s[1])


class _Refuse(Exception):
    pass


class Translator:
    """The Lean text of one linear process (`lean_text`)."""

    def __init__(self, lps: Lps, ns: str):
        self.lps, self.ns = lps, ns
        self.problems: list = []
        self.structs = lps.structs
        self.ctors = {}  # constructor name -> (sort, arity)
        self.projs = {}  # projection name -> (sort, constructor, index)
        self.recogs = {}  # recogniser name -> (sort, constructor)
        for sname, st in lps.structs.items():
            for cname, fields, rec in st.constructors:
                self.ctors[cname] = (sname, len(fields))
                for i, (p, _) in enumerate(fields):
                    if p:
                        self.projs.setdefault(p, []).append((sname, cname, i))
                if rec:
                    self.recogs[rec] = (sname, cname)
        # User maps, overloads told apart by sort.
        self.maps = {}
        seen: dict = {}
        for m in lps.maps:
            k = seen.get(m.name, 0)
            seen[m.name] = k + 1
            self.maps[(m.name, E.sort_str(m.sort))] = m.name if k == 0 else "%s_%d" % (m.name, k + 1)
        self.partials: dict = {}  # field name -> (kind, sort) of library partial operations used
        self.params = {n for n, _ in lps.params}

    # ------------------------------------------------------------ sorts

    def sort(self, s: tuple) -> str:
        if s[0] == "id":
            n = s[1]
            if n in ("Nat", "Pos"):
                return "Nat"
            if n in ("Int", "Bool"):
                return n
            if n in self.structs:
                return ident(n)
            if n in self.lps.aliases:
                return self.sort(self.lps.aliases[n])
            raise _Refuse("sort %s" % n)
        if s[0] == "List":
            return "(List %s)" % self.sort(s[1])
        if s[0] == "FSet":
            return "(Mcrl2.FSet %s)" % self.sort(s[1])
        if s[0] == "->":
            return "(%s)" % " → ".join([self.sort(d) for d in s[1]] + [self.sort(s[2])])
        raise _Refuse("sort %s" % E.sort_str(s))

    def _base(self, s: tuple) -> str:
        if s[0] == "id" and s[1] in self.lps.aliases:
            return self._base(self.lps.aliases[s[1]])
        return s[1] if s[0] == "id" else s[0]

    # ------------------------------------------------------------ expressions

    def sort_of(self, e) -> tuple:
        if isinstance(e, (E.Var, E.Op, E.Num)):
            return e.sort
        if isinstance(e, E.App):
            h = self.sort_of(e.head)
            return h[2] if h[0] == "->" else h
        if isinstance(e, E.Whr):
            return self.sort_of(e.body)
        if isinstance(e, E.Bind):
            if e.kind == "Lambda":
                return ("->", tuple(v.sort for v in e.vars), self.sort_of(e.body))
            return BOOL
        raise _Refuse("expression %r" % (e,))

    def var(self, v: E.Var, bound: set) -> str:
        if v.name in bound or v.name not in self.params:
            return ident(v.name)
        return "st.%s" % ident(v.name)

    def num(self, n: E.Num) -> str:
        if self._base(n.sort) == "Int":
            return "(%d : Int)" % n.value if n.value >= 0 else "(-%d : Int)" % -n.value
        if self._base(n.sort) in ("Nat", "Pos"):
            return "(%d : Nat)" % n.value
        raise _Refuse("number of sort %s" % E.sort_str(n.sort))

    def partial(self, kind: str, elem: tuple) -> str:
        tag = {"head": "head", "tail": "tail", "rhead": "rhead", "rtail": "rtail", ".": "at", "Int2Nat": "Int2Nat"}[kind]
        name = tag if kind == "Int2Nat" else "%s_%s" % (tag, _sort_tag(elem))
        self.partials[name] = (kind, elem)
        return "D.%s" % name

    def ex(self, e, bound: frozenset = frozenset()) -> str:
        if isinstance(e, E.Var):
            return self.var(e, bound)
        if isinstance(e, E.Num):
            return self.num(e)
        if isinstance(e, E.Op):
            return self.op(e, [], bound)
        if isinstance(e, E.App):
            if isinstance(e.head, E.Op):
                return self.op(e.head, list(e.args), bound)
            return "(%s %s)" % (self.ex(e.head, bound), " ".join(self.ex(a, bound) for a in e.args))
        if isinstance(e, E.Whr):
            names = {v.name for v, _ in e.defs}
            inner = bound | names
            if any(names & E.free_vars(d) for _, d in e.defs):
                return "((fun %s => %s) %s)" % (
                    " ".join("(%s : %s)" % (ident(v.name), self.sort(v.sort)) for v, _ in e.defs),
                    self.ex(e.body, inner), " ".join(self.ex(d, bound) for _, d in e.defs))
            lets = "".join("let %s : %s := %s; " % (ident(v.name), self.sort(v.sort), self.ex(d, bound)) for v, d in e.defs)
            return "(%s%s)" % (lets, self.ex(e.body, inner))
        if isinstance(e, E.Bind):
            vs = " ".join("(%s : %s)" % (ident(v.name), self.sort(v.sort)) for v in e.vars)
            inner = bound | {v.name for v in e.vars}
            body = self.ex(e.body, inner)
            if e.kind == "Lambda":
                return "(fun %s => %s)" % (vs, body)
            if e.kind == "Forall":
                return "(Mcrl2.pb (∀ %s, %s = true))" % (vs, body)
            if e.kind == "Exists":
                return "(Mcrl2.pb (∃ %s, %s = true))" % (vs, body)
            raise _Refuse("binder %s" % e.kind)
        raise _Refuse("expression %r" % (e,))

    def op(self, o: E.Op, args: list, bound) -> str:
        n, s = o.name, o.sort
        a = [self.ex(x, bound) for x in args]
        dom = s[1] if s[0] == "->" else ()
        cod = s[2] if s[0] == "->" else s
        key = (n, E.sort_str(s))
        if key in self.maps:
            return self._app("D.%s" % ident(self.maps[key]), a)
        if n in self.ctors and self.ctors[n][0] == self._base(cod) and len(a) == self.ctors[n][1]:
            return self._app("%s.%s.%s" % (self.ns, ident(self.ctors[n][0]), ident(n)), a)
        if n in self.projs and len(a) == 1:
            return self._app("%s.%s" % (self.ns, ident(self._proj_name(n, dom[0]))), a)
        if n in self.recogs and len(a) == 1:
            return self._app("%s.%s" % (self.ns, ident(n)), a)
        b0 = self._base(dom[0]) if dom else None
        if n == "true" or n == "false":
            return n
        if n == "!" and len(a) == 1:
            return "(!%s)" % a[0]
        if n in ("&&", "||") and len(a) == 2:
            return "(%s %s %s)" % (a[0], n, a[1])
        if n == "=>" and len(a) == 2:
            return "(!%s || %s)" % (a[0], a[1])
        if n == "if" and len(a) == 3:
            return "(Mcrl2.cnd %s %s %s)" % tuple(a)
        if n == "==" and len(a) == 2:
            return "(Mcrl2.eqb %s %s)" % tuple(a)
        if n == "!=" and len(a) == 2:
            return "(!Mcrl2.eqb %s %s)" % tuple(a)
        if n in ("<", "<=", ">", ">=") and len(a) == 2:
            if b0 in _NUMERIC:
                return "(Mcrl2.pb (%s %s %s))" % (self._numarg(args[0], a[0]), {"<": "<", "<=": "≤", ">": ">", ">=": "≥"}[n],
                                                 self._numarg(args[1], a[1]))
            if b0 == "List" and self._base(dom[0][1]) in ("Nat", "Pos"):
                x, y = (a[0], a[1]) if n in ("<", "<=") else (a[1], a[0])
                return "(Mcrl2.%s %s %s)" % ("llt" if n in ("<", ">") else "lle", x, y)
            raise _Refuse("%s on %s" % (n, E.sort_str(dom[0])))
        if n in ("+", "*", "-") and len(a) == 2 and b0 == "FSet":
            return "(Mcrl2.%s %s %s)" % ({"+": "funion", "*": "finter", "-": "fdiff"}[n], a[0], a[1])
        if n in ("+", "*") and len(a) == 2 and b0 in _NUMERIC:
            return "(%s %s %s)" % (self._numarg(args[0], a[0], cod), n, self._numarg(args[1], a[1], cod))
        if n == "-" and len(a) == 2 and b0 in _NUMERIC and self._base(cod) == "Int":
            return "(%s - %s)" % (self._numarg(args[0], a[0], INT), self._numarg(args[1], a[1], INT))
        if n in ("div", "mod") and len(a) == 2 and b0 in ("Nat", "Pos"):
            return "(%s %s %s)" % (a[0], "/" if n == "div" else "%", a[1])
        if n in ("min", "max") and len(a) == 2 and b0 in _NUMERIC:
            return "(%s %s %s)" % ("min" if n == "min" else "max", self._numarg(args[0], a[0], cod), self._numarg(args[1], a[1], cod))
        if n in ("Pos2Nat", "@cNat") and len(a) == 1:
            return a[0]
        if n in ("Nat2Int", "Pos2Int", "@cInt") and len(a) == 1:
            return "(Int.ofNat %s)" % a[0]
        if n == "@cNeg" and len(a) == 1:
            return "(-(Int.ofNat %s))" % a[0]
        if n == "Int2Nat" and len(a) == 1:
            return "(%s %s)" % (self.partial("Int2Nat", INT), a[0])
        if n == "[]" and not a and cod[0] == "List":
            return "([] : %s)" % self.sort(cod)
        if n == "|>" and len(a) == 2:
            return "(%s :: %s)" % tuple(a)
        if n == "<|" and len(a) == 2:
            return "(%s ++ [%s])" % tuple(a)
        if n == "++" and len(a) == 2:
            return "(%s ++ %s)" % tuple(a)
        if n == "#" and len(a) == 1 and b0 == "List":
            return "(List.length %s)" % a[0]
        if n in PARTIAL and b0 == "List" and len(a) == (2 if n == "." else 1):
            return "(%s %s)" % (self.partial(n, dom[0][1]), " ".join(a))
        if n == "in" and len(a) == 2 and self._base(dom[1]) == "List":
            return "(Mcrl2.pb (%s ∈ %s))" % tuple(a)
        if n == "in" and len(a) == 2 and self._base(dom[1]) == "FSet":
            return "(%s %s)" % (a[1], a[0])
        if n in ("{}", "@fset_empty") and not a and cod[0] == "FSet":
            return "(Mcrl2.fempty : %s)" % self.sort(cod)
        if n in ("@fset_insert", "@fset_cons") and len(a) == 2:
            return "(Mcrl2.fins %s %s)" % tuple(a)
        if n == "@func_update" and len(a) == 3:
            return "(Mcrl2.upd %s %s %s)" % tuple(a)
        raise _Refuse("operation %s: %s" % (n, E.sort_str(s)))

    def _numarg(self, e, text: str, target: tuple | None = None) -> str:
        """text, cast to Int when e is a Nat and the operation is on Int."""
        if target is not None and self._base(target) == "Int" and self._base(self.sort_of(e)) in ("Nat", "Pos"):
            return "(Int.ofNat %s)" % text
        return text

    @staticmethod
    def _app(f: str, a: list) -> str:
        return "(%s %s)" % (f, " ".join(a)) if a else f

    def _proj_name(self, p: str, s: tuple) -> str:
        cands = self.projs[p]
        if len({c[0] for c in cands}) == 1:
            return p
        return "%s_%s" % (p, _sort_tag(s))

    # ------------------------------------------------------------ declarations

    def _try(self, where: str, fn):
        try:
            return fn()
        except _Refuse as e:
            self.problems.append(Diagnostic("L1", "model", where, "lean", "no Lean meaning given to %s" % e))
            return "sorry_unsupported"

    def structs_text(self) -> str:
        out = []
        for sname, st in self.structs.items():
            out.append("inductive %s where" % ident(sname))
            for cname, fields, _ in st.constructors:
                fs = " ".join("(%s : %s)" % (ident(p or "a%d" % i), self._try("sort " + sname, lambda t=t: self.sort(t)))
                              for i, (p, t) in enumerate(fields))
                out.append("  | %s%s" % (ident(cname), (" " + fs) if fs else ""))
            out.append("")
        for p, cands in self.projs.items():
            by_sort: dict = {}
            for sname, cname, i in cands:
                by_sort.setdefault(sname, []).append((cname, i))
            for sname, cs in by_sort.items():
                st = self.structs[sname]
                if len(cs) != len(st.constructors):
                    self.problems.append(Diagnostic("L1", "model", "projection " + p, "lean",
                                                    "no Lean meaning given to a projection that is partial"))
                    continue
                name = self._proj_name(p, ("id", sname))
                rt = self.sort(st.constructors[0][1][cs[0][1]][1])
                out.append("def %s : %s → %s" % (ident(name), ident(sname), rt))
                for cname, i in cs:
                    k = dict((c[0], len(c[1])) for c in st.constructors)[cname]
                    pats = " ".join("x" if j == i else "_" for j in range(k))
                    out.append("  | .%s %s => x" % (ident(cname), pats))
        for r, (sname, cname) in self.recogs.items():
            out.append("def %s : %s → Bool" % (ident(r), ident(sname)))
            k = {c[0]: len(c[1]) for c in self.structs[sname].constructors}[cname]
            out.append("  | .%s%s => true" % (ident(cname), " _" * k))
            out.append("  | _ => false")
        return "\n".join(out) + "\n"

    def data_text(self, laws: list) -> str:
        out = ["/-- The maps of the model, and the partial operations of mCRL2's library it uses. -/", "structure Data where"]
        for m in self.lps.maps:
            name = self.maps[(m.name, E.sort_str(m.sort))]
            out.append("  %s : %s" % (ident(name), self._try("map " + m.name, lambda m=m: self._map_sort(m.sort))))
        for name, (kind, elem) in sorted(self.partials.items()):
            out.append("  %s : %s" % (ident(name), self._partial_sort(kind, elem)))
        out.append("")
        out.append("/-- The model's equations, and mCRL2's equations for the partial operations. -/")
        out.append("structure Laws (D : Data) : Prop where")
        out.extend(laws)
        for name, (kind, elem) in sorted(self.partials.items()):
            out.extend(self._partial_laws(name, kind, elem))
        return "\n".join(out) + "\n"

    def _map_sort(self, s: tuple) -> str:
        t = self.sort(s)
        return t[1:-1] if s[0] == "->" else t

    def _partial_sort(self, kind: str, elem: tuple) -> str:
        if kind == "Int2Nat":
            return "Int → Nat"
        e, l = self.sort(elem), "List %s" % self.sort(elem)
        return {"head": "%s → %s" % (l, e), "rhead": "%s → %s" % (l, e), "tail": "%s → %s" % (l, l),
                "rtail": "%s → %s" % (l, l), ".": "%s → Nat → %s" % (l, e)}[kind]

    def _partial_laws(self, name: str, kind: str, elem: tuple) -> list:
        if kind == "Int2Nat":
            return ["  %s_ofNat : ∀ n : Nat, D.%s (Int.ofNat n) = n" % (name, name)]
        e = self.sort(elem)
        if kind == "head":
            return ["  %s_cons : ∀ (x : %s) (l : List %s), D.%s (x :: l) = x" % (name, e, e, name)]
        if kind == "tail":
            return ["  %s_cons : ∀ (x : %s) (l : List %s), D.%s (x :: l) = l" % (name, e, e, name)]
        if kind == "rhead":
            return ["  %s_snoc : ∀ (x : %s) (l : List %s), D.%s (l ++ [x]) = x" % (name, e, e, name)]
        if kind == "rtail":
            return ["  %s_snoc : ∀ (x : %s) (l : List %s), D.%s (l ++ [x]) = l" % (name, e, e, name)]
        return ["  %s_zero : ∀ (x : %s) (l : List %s), D.%s (x :: l) 0 = x" % (name, e, e, name),
                "  %s_succ : ∀ (x : %s) (l : List %s) (n : Nat), D.%s (x :: l) (n + 1) = D.%s l n" % (name, e, e, name, name)]

    def laws_text(self) -> list:
        out, count = [], {}
        for q in self.lps.equations:
            head = q.lhs.head if isinstance(q.lhs, E.App) else q.lhs
            base = head.name if isinstance(head, E.Op) else "eqn"
            k = count.get(base, 0) + 1
            count[base] = k
            fv = E.free_vars(q.lhs) | E.free_vars(q.rhs) | (E.free_vars(q.cond) if q.cond is not None else set())
            vs = [v for v in q.vars if v.name in fv]
            bound = frozenset(v.name for v in q.vars)

            def law(q=q, vs=vs, bound=bound):
                binders = " ".join("(%s : %s)" % (ident(v.name), self.sort(v.sort)) for v in vs)
                cond = "" if q.cond is None or (isinstance(q.cond, E.Op) and q.cond.name == "true") else \
                    "%s = true → " % self.ex(q.cond, bound)
                return "%s%s%s = %s" % ("∀ %s, " % binders if binders else "", cond, self.ex(q.lhs, bound), self.ex(q.rhs, bound))
            out.append("  %s : %s" % (ident("%s_eq%d" % (base, k)), self._try("equation of " + base, law)))
        return out

    def state_text(self) -> str:
        out = ["/-- The process parameters. -/", "structure State where"]
        for n, s in self.lps.params:
            out.append("  %s : %s" % (ident(n), self._try("parameter " + n, lambda s=s: self.sort(s))))
        return "\n".join(out) + "\n"

    def acts(self) -> dict:
        names, seen = {}, {}
        for a in self.lps.acts:
            k = seen.get(a.name, 0)
            seen[a.name] = k + 1
            names[(a.name, tuple(a.sorts))] = a.name if k == 0 else "%s_%d" % (a.name, k + 1)
        return names

    def act_text(self, acts: dict) -> str:
        out = ["/-- The action labels; `tau` is the internal step. -/", "inductive Act where", "  | tau"]
        for (name, sorts), lean in acts.items():
            args = " ".join("(a%d : %s)" % (i, self._try("action " + name, lambda s=s: self.sort(s))) for i, s in enumerate(sorts))
            out.append("  | %s%s" % (ident(lean), (" " + args) if args else ""))
        return "\n".join(out) + "\n"

    def step_text(self, acts: dict) -> str:
        out = ["/-- One constructor per summand of the linear process, in its order. -/",
               "inductive Step (D : Data) : State → Act → State → Prop where"]
        for i, s in enumerate(self.lps.summands):
            where = "summand %d (%s)" % (i, s.name or s.label)
            if s.n_actions > 1 or s.timed:
                self.problems.append(Diagnostic("L1", "model", where, "lean", "no Lean meaning given to a multi-action or a timed summand"))
                continue
            sv = [(n, srt) for n, srt in s.sumvars]
            bound = frozenset(n for n, _ in sv)
            clash = [n for n, _ in sv if n in _RESERVED]
            if clash:
                self.problems.append(Diagnostic("L1", "model", where, "lean", "sum variable named %s" % ", ".join(clash)))
                continue

            def text(s=s, sv=sv, bound=bound):
                binders = "".join(" (%s : %s)" % (ident(n), self.sort(srt)) for n, srt in sv)
                cond = self.ex(s.cond, bound)
                if s.label:
                    act = self._app("Act.%s" % ident(acts[(s.label, tuple(s.sorts))]), [self.ex(x, bound) for x in s.args])
                else:
                    act = "Act.tau"
                if s.assigns:
                    upd = "{ st with %s }" % ", ".join("%s := %s" % (ident(p), self.ex(x, bound)) for p, x in s.assigns)
                else:
                    upd = "st"
                return "  | s%d (st : State)%s\n      (hc : %s = true) :\n      Step D st %s\n        %s" % (
                    i, binders, cond, act, upd)
            out.append("  -- %s" % (s.name or s.label or "tau"))
            out.append(self._try(where, text))
        return "\n".join(out) + "\n"

    def init_text(self, free: list) -> str:
        args = " ".join("(%s : %s)" % (ident(n), self.sort(s)) for n, s in self.lps.params if n in free)
        fields = []
        for (n, s), e in zip(self.lps.params, self.lps.init):
            fields.append("  %s := %s" % (ident(n), ident(n) if n in free else self._try("init " + n, lambda e=e: self.ex(e))))
        return ("/-- The initial state; the configuration parameters are left free. -/\ndef initial %s : State where\n%s\n"
                % (args, "\n".join(fields)))


PRELUDE = r'''/-
  mCRL2's library, as the Lean translation of naima-codegen uses it.
  GENERATED by naima-codegen (leanback.py). Do not edit.
-/

namespace Mcrl2

/-- A proposition as a Bool, classically: mCRL2's Bool-valued tests of
equality and membership are tests of values, decidable or not. -/
noncomputable def pb (p : Prop) : Bool := @decide p (Classical.propDecidable p)

@[simp] theorem pb_eq_true (p : Prop) : pb p = true ↔ p := by
  unfold pb; exact @decide_eq_true_iff p (Classical.propDecidable p)

@[simp] theorem pb_eq_false (p : Prop) : pb p = false ↔ ¬p := by
  unfold pb; exact @decide_eq_false_iff_not p (Classical.propDecidable p)

/-- mCRL2's `==`. -/
noncomputable def eqb {α : Sort _} (a b : α) : Bool := pb (a = b)

@[simp] theorem eqb_eq_true {α : Sort _} (a b : α) : eqb a b = true ↔ a = b := pb_eq_true _
@[simp] theorem eqb_eq_false {α : Sort _} (a b : α) : eqb a b = false ↔ a ≠ b := pb_eq_false _

/-- mCRL2's `if(c, a, b)`. -/
def cnd {α : Sort _} (c : Bool) (a b : α) : α := cond c a b

@[simp] theorem cnd_true {α : Sort _} (a b : α) : cnd true a b = a := rfl
@[simp] theorem cnd_false {α : Sort _} (a b : α) : cnd false a b = b := rfl

/-- mCRL2's `f[x -> v]`. -/
noncomputable def upd {α : Sort _} {β : Sort _} (f : α → β) (x : α) (v : β) : α → β :=
  fun y => cond (eqb y x) v (f y)

@[simp] theorem upd_same {α : Sort _} {β : Sort _} (f : α → β) (x : α) (v : β) : upd f x v x = v := by
  simp [upd]

theorem upd_other {α : Sort _} {β : Sort _} (f : α → β) (x y : α) (v : β) (h : y ≠ x) : upd f x v y = f y := by
  simp [upd, eqb, h]

/-- mCRL2's `FSet(T)`, as its characteristic function. -/
def FSet (α : Type) : Type := α → Bool

def fempty {α : Type} : FSet α := fun _ => false
noncomputable def fins {α : Type} (x : α) (s : FSet α) : FSet α := fun y => eqb y x || s y
def funion {α : Type} (s t : FSet α) : FSet α := fun y => s y || t y
def finter {α : Type} (s t : FSet α) : FSet α := fun y => s y && t y
def fdiff {α : Type} (s t : FSet α) : FSet α := fun y => s y && !t y

/-- mCRL2's `<` and `<=` on `List(Nat)`: lexicographic, a proper prefix first. -/
def llt : List Nat → List Nat → Bool
  | [], [] => false
  | [], _ :: _ => true
  | _ :: _, [] => false
  | a :: s, b :: t => decide (a < b) || (decide (a = b) && llt s t)

def lle : List Nat → List Nat → Bool
  | [], _ => true
  | _ :: _, [] => false
  | a :: s, b :: t => decide (a < b) || (decide (a = b) && lle s t)

/-- The states reachable from `i` by `step`. -/
inductive Reach {S A : Type} (step : S → A → S → Prop) (i : S) : S → Prop where
  | init : Reach step i i
  | step {s t : S} {a : A} : Reach step i s → step s a t → Reach step i t

end Mcrl2
'''


def box_never(mcf: str) -> str | None:
    """The label `a` of a formula `[true* . a] false` (comments and white space
    aside), or None for any other formula."""
    text = "".join(line.split("%", 1)[0] for line in mcf.splitlines())
    toks = "".join(text.split())
    if toks.startswith("[true*.") and toks.endswith("]false"):
        label = toks[len("[true*."):-len("]false")]
        if _IDENT.match(label):
            return label
    return None


def lean_text(lps: Lps, ns: str, free: list, properties: dict, source: str) -> str:
    """The Lean module of the linear process lps; properties is {name: mcf text}."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    t = Translator(lps, ns)
    acts = t.acts()
    structs = t.structs_text()
    laws = t.laws_text()
    state = t.state_text()
    act = t.act_text(acts)
    step = t.step_text(acts)
    init = t.init_text(free)
    data = t.data_text(laws)
    labels = {name: lean for (name, _), lean in acts.items()}
    props = []
    for pname, mcf in sorted(properties.items()):
        a = box_never(mcf)
        if a is None or a not in labels or any(n == a and s for (n, s) in acts):
            props.append("-- property %s: not of the form [true* . a] false with a an action without arguments; not translated\n" % pname)
            continue
        args = " ".join("(%s : %s)" % (ident(n), t.sort(s)) for n, s in lps.params if n in free)
        names = " ".join(ident(n) for n, s in lps.params if n in free)
        props.append(
            "/-- The property %s, `[true* . %s] false`: no reachable state has a `%s` step,\n"
            "for every interpretation of the maps that satisfies the model's equations\n"
            "and every configuration. -/\n"
            "def prop_%s : Prop :=\n  ∀ (D : Data), Laws D → ∀ %s (s : State),\n"
            "    Mcrl2.Reach (Step D) (initial %s) s → ∀ t, ¬ Step D s Act.%s t\n"
            % (pname, a, a, pname.replace("-", "_"), args, names, ident(labels[a])))
    if t.problems:
        raise GeneratorError(t.problems)
    head = ("/-\n  GENERATED by naima-codegen (leanback.py) from %s. Do not edit.\n"
            "  The refined Core with its observers, as a Lean 4 transition system.\n-/\n"
            "import Mcrl2\n\nset_option maxHeartbeats 0\nset_option linter.unusedVariables false\n\nnoncomputable section\n\nnamespace %s\n\n" % (source, ns))
    return head + "\n".join([structs, data, state, act, init, step] + props) + "\nend %s\n" % ns


def build(proj, free: list = (), namespace: str = "Model", tools=None) -> dict:
    """The Lean files of the project's refined Core with its observers:
    {"Mcrl2.lean": the library prelude, "<namespace>.lean": the system}.
    free names parameters left free in `initial` besides the configuration ones."""
    from .frontend import linearisation_input, run
    from .lps import read_lps
    from .outputs import refined
    from .source import read_core
    from .systems import with_observers
    from .toolchain import Toolchain

    tools = tools or Toolchain()
    model = run(proj, tools)
    ref = refined(proj, model)
    core = proj.path(proj.m("core.mcrl2")).read_text(encoding="utf-8")
    obs = proj.path(proj.m("observers.mcrl2"))
    text = ref.refined
    if obs.is_file():
        text, _ = with_observers(text, obs.read_text(encoding="utf-8"), ref.quiescent)
    init = proj.path(proj.m("codegen-init.mcrl2")).read_text(encoding="utf-8")
    k = init.rstrip().rfind(")")
    init = init[:k] + ref.init_suffix + init[k:]
    aterm = tools.linearise(linearisation_input(text, init, read_core(core), model.config), "generated/model/core-refined.mcrl2")
    lps = read_lps(aterm)
    config = [p.get("name") for p in model.config.parameters if p.get("role") == "config"]
    names = {n for n, _ in lps.params}
    unknown = [n for n in free if n not in names]
    if unknown:
        raise GeneratorError([Diagnostic("L2", "lean", "--free", "lean", "not a parameter: %s" % ", ".join(unknown))])
    props = {}
    pdir = proj.path(proj.m("properties"))
    if pdir.is_dir():
        props = {f.stem: f.read_text(encoding="utf-8") for f in sorted(pdir.glob("*.mcf"))}
    keep = [n for n, _ in lps.params if n in set(config) | set(free)]
    source = "%s/core.mcrl2, policy.toml, observers.mcrl2 and codegen-init.mcrl2" % proj.model
    return {"Mcrl2.lean": PRELUDE, namespace + ".lean": lean_text(lps, namespace, keep, props, source)}
