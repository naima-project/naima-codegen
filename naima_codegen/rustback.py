"""The Rust backend: the crate `<generated>/rust/<rust_crate>`
(docs/spec.md §3.8, §3.11).

It emits what the Python backend does: the types of the model's sorts, the
configuration, the initial state (§3.8.1, `init` included), every user map with
the walks compiled into loops (§3.4.8), the acceptor of the unrefined Core
(§3.8.9), `encode`/`decode` (§3.8.10) and, with a policy, the stepper (§3.8).
Not implemented yet: the catalogue's representations and widths (§3.10;
numbers are `i128` here, checked, §3.12.4), observables, the Python binding
(§3.11.4).

A summand's right sides are evaluated before anything is written (§3.8.4): a
function-sorted parameter's new value is built as an overlay over the old one
(§3.4.8) and committed after every right side is evaluated; inside a walk, an
accumulator's writes are collected first and applied before the next element.

No recursion in the generated code (walks are loops), and none here: every
compilation step is a loop over an explicit stack.
"""

from __future__ import annotations

import re

from .expr import App, Bind, Num, Op, Var, Whr, show, walk
from .frontend import INIT_PREFIX
from .functions import is_fun, mentions
from .sortedlists import Sorted
from .lower import Types, g10, plan_summand, width_range

from .header import block as _header_block  # noqa: E402
HEADER = _header_block("//")

_KEYWORDS = {"as", "break", "const", "continue", "crate", "else", "enum", "extern", "false", "fn", "for", "if", "impl",
             "in", "let", "loop", "match", "mod", "move", "mut", "pub", "ref", "return", "self", "Self", "static",
             "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while", "async", "await", "dyn",
             "abstract", "become", "box", "do", "final", "macro", "override", "priv", "typeof", "unsized", "virtual",
             "yield", "try", "gen"}
# Type names the generated crate or the prelude uses.
_TAKEN = {"Err", "Ok", "Option", "Some", "None", "Result", "Vec", "String", "Box", "Fun", "Ov", "Get", "GetSet",
          "StepError", "Reject", "Config", "State", "Event", "Action", "Entry", "Acceptor", "S", "BTreeMap",
          "BTreeSet", "M2"}

RUNTIME = r'''
#![forbid(unsafe_code)]
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::indexing_slicing,
        clippy::arithmetic_side_effects)]
#![allow(non_camel_case_types, non_snake_case, non_upper_case_globals, unused_mut, unused_variables,
         unused_parens, clippy::all, clippy::pedantic, dead_code, unreachable_patterns, irrefutable_let_patterns,
         missing_debug_implementations, unreachable_pub)]

use std::collections::{BTreeMap, BTreeSet, VecDeque};

/// An error of the generated code (docs/spec.md §3.14.2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StepError {
    Overflow(&'static str),
    Undefined(&'static str),
    /// No input summand accepts the event; the state is unchanged (docs/spec.md §3.8.2).
    Refused(Event),
    /// More than 2^24 actions in one step (§3.8.3).
    StepLimit,
    /// An event in a state that is not quiescent (§3.8.2, debug builds).
    NotQuiescent,
}

/// An index of the policy (docs/spec.md §3.7.6, §3.10.3): per class an
/// ordered set of (order key, id); the order key is the id (least), the key's
/// value (key), minus the entry stamp (newest) or the stamp (oldest).
#[derive(Debug, Clone, Default)]
pub struct Ix {
    order: u8,
    ent: BTreeMap<u32, (usize, i128)>,
    sets: Vec<BTreeSet<(i128, u32)>>,
    stamp: i128,
}

impl Ix {
    pub fn new(order: u8, ncls: usize) -> Ix {
        Ix { order, ent: BTreeMap::new(), sets: vec![BTreeSet::new(); ncls], stamp: 0 }
    }

    /// The index at one id after a write: member or not, its class and key (§3.8.3).
    pub fn refresh(&mut self, v: u32, m: bool, c: usize, k: i128) -> Result<(), StepError> {
        let old = self.ent.get(&v).copied();
        if !m {
            if let Some((oc, ok)) = old {
                self.ent.remove(&v);
                if let Some(set) = self.sets.get_mut(oc) {
                    set.remove(&(ok, v));
                }
            }
            return Ok(());
        }
        let ok = match (self.order, old) {
            (0, _) => i128::from(v),
            (1 | 4, _) => k,
            (_, Some((_, o))) => o,
            (o, None) => {
                self.stamp = self.stamp.checked_add(1).ok_or(StepError::Overflow("stamp"))?;
                if o == 2 { self.stamp.checked_neg().ok_or(StepError::Overflow("stamp"))? } else { self.stamp }
            }
        };
        if old != Some((c, ok)) {
            if let Some((oc, o)) = old {
                if let Some(set) = self.sets.get_mut(oc) {
                    set.remove(&(o, v));
                }
            }
            self.ent.insert(v, (c, ok));
            self.sets.get_mut(c).ok_or(StepError::Undefined("index class"))?.insert((ok, v));
        }
        Ok(())
    }

    /// The order key of a member (an index within this one takes it, §3.7.6).
    pub fn key_of(&self, v: u32) -> Result<i128, StepError> {
        self.ent.get(&v).map(|e| e.1).ok_or(StepError::Undefined("index within: not a member of the outer index"))
    }

    /// The first (order key, id) of one class.
    pub fn front(&self, c: usize) -> Option<(i128, u32)> {
        self.sets.get(c).and_then(|set| set.first().copied())
    }

    /// The members in index order (for tests).
    pub fn ordered(&self) -> Vec<u32> {
        let mut all: Vec<(i128, u32)> = self.ent.iter().map(|(v, (_, o))| (*o, *v)).collect();
        all.sort();
        all.into_iter().map(|(_, v)| v).collect()
    }
}

/// Why the acceptor refused an entry (§3.8.9).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Reject {
    Rejected,
    Ambiguous,
    Step(StepError),
}

/// A function read as a view.
pub trait Get<K, V> {
    fn get(&self, k: &K) -> V;
    /// The value at k read in place (a list's head without copying the list).
    fn get_ref(&self, k: &K) -> &V;
}

/// A function written in place.
pub trait GetSet<K, V>: Get<K, V> {
    fn set(&mut self, k: K, v: V);
    fn as_get(&self) -> &dyn Get<K, V>;
    /// The value at k, to change in place (a sorted list's insert, docs/spec.md §3.10.6).
    fn slot(&mut self, k: K) -> &mut V;
}

/// A stored function: a default and the entries that differ from it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Fun<K: Ord, V> {
    d: V,
    m: BTreeMap<K, V>,
}

impl<K: Ord, V: Clone + PartialEq> Fun<K, V> {
    pub fn new(d: V) -> Self {
        Fun { d, m: BTreeMap::new() }
    }
    pub fn from_parts(d: V, kv: Vec<(K, V)>) -> Self {
        let mut f = Fun::new(d);
        for (k, v) in kv {
            f.set(k, v);
        }
        f
    }
}

impl<K: Ord, V: Clone + PartialEq> Get<K, V> for Fun<K, V> {
    fn get(&self, k: &K) -> V {
        match self.m.get(k) {
            Some(v) => v.clone(),
            None => self.d.clone(),
        }
    }
    fn get_ref(&self, k: &K) -> &V {
        self.m.get(k).unwrap_or(&self.d)
    }
}

impl<K: Ord, V: Clone + PartialEq> GetSet<K, V> for Fun<K, V> {
    fn set(&mut self, k: K, v: V) {
        if v == self.d {
            self.m.remove(&k);
        } else {
            self.m.insert(k, v);
        }
    }
    fn as_get(&self) -> &dyn Get<K, V> {
        self
    }
    fn slot(&mut self, k: K) -> &mut V {
        let d = &self.d;
        self.m.entry(k).or_insert_with(|| d.clone())
    }
}

/// An overlay (docs/spec.md §3.4.8): the entries written over an unchanged view.
pub struct Ov<'a, K, V> {
    b: &'a dyn Get<K, V>,
    m: BTreeMap<K, V>,
}

impl<'a, K: Ord, V: Clone> Ov<'a, K, V> {
    pub fn over(b: &'a dyn Get<K, V>) -> Self {
        Ov { b, m: BTreeMap::new() }
    }
    pub fn writes(self) -> BTreeMap<K, V> {
        self.m
    }
}

impl<K: Ord, V: Clone> Get<K, V> for Ov<'_, K, V> {
    fn get(&self, k: &K) -> V {
        match self.m.get(k) {
            Some(v) => v.clone(),
            None => self.b.get(k),
        }
    }
    fn get_ref(&self, k: &K) -> &V {
        match self.m.get(k) {
            Some(v) => v,
            None => self.b.get_ref(k),
        }
    }
}

impl<K: Ord, V: Clone> GetSet<K, V> for Ov<'_, K, V> {
    fn set(&mut self, k: K, v: V) {
        self.m.insert(k, v);
    }
    fn as_get(&self) -> &dyn Get<K, V> {
        self
    }
    fn slot(&mut self, k: K) -> &mut V {
        let b = self.b;
        self.m.entry(k).or_insert_with_key(|k| b.get(k))
    }
}

mod rt {
    use super::StepError;
    use std::collections::BTreeSet;

    pub fn add(a: i128, b: i128) -> Result<i128, StepError> {
        a.checked_add(b).ok_or(StepError::Overflow("+"))
    }
    pub fn sub(a: i128, b: i128) -> Result<i128, StepError> {
        a.checked_sub(b).ok_or(StepError::Overflow("-"))
    }
    pub fn mul(a: i128, b: i128) -> Result<i128, StepError> {
        a.checked_mul(b).ok_or(StepError::Overflow("*"))
    }
    pub fn div(a: i128, b: i128) -> Result<i128, StepError> {
        a.checked_div_euclid(b).ok_or(StepError::Undefined("div"))
    }
    pub fn md(a: i128, b: i128) -> Result<i128, StepError> {
        a.checked_rem_euclid(b).ok_or(StepError::Undefined("mod"))
    }
    pub fn neg(a: i128) -> Result<i128, StepError> {
        a.checked_neg().ok_or(StepError::Overflow("-"))
    }
    /// A value written to a parameter fits its width (docs/spec.md §3.12.4).
    pub fn fit(v: i128, lo: i128, hi: i128, name: &'static str) -> Result<i128, StepError> {
        if v < lo || v > hi { Err(StepError::Overflow(name)) } else { Ok(v) }
    }
    pub fn int2nat(a: i128) -> Result<i128, StepError> {
        if a < 0 { Err(StepError::Undefined("Int2Nat")) } else { Ok(a) }
    }
    pub fn nat2pos(a: i128) -> Result<i128, StepError> {
        if a <= 0 { Err(StepError::Undefined("Nat2Pos")) } else { Ok(a) }
    }
    pub fn id(a: i128) -> Result<u32, StepError> {
        u32::try_from(a).map_err(|_| StepError::Overflow("id"))
    }
    pub fn count(n: usize) -> Result<i128, StepError> {
        i128::try_from(n).map_err(|_| StepError::Overflow("#"))
    }
    pub fn cons<X>(x: X, mut l: Vec<X>) -> Vec<X> {
        l.insert(0, x);
        l
    }
    pub fn snoc<X>(mut l: Vec<X>, x: X) -> Vec<X> {
        l.push(x);
        l
    }
    pub fn cat<X>(mut a: Vec<X>, b: Vec<X>) -> Vec<X> {
        a.extend(b);
        a
    }
    /// The head of a list read in place: one element cloned, not the list.
    pub fn head_ref<X: Clone>(l: &[X]) -> Result<X, StepError> {
        l.first().cloned().ok_or(StepError::Undefined("head"))
    }
    /// The head of a queue parameter (repr deque).
    pub fn dq_head<X: Clone>(l: &std::collections::VecDeque<X>) -> Result<X, StepError> {
        l.front().cloned().ok_or(StepError::Undefined("head"))
    }
    pub fn head<X>(l: Vec<X>) -> Result<X, StepError> {
        l.into_iter().next().ok_or(StepError::Undefined("head"))
    }
    pub fn tail<X>(mut l: Vec<X>) -> Result<Vec<X>, StepError> {
        if l.is_empty() {
            return Err(StepError::Undefined("tail"));
        }
        l.remove(0);
        Ok(l)
    }
    pub fn rhead<X>(mut l: Vec<X>) -> Result<X, StepError> {
        l.pop().ok_or(StepError::Undefined("rhead"))
    }
    pub fn rtail<X>(mut l: Vec<X>) -> Result<Vec<X>, StepError> {
        if l.pop().is_none() {
            return Err(StepError::Undefined("rtail"));
        }
        Ok(l)
    }
    pub fn at<X>(l: Vec<X>, i: i128) -> Result<X, StepError> {
        let i = usize::try_from(i).map_err(|_| StepError::Undefined("."))?;
        l.into_iter().nth(i).ok_or(StepError::Undefined("."))
    }
    pub fn nth<X: Clone>(l: &[X], i: usize) -> Result<X, StepError> {
        l.get(i).cloned().ok_or(StepError::Undefined("walk"))
    }
    pub fn rest<X: Clone>(l: &[X], i: usize) -> Vec<X> {
        l.get(i.saturating_add(1)..).map(<[X]>::to_vec).unwrap_or_default()
    }
    // The set operations update the larger operand in place: the cost is that of the
    // smaller one, never a copy of the set (docs/spec.md §3.13.1).
    pub fn union<X: Ord>(mut a: BTreeSet<X>, mut b: BTreeSet<X>) -> BTreeSet<X> {
        if a.len() < b.len() {
            std::mem::swap(&mut a, &mut b);
        }
        a.extend(b);
        a
    }
    pub fn diff<X: Ord>(mut a: BTreeSet<X>, b: BTreeSet<X>) -> BTreeSet<X> {
        if b.len() <= a.len() {
            for x in &b {
                a.remove(x);
            }
        } else {
            a.retain(|x| !b.contains(x));
        }
        a
    }
    pub fn inter<X: Ord>(mut a: BTreeSet<X>, mut b: BTreeSet<X>) -> BTreeSet<X> {
        if b.len() < a.len() {
            std::mem::swap(&mut a, &mut b);
        }
        a.retain(|x| b.contains(x));
        a
    }
    pub fn insert<X: Ord>(mut s: BTreeSet<X>, x: X) -> BTreeSet<X> {
        s.insert(x);
        s
    }
    /// least(W) (docs/spec.md §3.7.6).
    pub fn least<X: Ord + Clone>(s: &BTreeSet<X>) -> Result<X, StepError> {
        s.first().cloned().ok_or(StepError::Undefined("least of an empty set"))
    }
    /// An ordered insert into a list sorted by id (docs/spec.md §3.10.6): the
    /// model's walk, at the place a binary search finds.
    pub fn ins_sorted<X: Ord>(l: &mut Vec<X>, u: X) {
        if let Err(p) = l.binary_search(&u) {
            l.insert(p, u);
        }
    }
    /// An insert ordered by a key, into a list kept sorted by that key (docs/spec.md
    /// §3.10.7): the model's walk puts u before the first element whose key is greater,
    /// the place a binary search finds.
    pub fn ins_by_key<X, K: Ord>(l: &mut Vec<X>, u: X, key: impl Fn(&X) -> K) {
        debug_assert!(l.iter().zip(l.iter().skip(1)).all(|(x, y)| key(x) <= key(y)), "a key-sorted list out of order");
        let ku = key(&u);
        let p = l.partition_point(|x| key(x) <= ku);
        l.insert(p, u);
    }
    /// tail of a list entry, in place.
    pub fn pop_first<X>(l: &mut Vec<X>) -> Result<(), StepError> {
        if l.is_empty() {
            return Err(StepError::Undefined("tail"));
        }
        l.remove(0);
        Ok(())
    }
    /// The removal of u's first occurrence, in place.
    pub fn rem_first<X: PartialEq>(l: &mut Vec<X>, u: &X) {
        if let Some(p) = l.iter().position(|x| x == u) {
            l.remove(p);
        }
    }
    /// tail(l) == [], read in place.
    pub fn tail_empty<X>(l: &[X]) -> Result<bool, StepError> {
        if l.is_empty() {
            return Err(StepError::Undefined("tail"));
        }
        Ok(l.len() == 1)
    }
    /// l with u's first occurrence removed == [], read in place.
    pub fn rem_empty<X: PartialEq>(l: &[X], u: &X) -> bool {
        l.is_empty() || (l.len() == 1 && l.first() == Some(u))
    }
}

/// A malformed entry (docs/spec.md §3.14.2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodeError(pub &'static str);

mod codec {
    use super::DecodeError;

    pub fn nat(v: i128, out: &mut Vec<u8>) {
        // A Nat is never negative; 0 stands in for what cannot happen.
        let mut x = u128::try_from(v).unwrap_or(0);
        loop {
            let b = u8::try_from(x & 0x7F).unwrap_or(0);
            x >>= 7;
            if x == 0 {
                out.push(b);
                return;
            }
            out.push(b | 0x80);
        }
    }
    pub fn int(v: i128, out: &mut Vec<u8>) {
        // zigzag: 0, -1, 1, -2, ... as 0, 1, 2, 3, ...
        let z = if v >= 0 { u128::try_from(v).unwrap_or(0) << 1 } else { (v.unsigned_abs().saturating_sub(1) << 1) | 1 };
        let mut x = z;
        loop {
            let b = u8::try_from(x & 0x7F).unwrap_or(0);
            x >>= 7;
            if x == 0 {
                out.push(b);
                return;
            }
            out.push(b | 0x80);
        }
    }
    pub fn boolean(v: bool, out: &mut Vec<u8>) {
        out.push(u8::from(v));
    }
    pub fn len(n: usize, out: &mut Vec<u8>) {
        nat(i128::try_from(n).unwrap_or(0), out);
    }

    pub struct Rd<'a> {
        pub b: &'a [u8],
        pub p: usize,
    }

    impl Rd<'_> {
        pub fn byte(&mut self) -> Result<u8, DecodeError> {
            let v = *self.b.get(self.p).ok_or(DecodeError("end of entry"))?;
            self.p = self.p.saturating_add(1);
            Ok(v)
        }
        fn raw(&mut self) -> Result<u128, DecodeError> {
            let mut x: u128 = 0;
            let mut sh: u32 = 0;
            loop {
                let b = self.byte()?;
                if sh > 126 {
                    return Err(DecodeError("a number too long"));
                }
                x |= u128::from(b & 0x7F) << sh;
                sh = sh.saturating_add(7);
                if b & 0x80 == 0 {
                    return Ok(x);
                }
            }
        }
        pub fn nat(&mut self) -> Result<i128, DecodeError> {
            i128::try_from(self.raw()?).map_err(|_| DecodeError("a number out of range"))
        }
        pub fn int(&mut self) -> Result<i128, DecodeError> {
            let z = self.raw()?;
            let m = i128::try_from(z >> 1).map_err(|_| DecodeError("a number out of range"))?;
            Ok(if z & 1 == 0 { m } else { m.checked_neg().and_then(|x| x.checked_sub(1)).ok_or(DecodeError("a number out of range"))? })
        }
        pub fn boolean(&mut self) -> Result<bool, DecodeError> {
            match self.byte()? {
                0 => Ok(false),
                1 => Ok(true),
                _ => Err(DecodeError("a Bool")),
            }
        }
        pub fn len(&mut self) -> Result<usize, DecodeError> {
            usize::try_from(self.nat()?).map_err(|_| DecodeError("a length"))
        }
        pub fn done(&self) -> bool {
            self.p == self.b.len()
        }
    }
}

/// A value printed as mCRL2 prints it (for the tests that compare with mCRL2).
pub trait M2 {
    fn m2(&self, out: &mut String);
}

impl M2 for i128 {
    fn m2(&self, out: &mut String) {
        out.push_str(&self.to_string());
    }
}

impl M2 for bool {
    fn m2(&self, out: &mut String) {
        out.push_str(if *self { "true" } else { "false" });
    }
}

impl<X: M2> M2 for Vec<X> {
    fn m2(&self, out: &mut String) {
        out.push('[');
        for (k, x) in self.iter().enumerate() {
            if k > 0 {
                out.push_str(", ");
            }
            x.m2(out);
        }
        out.push(']');
    }
}

impl<X: M2> M2 for BTreeSet<X> {
    fn m2(&self, out: &mut String) {
        out.push('{');
        for (k, x) in self.iter().enumerate() {
            if k > 0 {
                out.push_str(", ");
            }
            x.m2(out);
        }
        out.push('}');
    }
}
'''


WORKSPACE_CARGO = """[package]
name = "{name}"
description = "Generated from the verified model (docs/spec.md §3.11)."
version.workspace = true
edition.workspace = true
rust-version.workspace = true
license.workspace = true
publish.workspace = true

[lib]
path = "src/lib.rs"

[lints]
workspace = true
"""

STANDALONE_CARGO = """[package]
name = "{name}"
version = "0.0.0"
edition = "2024"
publish = false

[lib]
path = "src/lib.rs"
"""


def cargo_toml(package: str, workspace: bool) -> str:
    """The crate's Cargo.toml after its header (§3.11.1, §3.6.9): inheriting from
    the enclosing workspace, or standalone."""
    return (WORKSPACE_CARGO if workspace else STANDALONE_CARGO).replace("{name}", package)



def ident(name: str) -> str:
    """A Rust identifier for a model name (§3.3.8: a reserved word gets a trailing underscore)."""
    n = re.sub(r"[^A-Za-z0-9_]", lambda m: "_q" if m.group(0) == "'" else "_", name)
    return n + "_" if n in _KEYWORDS else n


def type_name(sort_name: str) -> str:
    n = ident(sort_name)
    return n + "_" if n in _TAKEN else n


def _borrowed(code: str) -> str:
    """A parameter or variable read without its clone, where a reference serves."""
    m = re.fullmatch(r"((?:s\.)?[A-Za-z_][A-Za-z0-9_]*)\.clone\(\)", code)
    return m.group(1) if m else code


def camel(label: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in label[2:].split("_") if p)


class Emitter:
    def __init__(self, model):
        self.model = model
        self.lps = model.lps
        self.types = Types(model.lps, model.config.id_sorts)
        self.maps = {m.name: m for m in model.lps.maps}
        self.fun_maps = model.fun_maps
        self.walks = model.walks
        # queue parameters (repr deque): a VecDeque in S, read at its head, tailed and appended in place
        self.deques = {p.name for p in model.params if p.repr == "deque"}
        self.n = 0
        # occurrences of a variable whose value is moved, not cloned: {id(Var): code} (see takes)
        self.moves = {}
        # set and list arguments passed by reference: {map: positions} (see borrowable)
        self.borrows = {}
        # applications whose operands after the moved one are evaluated first: {id(App): moved position}
        self.hoists = {}
        # sorted per-id lists (docs/spec.md §3.10.6), set by emit
        self.sorted = Sorted([model.lps], {}, {}, ())

    def fresh(self, base: str) -> str:
        self.n += 1
        return "%s%d" % (base, self.n)

    # -------------------------------------------------------------------- types

    def rtype(self, s) -> str:
        out: list = []
        stack = [(s, False)]
        while stack:
            x, seen = stack.pop()
            x = self.types.resolve(x)
            if x[0] == "id":
                if x[1] == "Bool":
                    out.append("bool")
                elif x[1] in ("Nat", "Pos", "Int"):
                    out.append("i128")
                elif x[1] in self.lps.structs:
                    out.append(type_name(x[1]))
                else:
                    raise g10("sort %s" % x[1], "no Rust type")
                continue
            if x[0] == "->":
                if not seen:
                    stack.append((x, True))
                    stack.append((x[2], False))
                    stack.append((x[1][0], False))
                    continue
                v, k = out.pop(), out.pop()
                out.append("Fun<%s, %s>" % (k, v))
                continue
            if x[0] in ("List", "FSet"):
                if not seen:
                    stack.append((x, True))
                    stack.append((x[1], False))
                    continue
                out.append(("Vec<%s>" if x[0] == "List" else "BTreeSet<%s>") % out.pop())
                continue
            raise g10("sort %s" % str(x), "no Rust type")
        return out[0]

    def kv(self, s):
        s = self.types.resolve(s)
        return self.rtype(s[1][0]), self.rtype(s[2])

    def emit_types(self) -> list:
        lines = []
        t = self.types
        derive = "#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash"
        for name in sorted(self.lps.structs):
            st = self.lps.structs[name]
            tn = type_name(name)
            shape = t.shape[name]
            if shape == "id":
                lines += [derive + ", Default, Copy)]", "pub struct %s(pub u32);" % tn, "",
                          "impl M2 for %s {" % tn, "    fn m2(&self, out: &mut String) {",
                          '        out.push_str("%s(");' % st.constructors[0][0],
                          "        out.push_str(&self.0.to_string());", "        out.push(')');", "    }", "}", ""]
            elif shape == "enumeration":
                lines += [derive + ", Default, Copy)]", "pub enum %s {" % tn]
                for k, (c, _, _) in enumerate(st.constructors):
                    lines.append("    %s%s," % ("#[default]\n    " if k == 0 else "", ident(c)))
                lines += ["}", "", "impl M2 for %s {" % tn, "    fn m2(&self, out: &mut String) {",
                          "        out.push_str(match self {"]
                lines += ['            %s::%s => "%s",' % (tn, ident(c), c) for c, _, _ in st.constructors]
                lines += ["        });", "    }", "}", ""]
            elif shape == "record":
                c, fields, _ = st.constructors[0]
                lines += [derive + ", Default)]", "pub struct %s {" % tn]
                lines += ["    pub %s: %s," % (self.field(name, 0, j), self.rtype(fs)) for j, (_, fs) in enumerate(fields)]
                lines += ["}", "", "impl M2 for %s {" % tn, "    fn m2(&self, out: &mut String) {",
                          '        out.push_str("%s(");' % c]
                for j in range(len(fields)):
                    if j:
                        lines.append('        out.push_str(", ");')
                    lines.append("        self.%s.m2(out);" % self.field(name, 0, j))
                lines += ["        out.push(')');", "    }", "}", ""]
            else:
                lines += [derive + ")]", "pub enum %s {" % tn]
                for c, fields, _ in st.constructors:
                    lines.append("    %s%s," % (ident(c), "(%s)" % ", ".join(self.rtype(fs) for _, fs in fields) if fields else ""))
                c0, f0, _ = st.constructors[0]
                lines += ["}", "", "impl Default for %s {" % tn, "    fn default() -> Self {",
                          "        %s::%s%s" % (tn, ident(c0), "(%s)" % ", ".join("Default::default()" for _ in f0) if f0 else ""),
                          "    }", "}", "", "impl M2 for %s {" % tn, "    fn m2(&self, out: &mut String) {",
                          "        match self {"]
                for c, fields, _ in st.constructors:
                    if not fields:
                        lines.append('            %s::%s => out.push_str("%s"),' % (tn, ident(c), c))
                        continue
                    names = ["f%d" % j for j in range(len(fields))]
                    lines.append("            %s::%s(%s) => {" % (tn, ident(c), ", ".join(names)))
                    lines.append('                out.push_str("%s(");' % c)
                    for j, f in enumerate(names):
                        if j:
                            lines.append('                out.push_str(", ");')
                        lines.append("                %s.m2(out);" % f)
                    lines.append("                out.push(')');")
                    lines.append("            }")
                lines += ["        }", "    }", "}", ""]
        return lines

    def field(self, sort: str, cons: int, j: int) -> str:
        p = self.lps.structs[sort].constructors[cons][1][j][0]
        return ident(p) if p else "f%d" % j


    # ------------------------------------------------------------ the entry codec (§3.8.10)

    def enc_stmt(self, s, v: str, ind: str) -> list:
        """Statements encoding the value `v` (a reference) of sort s into `out`."""
        s = self.types.resolve(s)
        k = self.types.kind(s)
        if k == "List":
            x = self.fresh("x")
            return [ind + "codec::len((%s).len(), out);" % v, ind + "for %s in (%s).iter() {" % (x, v)] + \
                self.enc_stmt(s[1], x, ind + "    ") + [ind + "}"]
        if k == "bool":
            return [ind + "codec::boolean(*(%s), out);" % v]
        if k == "int":
            return [ind + "codec::%s(*(%s), out);" % ("int" if s[1] == "Int" else "nat", v)]
        if k == "id":
            return [ind + "codec::nat(i128::from((%s).0), out);" % v]
        if k in ("enumeration", "record", "union"):
            return [ind + "enc_%s(%s, out);" % (type_name(s[1]), v)]
        raise g10("sort %s" % (s,), "no entry encoding")

    def dec_expr(self, s) -> str:
        """An expression decoding a value of sort s from `r` (with `?`)."""
        s = self.types.resolve(s)
        k = self.types.kind(s)
        if k == "List":
            return "{ let n = r.len()?; let mut l = Vec::new(); for _ in 0..n { l.push(%s); } l }" % self.dec_expr(s[1])
        if k == "bool":
            return "r.boolean()?"
        if k == "int":
            return "r.%s()?" % ("int" if s[1] == "Int" else "nat")
        if k == "id":
            return "%s(u32::try_from(r.nat()?).map_err(|_| DecodeError(\"an id\"))?)" % type_name(s[1])
        if k in ("enumeration", "record", "union"):
            return "dec_%s(r)?" % type_name(s[1])
        raise g10("sort %s" % (s,), "no entry decoding")

    def emit_codec_types(self) -> list:
        lines = []
        for name in sorted(self.lps.structs):
            shape = self.types.shape[name]
            if shape == "id":
                continue
            tn = type_name(name)
            cons = self.lps.structs[name].constructors
            lines.append("fn enc_%s(v: &%s, out: &mut Vec<u8>) {" % (tn, tn))
            if shape == "enumeration":
                lines.append("    codec::nat(match v {")
                lines += ["        %s::%s => %d," % (tn, ident(c), k) for k, (c, _, _) in enumerate(cons)]
                lines.append("    }, out);")
            elif shape == "record":
                for j, (_, fs) in enumerate(cons[0][1]):
                    lines += self.enc_stmt(fs, "&v.%s" % self.field(name, 0, j), "    ")
            else:
                lines.append("    match v {")
                for k, (c, fields, _) in enumerate(cons):
                    names = ["f%d" % j for j in range(len(fields))]
                    lines.append("        %s::%s%s => {" % (tn, ident(c), "(%s)" % ", ".join(names) if fields else ""))
                    lines.append("            codec::nat(%d, out);" % k)
                    for (_, fs), nm in zip(fields, names):
                        lines += self.enc_stmt(fs, nm, "            ")
                    lines.append("        }")
                lines.append("    }")
            lines += ["}", "", "fn dec_%s(r: &mut codec::Rd<'_>) -> Result<%s, DecodeError> {" % (tn, tn)]
            if shape == "enumeration":
                lines.append("    match r.nat()? {")
                lines += ["        %d => Ok(%s::%s)," % (k, tn, ident(c)) for k, (c, _, _) in enumerate(cons)]
                lines.append('        _ => Err(DecodeError("a constructor index")),')
                lines.append("    }")
            elif shape == "record":
                lines.append("    Ok(%s { %s })" % (tn, ", ".join("%s: %s" % (self.field(name, 0, j), self.dec_expr(fs))
                                                            for j, (_, fs) in enumerate(cons[0][1]))))
            else:
                lines.append("    match r.nat()? {")
                for k, (c, fields, _) in enumerate(cons):
                    if fields:
                        lines.append("        %d => Ok(%s::%s(%s))," % (k, tn, ident(c), ", ".join(self.dec_expr(fs) for _, fs in fields)))
                    else:
                        lines.append("        %d => Ok(%s::%s)," % (k, tn, ident(c)))
                lines.append('        _ => Err(DecodeError("a constructor index")),')
                lines.append("    }")
            lines += ["}", ""]
        return lines

    # -------------------------------------------------------------- expressions

    def expr(self, e, scope: dict) -> str:
        """Rust for a value expression (owned); scope: name -> (code, kind) with
        kind "val" (a value, cloned at each use), "ref" (a function view, `&dyn Get`
        or `&Fun`), "mut" (a written function, `&mut dyn GetSet`)."""
        out: list = []
        stack = [(e, False)]
        while stack:
            x, seen = stack.pop()
            if isinstance(x, Num):
                out.append("%di128" % x.value)
                continue
            if isinstance(x, Var):
                if id(x) in self.moves:
                    out.append(self.moves[id(x)])
                    continue
                code, kind = self._var(x.name, scope, e)
                if kind not in ("val", "litval"):
                    raise g10("expression %s" % show(e)[:80], "function %s used as a value" % x.name)
                if x.name in self.deques and code == "s.%s" % ident(x.name):
                    out.append("Vec::from(%s.clone())" % code)
                else:
                    out.append("%s.clone()" % code)
                continue
            if isinstance(x, Op):
                out.append(self._const(x))
                continue
            if isinstance(x, Bind):
                out.append(self._bind(x, scope))
                continue
            if isinstance(x, Whr):
                inner = dict(scope)
                lets = []
                for v, d in x.defs:
                    nm = self.fresh("w_")
                    lets.append("let %s = %s;" % (nm, self.expr(d, scope)))
                    inner[v.name] = (nm, "val")
                out.append("{ %s %s }" % (" ".join(lets), self.expr(x.body, inner)))
                continue
            h = x.head
            hn = h.name if isinstance(h, Op) else None
            if is_fun(self.lps, self._sort_of(x)):
                raise g10("expression %s" % show(x)[:80], "a function where a value is expected")
            if hn == "head" and len(x.args) == 1 and not isinstance(x.args[0], Var) \
                    and not isinstance(x.args[0].head, Op) and not is_fun(self.lps, self._sort_of(x.args[0])):
                # the head of a read F(y): the list read in place, one element cloned
                f = self.view_ref(x.args[0].head, scope)
                out.append("rt::head_ref((%s).get_ref(&%s))?" % (f, self.expr(x.args[0].args[0], scope)))
                continue
            if hn in ("==", "!=") and len(x.args) == 2 and self._empty_read(x) is not None:
                # F(y), tail(F(y)) or r(F(y), u) == [] (§3.10.7): the entry read in place, not copied
                kind, rd, u = self._empty_read(x)
                f = "(%s).get_ref(&%s)" % (self.view_ref(rd.head, scope), self.expr(rd.args[0], scope))
                t = "rt::tail_empty(%s)?" % f if kind == "tail" else "%s.is_empty()" % f if kind == "self" \
                    else "rt::rem_empty(%s, &%s)" % (f, self.expr(u, scope))
                out.append(t if hn == "==" else "!%s" % t)
                continue
            if not isinstance(h, Op):  # a read F(x)
                f = self.view_ref(h, scope)
                out.append("(%s).get(&%s)" % (f, self.expr(x.args[0], scope)))
                continue
            if hn == "if" and len(x.args) == 3:
                if not seen:
                    stack.append((x, True))
                    for a in reversed(x.args):
                        stack.append((a, False))
                    continue
                c, a, b = out.pop(-3), out.pop(-2), out.pop()
                out.append("(if %s { %s } else { %s })" % (c, a, b))
                continue
            if hn is not None and hn.startswith("ix_"):
                out.append(self.ix_app(x, hn, scope))
                continue
            if hn == "head" and len(x.args) == 1 and isinstance(x.args[0], Var) and x.args[0].name in self.deques \
                    and self._var(x.args[0].name, scope, e)[0] == "s.%s" % ident(x.args[0].name):
                out.append("rt::dq_head(&s.%s)?" % ident(x.args[0].name))
                continue
            if hn in self.fun_maps or hn in self.walks or hn in self.model_maps():
                if id(x) in self.hoists:
                    out.append("{ %s }" % (self.call(x, scope) + "?"))
                else:
                    out.append(self.call(x, scope) + "?")
                continue
            if hn in self.types.proj and self.types.proj[hn][0][0].shape == "union":
                out.append("proj_%s(&%s)?" % (ident(hn), self.expr(x.args[0], scope)))
                continue
            if not seen:
                stack.append((x, True))
                for a in reversed(x.args):
                    stack.append((a, False))
                continue
            n = len(x.args)
            args = out[len(out) - n:]
            del out[len(out) - n:]
            if id(x) in self.hoists:
                lets, args = self.hoisted(x, args, self.owned(x)[0])
                out.append("{ %s%s }" % (lets, self._app(x, hn, args)))
                continue
            out.append(self._app(x, hn, args))
        return out[-1]

    def ix_app(self, x, hn: str, scope: dict) -> str:
        """A selection operation of the refined Core (docs/spec.md §3.7.5)."""
        if hn.startswith("ix_least_"):
            return "rt::least(&%s)?" % _borrowed(self.expr(x.args[0], scope))
        for op in ("first", "some"):
            if hn.startswith("ix_%s_" % op):
                return "ix%s_%s(s)?" % (op, hn[len("ix_%s_" % op):])
        raise g10("operator %s" % hn, "a generator map where only first, some and least may appear")

    def model_maps(self) -> set:
        return {m.name for m in self.model.user_maps} | {m.name for m in self.lps.maps if m.name.startswith(INIT_PREFIX)}

    def _sort_of(self, x):
        h = x.head
        s = h.sort if isinstance(h, (Op, Var)) else None
        if s is None:
            return ("id", "?")
        k = len(x.args)
        cur = s
        if cur[0] == "->" and len(cur[1]) == k:
            return cur[2]
        return ("id", "?")

    def _var(self, name, scope, e):
        if name not in scope:
            raise g10("expression %s" % show(e)[:80], "variable %s is not in scope" % name)
        return scope[name]

    def _const(self, x: Op) -> str:
        n = x.name
        if n in ("true", "false"):
            return n
        if n == "[]":
            return "Vec::new()"
        if n in ("{}", "@fset_empty"):
            return "BTreeSet::new()"
        if n in self.types.cons:
            c = self.types.cons[n]
            return "%s::%s" % (type_name(c.sort), ident(n))
        if n in self.maps:
            return "m_%s()?" % ident(n)
        raise g10("operator %s" % n, "no Rust for this constant")

    def _bind(self, x: Bind, scope: dict) -> str:
        if x.kind == "Lambda" and len(x.vars) == 1:
            return "Fun::new(%s)" % self.expr(x.body, scope)
        if x.kind in ("Exists", "Forall") and len(x.vars) == 1:
            v = x.vars[0].name
            mem, rest = x.body.args[0], x.body.args[1]
            col = "(%s).iter().cloned()" % _borrowed(self.expr(mem.args[1], scope))
            name = self.fresh("q_")
            inner = dict(scope)
            inner[v] = (name, "val")
            body = self.expr(rest, inner)
            if x.kind == "Exists":
                return "{ let mut r = false; for %s in %s { if %s { r = true; break; } } r }" % (name, col, body)
            return "{ let mut r = true; for %s in %s { if !(%s) { r = false; break; } } r }" % (name, col, body)
        raise g10("binder %s" % x.kind, "no Rust for it here")

    def _app(self, x, hn, a) -> str:
        t = self.types
        sorts = x.head.sort
        dom = sorts[1] if sorts[0] == "->" else ()
        k0 = t.kind(dom[0]) if dom else None
        if hn in t.cons:
            c = t.cons[hn]
            tn = type_name(c.sort)
            if c.shape == "id":
                return "%s(rt::id(%s)?)" % (tn, a[0])
            if c.shape == "record":
                return "(%s { %s })" % (tn, ", ".join("%s: %s" % (self.field(c.sort, 0, j), v) for j, v in enumerate(a)))
            return "%s::%s(%s)" % (tn, ident(hn), ", ".join(a))
        if hn in t.proj:
            c, j = t.proj[hn][0]
            if c.shape == "id":
                return "i128::from((%s).0)" % a[0]
            return "(%s).%s" % (a[0], self.field(c.sort, c.index, j))
        if hn in t.recog:
            c = t.recog[hn]
            return "matches!(%s, %s::%s { .. })" % (a[0], type_name(c.sort), ident(c.name))
        if hn in ("&&", "||") and len(a) == 2:
            return "(%s %s %s)" % (a[0], hn, a[1])
        if hn in ("==", "!=") and len(a) == 2:
            for x, y in ((a[0], a[1]), (a[1], a[0])):
                if y == "BTreeSet::new()" or y == "Vec::new()":
                    q = re.fullmatch(r"Vec::from\((s\.[A-Za-z_][A-Za-z0-9_]*)\.clone\(\)\)", x)
                    return "%s(%s).is_empty()" % ("" if hn == "==" else "!", q.group(1) if q else _borrowed(x))
            return "(%s %s %s)" % (a[0], hn, a[1])
        if hn in ("<", "<=", ">", ">=") and len(a) == 2:
            if k0 == "FSet":
                if hn == "<=":
                    return "(%s).is_subset(&%s)" % (_borrowed(a[0]), _borrowed(a[1]))
                if hn == ">=":
                    return "(%s).is_superset(&%s)" % (_borrowed(a[0]), _borrowed(a[1]))
                raise g10("operator %s" % hn, "strict set inclusion")
            return "(%s %s %s)" % (a[0], hn, a[1])
        if hn == "=>":
            return "(!%s || %s)" % (a[0], a[1])
        if hn == "!":
            return "(!%s)" % a[0]
        if hn == "+" and len(a) == 2:
            return "rt::union(%s, %s)" % (a[0], a[1]) if k0 == "FSet" else "rt::add(%s, %s)?" % (a[0], a[1])
        if hn == "-" and len(a) == 2:
            return "rt::diff(%s, %s)" % (a[0], a[1]) if k0 == "FSet" else "rt::sub(%s, %s)?" % (a[0], a[1])
        if hn == "-" and len(a) == 1:
            return "rt::neg(%s)?" % a[0]
        if hn == "*" and len(a) == 2:
            return "rt::inter(%s, %s)" % (a[0], a[1]) if k0 == "FSet" else "rt::mul(%s, %s)?" % (a[0], a[1])
        if hn == "div":
            return "rt::div(%s, %s)?" % (a[0], a[1])
        if hn == "mod":
            return "rt::md(%s, %s)?" % (a[0], a[1])
        if hn == "min":
            return "(%s).min(%s)" % (a[0], a[1])
        if hn == "max":
            return "(%s).max(%s)" % (a[0], a[1])
        if hn == "succ":
            return "rt::add(%s, 1)?" % a[0]
        if hn == "pred":
            return "rt::sub(%s, 1)?" % a[0]
        if hn == "abs":
            return "(%s).checked_abs().ok_or(StepError::Overflow(\"abs\"))?" % a[0]
        if hn in ("Pos2Nat", "Nat2Int", "Pos2Int", "@cNat", "@cInt"):
            return a[0]
        if hn == "@cNeg":
            return "rt::neg(%s)?" % a[0]
        if hn == "Int2Nat":
            return "rt::int2nat(%s)?" % a[0]
        if hn == "Nat2Pos":
            return "rt::nat2pos(%s)?" % a[0]
        if hn == "|>":
            return "rt::cons(%s, %s)" % (a[0], a[1])
        if hn == "<|":
            return "rt::snoc(%s, %s)" % (a[0], a[1])
        if hn == "++":
            return "rt::cat(%s, %s)" % (a[0], a[1])
        if hn == "head" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*\.clone\(\)", a[0]):
            return "rt::head_ref(&%s)?" % a[0][:-len(".clone()")]
        if hn in ("head", "tail", "rhead", "rtail"):
            return "rt::%s(%s)?" % (hn, a[0])
        if hn == "#":
            return "rt::count((%s).len())?" % _borrowed(a[0])
        if hn == ".":
            return "rt::at(%s, %s)?" % (a[0], a[1])
        if hn == "in":
            return "(%s).contains(&%s)" % (_borrowed(a[1]), a[0])
        if hn in ("@fset_cons", "@fset_insert"):
            return "rt::insert(%s, %s)" % (a[1], a[0])
        raise g10("operator %s" % hn, "no Rust for it")

    def owned(self, x):
        """For an application x: its argument positions in Rust evaluation order, and
        those passed by value and consumed (a set operation's operands, a user map's
        value arguments); None when x passes no argument by value."""
        h = x.head
        if not isinstance(h, Op):
            return None
        hn = h.name
        n = len(x.args)
        dom = h.sort[1] if h.sort[0] == "->" else ()
        if hn in ("+", "-", "*") and n == 2 and dom and self.types.kind(dom[0]) == "FSet":
            return [0, 1], ({0} if hn == "-" else {0, 1})
        if hn in ("@fset_cons", "@fset_insert") and n == 2:
            return [1, 0], {1}
        if hn == "|>" and n == 2:
            return [0, 1], {1}
        if hn in ("<|", "++") and n == 2:
            return [0, 1], ({0} if hn == "<|" else {0, 1})
        if hn in ("tail", "rtail") and n == 1:
            return [0], {0}
        if hn in self.fun_maps or hn in self.walks or hn in self.model_maps():
            return list(range(n)), {j for j in range(n) if j < len(dom) and not is_fun(self.lps, dom[j])
                                    and j not in self.borrows.get(hn, ())}
        return None

    def borrowable(self, maps: dict) -> dict:
        """{map: positions}: the set and list arguments a user map only reads, passed by
        reference instead of by value (docs/spec.md §3.8.4). A position qualifies
        when every occurrence of its variable is read in place (`in`, `#`, `== {}`,
        `least`, `head`, `<=`, a quantifier's range), passed at a qualifying position
        of a map (the greatest fixpoint), or, in a walk, passed on unchanged; the list
        a walk walks always qualifies. maps: name -> (Map, equations)."""
        cand = {}
        for name, (m, eqs) in maps.items():
            dom = m.sort[1] if m.sort[0] == "->" else ()
            w = self.walks.get(name)
            js = set()
            for j, srt in enumerate(dom):
                if self.types.kind(srt) not in ("FSet", "List"):
                    continue
                if w is None and j == 0 and len(eqs) > 1:
                    continue  # matched by constructor
                js.add(j)
            cand[name] = js
        changed = True
        while changed:
            changed = False
            for name, (m, eqs) in maps.items():
                w = self.walks.get(name)
                for j in sorted(cand[name]):
                    if w is not None:
                        if j == w.pos:
                            continue
                        uses = [(w.params[j], w.base), (w.params[j], w.body)]
                    else:
                        uses = [(e.lhs.args[j].name, e.rhs) for e in eqs
                                if isinstance(e.lhs, App) and j < len(e.lhs.args) and isinstance(e.lhs.args[j], Var)]
                    if not all(self._reads_only(e, v, name, j, cand) for v, e in uses):
                        cand[name].discard(j)
                        changed = True
        return {k: v for k, v in cand.items() if v}

    def _reads_only(self, e, v: str, me: str, j: int, cand: dict) -> bool:
        """Whether every occurrence of variable v in e is a read in place (see borrowable)."""
        stack = [(e, None, None)]
        while stack:
            x, parent, k = stack.pop()
            if isinstance(x, Var):
                if x.name != v:
                    continue
                if parent is None:
                    return False
                hn = parent.head.name if isinstance(parent.head, Op) else None
                if hn == "in" and k == 1 or hn in ("#", "head", "<=", ">=") or (hn or "").startswith("ix_least_"):
                    continue
                if hn in ("==", "!=") and isinstance(parent.args[1 - k], Op) \
                        and parent.args[1 - k].name in ("{}", "[]", "@fset_empty"):
                    continue
                if hn == me and k == j:
                    continue  # a walk passing it on unchanged
                if hn in cand and k in cand[hn] and hn != me:
                    continue
                return False
            if isinstance(x, App):
                for k2, a in enumerate(x.args):
                    stack.append((a, x, k2))
            elif isinstance(x, Bind):
                stack.append((x.body, None, None))
            elif isinstance(x, Whr):
                stack.append((x.body, None, None))
                for _, d in x.defs:
                    stack.append((d, None, None))
        return True

    def _reach(self, e, name: str) -> bool:
        """Whether some occurrence of `name` in e lies on a path of `if` branches and
        consumed operands (`owned`): a place its value could be moved from."""
        stack = [e]
        while stack:
            x = stack.pop()
            if isinstance(x, Var):
                if x.name == name:
                    return True
                continue
            if not isinstance(x, App) or not isinstance(x.head, Op):
                continue
            if x.head.name == "if" and len(x.args) == 3:
                stack += [x.args[1], x.args[2]]
                continue
            o = self.owned(x)
            if o is not None:
                stack += [x.args[j] for j in o[1] if _occurs(x.args[j], name)]
        return False

    def takes(self, e, name: str, code: str, hoist: bool = True) -> dict:
        """The occurrences of variable `name` in e whose value can be moved (`code`)
        instead of cloned, as {id(occurrence): code}: those on a path of `if` branches
        and consumed operands (`owned`) along which nothing reads `name` after the move.
        Reads elsewhere stay reads (a condition is evaluated before its branches, an
        operand before the consumed one); an operand after the consumed one that reads
        `name` is evaluated first (hoisted, `self.hoists`) when `hoist`. At most one move
        happens on any path."""
        marked = {}
        stack = [e]
        while stack:
            x = stack.pop()
            if isinstance(x, Var):
                if x.name == name:
                    marked[id(x)] = marked.get(id(x), 0) + 1
                continue
            if not isinstance(x, App) or not isinstance(x.head, Op):
                continue
            if x.head.name == "if" and len(x.args) == 3:
                stack += [x.args[1], x.args[2]]
                continue
            o = self.owned(x)
            if o is None:
                continue
            order, owned = o
            hits = [j for j in order if _occurs(x.args[j], name)]
            cand = [j for j in hits if j in owned and self._reach(x.args[j], name)]
            if not cand:
                continue
            f = cand[-1]
            if hits[-1] != f:
                if not hoist or id(x) in self.hoists:
                    continue
                self.hoists[id(x)] = f
            stack.append(x.args[f])
        # a subexpression shared by two paths is moved only if every path to it moves
        total = {}
        for x in walk(e):
            if isinstance(x, Var) and id(x) in marked:
                total[id(x)] = total.get(id(x), 0) + 1
        return {k: code for k, c in marked.items() if total[k] == c}

    def hoisted(self, x, args: list, order: list) -> tuple:
        """For an application whose consumed operand is moved (`takes`): the operands
        evaluated after it in `order`, bound first, as (lets, args)."""
        f = self.hoists.get(id(x))
        if f is None:
            return "", args
        args = list(args)
        lets = []
        for j in order[order.index(f) + 1:]:
            h = self.fresh("h")
            lets.append("let %s = %s; " % (h, args[j]))
            args[j] = h
        return "".join(lets), args

    # -------------------------------------------------------------- views

    def view(self, e, scope: dict):
        """(code, kind) of a function-sorted expression: kind "ref" (a reference to a
        view) or "ov" (an owned overlay)."""
        if isinstance(e, Var):
            code, kind = self._var(e.name, scope, e)
            if kind == "ref":
                return code, "ref"
            if kind == "mut":
                return "%s.as_get()" % code, "ref"
            raise g10("expression %s" % show(e)[:80], "%s is not a function" % e.name)
        h = e.head.name if isinstance(e, App) and isinstance(e.head, Op) else None
        if h == "@func_update":
            base = self.view_ov(e.args[0], scope)
            return "{ let mut o = %s; o.set(%s, %s); o }" % (
                base, self.expr(e.args[1], scope), self.expr(e.args[2], scope)), "ov"
        if h == "if":
            return "(if %s { %s } else { %s })" % (
                self.expr(e.args[0], scope), self.view_ov(e.args[1], scope), self.view_ov(e.args[2], scope)), "ov"
        if h in self.fun_maps and self.fun_maps[h][1] is not None:
            k = self.fun_maps[h][1]
            base = self.view_ov(e.args[k], scope)
            return "{ let mut o = %s; %s?; o }" % (base, self.call(e, scope, target="&mut o")), "ov"
        raise g10("function expression %s" % show(e)[:80], "no Rust for it")

    def view_ov(self, e, scope) -> str:
        code, kind = self.view(e, scope)
        return "Ov::over(%s)" % code if kind == "ref" else code

    def _empty_read(self, x):
        """For `F(y) == []`, `tail(F(y)) == []` or `r(F(y), u) == []` (r a first-occurrence
        removal, §3.10.7), F a function read of a list: (kind, the read F(y), u); else None."""
        for a, b in ((x.args[0], x.args[1]), (x.args[1], x.args[0])):
            if not (isinstance(b, Op) and b.name == "[]") or not isinstance(a, App):
                continue
            if not isinstance(a.head, Op):
                if len(a.args) == 1 and not is_fun(self.lps, self._sort_of(a)) \
                        and self.types.kind(self._sort_of(a)) == "List":
                    return ("self", a, None)
                continue
            rd = a.args[0] if a.args else None
            if not (isinstance(rd, App) and not isinstance(rd.head, Op) and len(rd.args) == 1) \
                    or is_fun(self.lps, self._sort_of(rd)):
                continue
            if a.head.name == "tail" and len(a.args) == 1:
                return ("tail", rd, None)
            if a.head.name in self.sorted.removes and len(a.args) == 2:
                return ("rem", rd, a.args[1])
        return None

    def view_ref(self, e, scope) -> str:
        code, kind = self.view(e, scope)
        return code if kind == "ref" else "&%s" % code

    def written(self, name: str) -> set:
        w = self.walks.get(name)
        out = set(w.overlays) if w else set()
        r = self.fun_maps.get(name, ((), None))[1]
        if r is not None:
            out.add(r)
        return out

    def call(self, x, scope: dict, target: str | None = None) -> str:
        """A call of a user map (without the `?`); `target` is passed at the result position."""
        name = x.head.name
        fpos, k = self.fun_maps.get(name, ((), None))
        args = []
        for j, a in enumerate(x.args):
            if j == k and target is not None:
                args.append(target)
            elif j in fpos:
                if j in self.written(name):
                    args.append("&mut %s" % self.view_ov(a, scope))
                else:
                    args.append(self.view_ref(a, scope))
            elif j in self.borrows.get(name, ()):
                code = self.expr(a, scope)
                b = _borrowed(code)
                args.append("&%s" % b if b != code else "&(%s)" % code)
            else:
                args.append(self.expr(a, scope))
        lets, args = self.hoisted(x, args, list(range(len(args))))
        return "%sm_%s(%s)" % (lets, ident(name), ", ".join(args))

    def apply_fe(self, fe, target: str, scope: dict, lines: list, ind: str):
        """Statements that apply a function expression to `target` (an overlay whose
        leaves read the unchanged parameters, so they can be evaluated in turn)."""
        stack = [("fe", fe, ind)]
        while stack:
            kind, x, i = stack.pop()
            if kind == "line":
                lines.append(i + x)
                continue
            if isinstance(x, Var):
                continue
            h = x.head.name if isinstance(x, App) and isinstance(x.head, Op) else None
            if h == "@func_update":
                stack.append(("line", "%s.set(%s, %s);" % (target, self.expr(x.args[1], scope), self.expr(x.args[2], scope)), i))
                stack.append(("fe", x.args[0], i))
            elif h == "if":
                stack.append(("line", "}", i))
                stack.append(("fe", x.args[2], i + "    "))
                stack.append(("line", "} else {", i))
                stack.append(("fe", x.args[1], i + "    "))
                stack.append(("line", "if %s {" % self.expr(x.args[0], scope), i))
            elif h in self.fun_maps and self.fun_maps[h][1] is not None:
                k = self.fun_maps[h][1]
                stack.append(("line", "%s?;" % self.call(x, scope, target="&mut %s" % target), i))
                stack.append(("fe", x.args[k], i))
            else:
                raise g10("function expression %s" % show(x)[:80], "no Rust for it")

    def collect_writes(self, fe, wvar: str, scope: dict, lines: list, ind: str, acc: str | None = None):
        """Statements that push onto `wvar` the writes of a function expression, every
        index, value and condition evaluated before any is applied (inside a walk,
        where the leaves read the accumulator itself). With `acc`, the walk's sorted
        parameter (docs/spec.md §3.10.6): its ordered inserts are pushed onto `wvar`i
        as (key, id), to apply in place."""
        stack = [("fe", fe, ind)]
        while stack:
            kind, x, i = stack.pop()
            if kind == "line":
                lines.append(i + x)
                continue
            if isinstance(x, Var):
                continue
            h = x.head.name if isinstance(x, App) and isinstance(x.head, Op) else None
            up = self.sorted.update_value(x.args[2], x.args[1], acc) \
                if acc is not None and h == "@func_update" and isinstance(x.args[0], Var) and x.args[0].name == acc else None
            if up is not None and up[0] == "ins":
                stack.append(("line", "%si.push((%s, %s));" % (wvar, self.expr(x.args[1], scope), self.expr(up[1], scope)), i))
            elif up is not None:
                stack.append(("line", "{ let k = %s; for x in %s { %si.push((k.clone(), x)); } }"
                              % (self.expr(x.args[1], scope), self.expr(up[1], scope), wvar), i))
            elif h == "@func_update":
                stack.append(("line", "%s.push((%s, %s));" % (wvar, self.expr(x.args[1], scope), self.expr(x.args[2], scope)), i))
                stack.append(("fe", x.args[0], i))
            elif h == "if":
                stack.append(("line", "}", i))
                stack.append(("fe", x.args[2], i + "    "))
                stack.append(("line", "} else {", i))
                stack.append(("fe", x.args[1], i + "    "))
                stack.append(("line", "if %s {" % self.expr(x.args[0], scope), i))
            else:
                raise g10("function expression %s" % show(x)[:80], "an in-place call inside a walk")

    # ------------------------------------------------------------ maps and walks

    def param_type(self, m, j: int) -> str:
        s = m.sort[1][j]
        if is_fun(self.lps, s):
            k, v = self.kv(s)
            return ("&mut dyn GetSet<%s, %s>" if j in self.written(m.name) else "&dyn Get<%s, %s>") % (k, v)
        if j in self.borrows.get(m.name, ()):
            return "&" + self.rtype(s)
        return self.rtype(s)

    def ret_type(self, m) -> str:
        r = m.sort[2] if m.sort[0] == "->" else m.sort
        return "()" if is_fun(self.lps, r) else self.rtype(r)

    def emit_map(self, m, eqs) -> list:
        self._cur = m.name
        if m.name in self.walks:
            return self.emit_walk(m, self.walks[m.name])
        name = m.name
        n = m.arity
        params = ", ".join("a%d: %s" % (j, self.param_type(m, j)) for j in range(n))
        lines = ["pub fn m_%s(%s) -> Result<%s, StepError> {" % (ident(name), params, self.ret_type(m))]
        ret = self.fun_maps.get(name, ((), None))[1]
        if n == 0:
            lines.append("    Ok(%s)" % self.expr(eqs[0].rhs, {}))
            return lines + ["}"]
        if len(eqs) == 1 and isinstance(eqs[0].lhs, App) and all(isinstance(p, Var) for p in eqs[0].lhs.args):
            scope = {}
            for j, p in enumerate(eqs[0].lhs.args):
                scope[p.name] = self._param_scope(m, j)
            lines += self._body(eqs[0].rhs, scope, ret, eqs[0].lhs.args, "    ")
            return lines + ["}"]
        # One equation per constructor of the first argument.
        lines.append("    match a0 {")
        first = self.types.resolve(m.sort[1][0])[1]
        for e in eqs:
            p = e.lhs.args[0]
            scope = {}
            for j, q in enumerate(e.lhs.args[1:], start=1):
                scope[q.name] = self._param_scope(m, j)
            tn = type_name(first)
            if isinstance(p, Op):
                pat = "%s::%s" % (tn, ident(p.name))
            else:
                names = [self.fresh("f") for _ in p.args]
                pat = "%s::%s(%s)" % (tn, ident(p.head.name), ", ".join(names))
                for v, nm in zip(p.args, names):
                    scope[v.name] = (nm, "val")
            lines.append("        %s => {" % pat)
            lines += self._body(e.rhs, scope, ret, e.lhs.args, "            ")
            lines.append("        }")
        lines += ["    }", "}"]
        return lines

    def _param_scope(self, m, j: int):
        if is_fun(self.lps, m.sort[1][j]):
            return ("a%d" % j, "mut" if j in self.written(m.name) else "ref")
        return ("a%d" % j, "val")

    def _body(self, rhs, scope, ret, lhs_args, ind) -> list:
        if ret is None:
            # a set or list argument is the callee's own: moved into the result, not cloned
            for j, p in enumerate(lhs_args):
                if isinstance(p, Var) and scope.get(p.name) == ("a%d" % j, "val") \
                        and self.types.kind(p.sort) in ("FSet", "List") and j not in self.borrows.get(self._cur, ()):
                    self.moves.update(self.takes(rhs, p.name, "a%d" % j))
            code = self.expr(rhs, scope)
            self.moves, self.hoists = {}, {}
            return [ind + "Ok(%s)" % code]
        target = "a%d" % ret
        lines = [ind + "let mut w: Vec<_> = Vec::new();"]
        self.collect_writes(rhs, "w", scope, lines, ind)
        lines += [ind + "for (k, v) in w {", ind + "    %s.set(k, v);" % target, ind + "}", ind + "Ok(())"]
        return lines

    def emit_walk(self, m, w) -> list:
        n = len(w.params)
        params = ", ".join("a%d: %s" % (j, self.param_type(m, j)) for j in range(n))
        lines = ["pub fn m_%s(%s) -> Result<%s, StepError> {" % (ident(m.name), params, self.ret_type(m))]
        scope = {}
        for j, p in enumerate(w.params):
            if j == w.pos:
                lines.append("    let v_l = a%d;" % j)
                continue
            v = "v_" + ident(p)
            if is_fun(self.lps, m.sort[1][j]):
                lines.append("    let %s = a%d;" % (v, j))
                scope[p] = (v, "mut" if j in self.written(m.name) else "ref")
            elif j in self.borrows.get(m.name, ()):
                lines.append("    let %s = a%d;" % (v, j))
                scope[p] = (v, "val")
            else:
                lines.append("    let mut %s = a%d;" % (v, j))
                scope[p] = (v, "val")
        lines += ["    let mut i: usize = 0;", "    let n = v_l.len();"]
        if w.op == "+":
            lines.append("    let mut acc: i128 = 0;")
        elif w.op == "|>":
            lines.append("    let mut pre = Vec::new();")
        lines.append("    let r: %s = loop {" % self.ret_type(m))
        base_scope = dict(scope)
        base_scope[w.tail] = ("Vec::new()", "lit")
        lines.append("        if i == n {")
        lines += self._walk_exit(w, w.base, self._lit(base_scope), "            ", m)
        lines.append("        }")
        lines.append("        let x = rt::nth(&v_l, i)?;")
        body_scope = dict(scope)
        body_scope[w.head] = ("x", "val")
        body_scope[w.tail] = ("rt::rest(&v_l, i)", "lit")
        body_scope = self._lit(body_scope)
        stack = [("R", w.body, "        ", body_scope)]
        while stack:
            kind, x, ind, sc = stack.pop()
            if kind == "line":
                lines.append(ind + x)
                continue
            if not mentions(x, m.name):
                lines += self._walk_exit(w, x, sc, ind, m)
                continue
            if isinstance(x, Whr):
                sc2 = dict(sc)
                for v, d in x.defs:
                    nm = self.fresh("w_")
                    lines.append(ind + "let %s = %s;" % (nm, self.expr(d, sc)))
                    sc2[v.name] = (nm, "val")
                stack.append(("R", x.body, ind, sc2))
                continue
            h = x.head.name
            if h == m.name:
                lines += self._walk_continue(w, m, x, sc, ind)
                continue
            if h == "if":
                stack.append(("line", "}", ind, None))
                stack.append(("R", x.args[2], ind + "    ", sc))
                stack.append(("line", "} else {", ind, None))
                stack.append(("R", x.args[1], ind + "    ", sc))
                lines.append(ind + "if %s {" % self.expr(x.args[0], sc))
                continue
            left = not mentions(x.args[0], m.name)
            e, r = (x.args[0], x.args[1]) if left else (x.args[1], x.args[0])
            ev = self.expr(e, sc)
            if h == "+":
                lines.append(ind + "acc = rt::add(acc, %s)?;" % ev)
            elif h == "||":
                lines.append(ind + "if %s {" % ev)
                lines.append(ind + "    break true;")
                lines.append(ind + "}")
            elif h == "&&":
                lines.append(ind + "if !(%s) {" % ev)
                lines.append(ind + "    break false;")
                lines.append(ind + "}")
            elif h == "|>":
                lines.append(ind + "pre.push(%s);" % ev)
            stack.append(("R", r, ind, sc))
        lines.append("    };")
        if w.op == "+":
            lines.append("    rt::add(acc, r)")
        elif w.op == "|>":
            lines.append("    pre.extend(r);")
            lines.append("    Ok(pre)")
        else:
            lines.append("    Ok(r)")
        return lines + ["}"]

    @staticmethod
    def _lit(scope: dict) -> dict:
        """A scope entry of kind "lit" is a value expression used as is (not a variable)."""
        out = {}
        for k, (code, kind) in scope.items():
            out[k] = ("(%s)" % code, "litval") if kind == "lit" else (code, kind)
        return out

    def _walk_moves(self, m, w, x, params) -> dict:
        """Moves of the walk's set and list parameters (`params`) out of x (see takes)."""
        out = {}
        for j, p in enumerate(w.params):
            if p in params and j != w.pos and self.types.kind(m.sort[1][j]) in ("FSet", "List") \
                    and j not in self.borrows.get(m.name, ()):
                out.update(self.takes(x, p, "std::mem::take(&mut v_%s)" % ident(p)))
        return out

    def _walk_exit(self, w, x, scope, ind, m=None) -> list:
        if w.ret_pos is None:
            # the loop ends here: every parameter is moved out, not cloned
            self.moves = self._walk_moves(m, w, x, set(w.params)) if m is not None else {}
            code = self.expr(x, self._vals(scope))
            self.moves, self.hoists = {}, {}
            return [ind + "break %s;" % code]
        target = "v_" + ident(w.params[w.ret_pos])
        acc = w.params[w.ret_pos] if m is not None and self.sorted.walks.get(m.name) == w.ret_pos else None
        lines = [ind + "let mut wr: Vec<_> = Vec::new();"]
        if acc is not None:
            lines.append(ind + "let mut wri: Vec<_> = Vec::new();")
        self.collect_writes(x, "wr", self._vals(scope), lines, ind, acc)
        lines += [ind + "for (k, v) in wr {", ind + "    %s.set(k, v);" % target, ind + "}"]
        if acc is not None:
            lines += [ind + "for (k, u) in wri {", ind + "    rt::ins_sorted(%s.slot(k), u);" % target, ind + "}"]
        return lines + [ind + "break ();"]

    @staticmethod
    def _vals(scope: dict) -> dict:
        # A "litval" entry is an expression: `x.clone()` of it is still right.
        return {k: (c, "val" if kind == "litval" else kind) for k, (c, kind) in scope.items()}

    def _walk_continue(self, w, m, x, scope, ind) -> list:
        scope = self._vals(scope)
        lines, temps, writes = [], [], []
        for j, (p, a) in enumerate(zip(w.params, x.args)):
            if j == w.pos or (isinstance(a, Var) and a.name == p):
                continue
            # the new value of p takes p's old value when no later argument reads it
            later = [b for q, b in enumerate(x.args) if q > j and q != w.pos
                     and not (isinstance(b, Var) and b.name == w.params[q])]
            if not any(_occurs(b, p) for b in later):
                self.moves = self._walk_moves(m, w, a, {p})
            if is_fun(self.lps, m.sort[1][j]):
                wv = self.fresh("wc")
                acc = p if self.sorted.walks.get(m.name) == j else None
                lines.append(ind + "let mut %s: Vec<_> = Vec::new();" % wv)
                if acc is not None:
                    lines.append(ind + "let mut %si: Vec<_> = Vec::new();" % wv)
                self.collect_writes(a, wv, scope, lines, ind, acc)
                writes.append(("v_" + ident(p), wv, acc is not None))
            else:
                t = self.fresh("t")
                lines.append(ind + "let %s = %s;" % (t, self.expr(a, scope)))
                temps.append(("v_" + ident(p), t))
            self.moves, self.hoists = {}, {}
        for v, wv, ins in writes:
            lines += [ind + "for (k, v) in %s {" % wv, ind + "    %s.set(k, v);" % v, ind + "}"]
            if ins:
                lines += [ind + "for (k, u) in %si {" % wv, ind + "    rt::ins_sorted(%s.slot(k), u);" % v, ind + "}"]
        for v, t in temps:
            lines.append(ind + "%s = %s;" % (v, t))
        lines += [ind + "i = i.saturating_add(1);", ind + "continue;"]
        return lines


_WHOLE = (("copy", r"\bs\.(\w+)\.clone\(\)"), ("copy", r"Vec::from\(s\.(\w+)"),
          ("scan", r"\bs\.(\w+)\.symmetric_difference"), ("scan", r"for \w+ in &?s\.(\w+)\b"),
          ("scan", r"\bs\.(\w+)\.iter\(\)"))


def whole_operations(text: str, frontier: set) -> dict:
    """{summand: sorted ["<copy | scan> <parameter>"]}: each place the code of a summand
    (its match, fire and output functions, after their `// <summand>` comment) copies or
    scans a whole frontier set or list, a cost that grows with the size of the DAG
    (docs/spec.md §3.13.1)."""
    out: dict = {}
    cur = None
    for line in text.split("\n"):
        m = re.match(r"// (\S+)$", line)
        if m:
            cur = m.group(1)
            continue
        if line.startswith(("fn ", "pub fn ")) and not re.match(r"(pub )?fn (match|fire|out)", line):
            cur = None
        if cur is None:
            continue
        for kind, pat in _WHOLE:
            for x in re.findall(pat, line):
                if x in frontier:
                    out.setdefault(cur, set()).add("%s %s" % (kind, x))
    return {k: sorted(v) for k, v in sorted(out.items())}


def map_borrows(model) -> dict:
    """{map: positions} of the set and list arguments the generated user maps of model
    take by reference (Emitter.borrowable), for a caller that writes its own calls."""
    em = Emitter(model)
    eqs: dict = {}
    for e in model.lps.equations:
        h = e.lhs.head if isinstance(e.lhs, App) else e.lhs
        eqs.setdefault(h.name, []).append(e)
    maps = list(model.user_maps) + [m for m in model.lps.maps if m.name.startswith(INIT_PREFIX)]
    return em.borrowable({m.name: (m, eqs.get(m.name, [])) for m in maps})


def emit(model, st=None, package: str = "generated", workspace: bool = False) -> dict:
    """The files of <generated>/rust/<rust_crate>: {relative path: text}; with st (a
    `stepper.Stepper`), the stepper of the refined Core too (docs/spec.md §3.8)."""
    em = Emitter(st.model if st is not None else model)
    em.sorted = Sorted([model.lps] + ([st.model.lps] if st is not None else []), em.walks, em.fun_maps,
                       model.config.id_sorts)
    lps = model.lps
    out = [HEADER.rstrip("\n"), "//! The model's generated code (docs/spec.md §3.11).", RUNTIME]
    out += em.emit_types()
    # Projections out of unions: undefined on another constructor.
    for p, cands in sorted(em.types.proj.items()):
        c, j = cands[0]
        if c.shape != "union":
            continue
        tn = type_name(c.sort)
        ft = em.rtype(c.fields[j][1])
        out += ["fn proj_%s(v: &%s) -> Result<%s, StepError> {" % (ident(p), tn, ft), "    match v {"]
        for cc, jj in cands:
            names = ["f%d" % k for k in range(len(cc.fields))]
            out.append("        %s::%s(%s) => Ok(%s.clone())," % (tn, ident(cc.name), ", ".join(names), names[jj]))
        out += ['        _ => Err(StepError::Undefined("%s")),' % p, "    }", "}", ""]
    eqs: dict = {}
    for e in lps.equations:
        h = e.lhs.head if isinstance(e.lhs, App) else e.lhs
        eqs.setdefault(h.name, []).append(e)
    umaps = (st.model if st is not None else model).user_maps
    for e in (st.model.lps.equations if st is not None else []):
        h = e.lhs.head if isinstance(e.lhs, App) else e.lhs
        if h.name.startswith("p_"):
            eqs.setdefault(h.name, []).append(e)
    # the user's maps by name, then the generator's init maps by name (§3.6.2)
    emitted = sorted(umaps, key=lambda m: m.name) + sorted((m for m in lps.maps if m.name.startswith(INIT_PREFIX)),
                                                           key=lambda m: m.name)
    em.borrows = em.borrowable({m.name: (m, eqs.get(m.name, [])) for m in emitted})
    for m in emitted:
        out += em.emit_map(m, eqs.get(m.name, []))
        out.append("")

    roles = {p.name: p.role for p in model.params}
    names = [n for n, _ in lps.params]
    sorts = dict(lps.params)
    config = [n for n in names if roles.get(n) == "config"]
    out += ["/// The configuration parameters of Core (§3.6.2).", "#[derive(Debug, Clone)]", "pub struct Config {"]
    out += ["    pub %s: %s," % (ident(n), em.rtype(sorts[n])) for n in config]
    out += ["}", "", "/// A state of Core: one field per parameter.", "#[derive(Debug, Clone)]", "pub struct S {"]
    out += ["    pub %s: %s," % (ident(n), em.rtype(sorts[n]) if n not in em.deques else
                                 "VecDeque<%s>" % em.rtype(sorts[n])[len("Vec<"):-1]) for n in names]
    xfields = _stepper_fields(st) if st is not None else []
    out += ["    pub %s: %s," % f for f in xfields]
    fps0 = {n for n, s in lps.params if is_fun(lps, s)}
    out += ["}", "", "fn initial(config: &Config) -> Result<S, StepError> {", "    Ok(S {"]
    for (n, srt), v in zip(lps.params, lps.init):
        if roles.get(n) == "config":
            val = "config.%s.clone()" % ident(n)
        elif n in model.inits:
            val = "m_gen_init_%s(%s)?" % (ident(n), ", ".join("config.%s.clone()" % ident(c) for c in config))
        else:
            val = em.expr(v, {})
        r = width_range(model, n)
        if r is not None and n not in fps0:
            val = 'rt::fit(%s, %d, %d, "%s")?' % (val, r[0], r[1], n)
        if n in em.deques:
            val = "VecDeque::from(%s)" % val
        out.append("        %s: %s," % (ident(n), val))
    out += ["        %s: Default::default()," % f for f, _ in xfields]
    out += ["    })", "}", ""]

    # Labels: one variant per label and kind (§3.11.2).
    inputs = sorted((a for a in model.actions if a.kind == "input"), key=lambda a: a.label)
    outputs = sorted((a for a in model.actions if a.kind == "output"), key=lambda a: a.label)
    for enum, labels in (("Event", inputs), ("Action", outputs)):
        out += ["#[derive(Debug, Clone, PartialEq, Eq)]", "pub enum %s {" % enum]
        for a in labels:
            out.append("    %s%s," % (camel(a.label), "(%s)" % ", ".join(em.rtype(s) for s in a.sorts) if a.sorts else ""))
        out += ["}", ""]
    out += ["#[derive(Debug, Clone, PartialEq, Eq)]", "pub enum Entry {", "    Event(Event),", "    Action(Action),", "}", ""]

    fps = {n for n, s in lps.params if is_fun(lps, s)}
    arms: dict = {}
    for k, s in enumerate(lps.summands):
        kind = model.summand_kind.get(s.name)
        labels = inputs if kind == "input" else outputs
        a = next(a for a in labels if a.label == s.label and len(a.sorts) == len(s.args))
        arms.setdefault(("Event" if kind == "input" else "Action", a.label, len(a.sorts)), []).append(k)
        out += _emit_summand(em, model, s, k, fps)
    out += ["/// Checks the action record against the unrefined Core (docs/spec.md §3.8.9).",
            "#[derive(Debug)]", "pub struct Acceptor {", "    s: S,", "}", "", "impl Acceptor {",
            "    pub fn new(config: Config) -> Result<Acceptor, StepError> {",
            "        Ok(Acceptor { s: initial(&config)? })", "    }", "",
            "    /// The state, for tests.", "    pub fn state(&self) -> &S {", "        &self.s", "    }", "",
            "    /// The state, for tests that start from another initial state.", "    #[doc(hidden)]",
            "    pub fn state_mut(&mut self) -> &mut S {", "        &mut self.s", "    }", "",
            "    pub fn accept(&mut self, entry: &Entry) -> Result<(), Reject> {", "        match entry {"]
    for (enum, label, n), ks in sorted(arms.items()):
        a = ", ".join("a%d" % j for j in range(n))
        out.append("            Entry::%s(%s::%s%s) => {" % (enum, enum, camel(label), "(%s)" % a if n else ""))
        for k in ks:
            out.append("                let e%d = match%d(&self.s%s).map_err(Reject::Step)?;" % (k, k, "".join(", a%d" % j for j in range(n))))
        out.append("                let found = [%s].iter().filter(|x| **x).count();" % ", ".join("e%d.is_some()" % k for k in ks))
        out.append("                if found > 1 {")
        out.append("                    return Err(Reject::Ambiguous);")
        out.append("                }")
        for k in ks:
            out.append("                if let Some(env) = e%d {" % k)
            out.append("                    return fire%d(&mut self.s, env).map_err(Reject::Step);" % k)
            out.append("                }")
        out.append("                Err(Reject::Rejected)")
        out.append("            }")
    out += ["        }", "    }", "}", ""]
    out += em.emit_codec_types()
    out += ["/// The bytes of an entry (docs/spec.md §3.8.10).", "pub fn encode(entry: &Entry, out: &mut Vec<u8>) {",
            "    match entry {"]
    dec = ["/// The entry of some bytes (§3.8.10).", "pub fn decode(bytes: &[u8]) -> Result<Entry, DecodeError> {",
           "    let mut r = codec::Rd { b: bytes, p: 0 };", "    let r = &mut r;", "    let side = r.byte()?;",
           "    let code = r.nat()?;", "    let e = match (side, code) {"]
    for side, enum, labels in ((0, "Event", inputs), (1, "Action", outputs)):
        for code, a in enumerate(labels):
            names = ["a%d" % j for j in range(len(a.sorts))]
            pat = "Entry::%s(%s::%s%s)" % (enum, enum, camel(a.label), "(%s)" % ", ".join(names) if names else "")
            out.append("        %s => {" % pat)
            out.append("            out.push(%d);" % side)
            out.append("            codec::nat(%d, out);" % code)
            for s, nm in zip(a.sorts, names):
                out += em.enc_stmt(s, nm, "            ")
            out.append("        }")
            dec.append("        (%d, %d) => Entry::%s(%s::%s%s)," % (side, code, enum, enum, camel(a.label),
                       "(%s)" % ", ".join(em.dec_expr(s) for s in a.sorts) if a.sorts else ""))
    out += ["    }", "}", ""]
    dec += ['        _ => return Err(DecodeError("a label")),', "    };", "    if !r.done() {",
            '        return Err(DecodeError("trailing bytes"));', "    }", "    Ok(e)", "}", ""]
    out += dec
    if st is not None:
        out += _emit_stepper(em, model, st, inputs, outputs)
    lib = "\n".join(out) + "\n"
    cargo = _header_block("#") + cargo_toml(package, workspace)
    return {"Cargo.toml": cargo, "src/lib.rs": lib}


def _emit_summand(em: Emitter, model, s, k, fps: set, watched: dict | None = None) -> list:
    lps = model.lps
    plan = plan_summand(em.types, s, fps, em.fun_maps)
    sorts = dict(lps.params)
    pscope = {}
    for n, srt in lps.params:
        pscope[n] = ("(&s.%s)" % ident(n), "ref") if n in fps else ("s.%s" % ident(n), "val")
    args = "".join(", a%d: &%s" % (j, em.rtype(t)) for j, t in enumerate(s.sorts))
    svt = [em.rtype(t) for _, t in s.sumvars]
    env_t = "(%s)" % "".join("%s, " % t for t in svt)
    lines = ["// %s" % s.name, "fn match%s(s: &S%s) -> Result<Option<%s>, StepError> {" % (k, args, env_t)]
    scope = dict(pscope)
    sv = {n: "v_" + ident(n) for n, _ in s.sumvars}
    for b in plan.binds:
        acc = "a%d" % b.arg
        for c, j in b.steps:
            if c.shape == "id":
                acc = "i128::from((%s).0)" % acc
            elif c.shape == "record":
                acc = "(%s).%s" % (acc, em.field(c.sort, 0, j))
            else:
                names = ["f%d" % q for q in range(len(c.fields))]
                tmp = em.fresh("p")
                lines.append("    let %s = match &%s { %s::%s(%s) => %s, _ => return Ok(None) };"
                             % (tmp, acc, type_name(c.sort), ident(c.name), ", ".join(names), names[j]))
                acc = "(*%s)" % tmp
        lines.append("    let %s = %s.clone();" % (sv[b.var], acc))
        scope[b.var] = (sv[b.var], "val")
    for var, e in plan.fixed:
        lines.append("    let %s = %s;" % (sv[var], em.expr(e, scope)))
        scope[var] = (sv[var], "val")
    for j, a in enumerate(s.args):
        if isinstance(a, Var) and any(b.var == a.name and b.arg == j and not b.steps for b in plan.binds):
            continue
        lines.append("    if *a%d != %s {" % (j, em.expr(a, scope)))
        lines.append("        return Ok(None);")
        lines.append("    }")
    lines.append("    if !(%s) {" % em.expr(s.cond, scope))
    lines.append("        return Ok(None);")
    lines.append("    }")
    lines.append("    Ok(Some((%s)))" % "".join("%s, " % sv[n] for n, _ in s.sumvars))
    lines += ["}", ""]
    # The firing: overlays and new values first, then the writes (§3.8.4).
    lines.append("fn fire%s(s: &mut S, env: %s) -> Result<(), StepError> {" % (k, env_t))
    lines.append("    let (%s) = env;" % "".join("%s, " % sv[n] for n, _ in s.sumvars))
    scope = dict(pscope)
    scope.update({n: (sv[n], "val") for n, _ in s.sumvars})
    lines += _fire_body(em, model, s, scope, fps, watched)
    lines += ["    Ok(())", "}", ""]
    return lines


def _fire_body(em: Emitter, model, s, scope: dict, fps: set, watched: dict | None) -> list:
    """The next state: overlays and new values first, then the writes (§3.8.4); a
    write to what an index reads dirties its id (docs/spec.md §3.8.3)."""
    lines = []
    commits, writes, deferred, inplace, listops = [], [], [], [], []
    for n, e in s.assigns:
        if n in fps and _inplace(em, model, s, n, e, fps, watched):
            inplace.append((n, e))
        elif n in fps and _list_op(em, model, s, n, e, watched) is not None:
            kind, k, u, f = _list_op(em, model, s, n, e, watched)
            t = ident(n)
            lines.append("    let lk_%s = %s;" % (t, em.expr(k, scope)))
            if u is not None:
                lines.append("    let lu_%s = %s;" % (t, em.expr(u, scope)))
            listops.append((n, kind, None if f is None else em.view_ref(f, scope)))
        elif n in fps:
            o = "o_" + ident(n)
            lines.append("    let mut %s = Ov::over(&s.%s);" % (o, ident(n)))
            em.apply_fe(e, o, scope, lines, "    ")
            lines.append("    let w_%s = %s.writes();" % (ident(n), o))
            commits.append(n)
        elif _set_inplace(em, model, n, e) is not None:
            ops = _set_inplace(em, model, n, e)
            plan = []
            for k, (kind, r, c) in enumerate(ops):
                rv = "r_%s_%d" % (ident(n), k)
                deferred.append((rv, r, None))
                cv = None
                if c is not None:
                    cv = "c_%s_%d" % (ident(n), k)
                    lines.append("    let %s = %s;" % (cv, em.expr(c[1], scope) if c[0] else "!(%s)" % em.expr(c[1], scope)))
                plan.append((kind, rv, cv))
            writes.append((n, ("set", plan)))
        elif n in em.deques:
            t = "n_" + ident(n)
            hn = e.head.name if hasattr(e, "head") and isinstance(e.head, Op) else None
            if hn == "tail" and isinstance(e.args[0], Var) and e.args[0].name == n:
                writes.append((n, ("pop", None)))
            elif hn == "++" and isinstance(e.args[0], Var) and e.args[0].name == n:
                lines.append("    let %s = %s;" % (t, em.expr(e.args[1], scope)))
                writes.append((n, ("extend", t)))
            else:
                lines.append("    let %s = VecDeque::from(%s);" % (t, em.expr(e, scope)))
                writes.append((n, t))
        elif _movable(em, model, n) and not (watched and n in watched):
            deferred.append(("n_" + ident(n), e, n))
            writes.append((n, "n_" + ident(n)))
        else:
            t = "n_" + ident(n)
            lines.append("    let %s = %s;" % (t, em.expr(e, scope)))
            writes.append((n, t))
    # A sorted per-id list is changed in place (docs/spec.md §3.10.6), after every other
    # value that reads the old state has been computed and before any is moved out of it.
    for n, e in inplace:
        _apply_inplace(em, n, e, scope, lines)
    # A set or list given a new value here is moved, not copied, into the one expression
    # that consumes it (its own new value, or another's: `X := X + Y, Y := {}`), once
    # every other expression that reads it has been evaluated: the step then costs what
    # changes, not the size of the set (docs/spec.md §3.8.4).
    movable = {own for _, _, own in deferred if own is not None}
    while deferred:
        free = []
        for k, (_, e, _) in enumerate(deferred):
            want = [y for y in sorted(movable) if _occurs(e, y) and em._reach(e, y)]
            ok = [y for y in want if not any(_occurs(e2, y) for j, (_, e2, _) in enumerate(deferred) if j != k)]
            free.append((len(ok) < len(want), k, ok))
        _, k, ys = min(free)
        lhs, e, _ = deferred.pop(k)
        em.moves = {}
        for y in ys:
            em.moves.update(em.takes(e, y, "std::mem::take(&mut s.%s)" % ident(y), hoist=len(ys) == 1))
        movable -= set(ys)
        lines.append("    let %s = %s;" % (lhs, em.expr(e, scope)))
        em.moves, em.hoists = {}, {}
    for n in commits:
        r = width_range(model, n)
        v = "v" if r is None else 'rt::fit(v, %d, %d, "%s")?' % (r[0], r[1], n)
        lines += ["    for (k, v) in w_%s {" % ident(n)]
        if watched and n in watched:
            lines.append("        s.dirty_%s.push(k.0);" % watched[n])
        lines += ["        s.%s.set(k, %s);" % (ident(n), v), "    }"]
    for n, t in writes:
        if isinstance(t, tuple) and t[0] == "set":
            # a set updated in place by its own additions and removals (no copy of the set)
            for kind, rv, cv in t[1]:
                ind = "    "
                if cv is not None:
                    lines.append("    if %s {" % cv)
                    ind = "        "
                if kind == "add":
                    lines.append(ind + "for x in %s {" % rv)
                    lines.append(ind + "    let k = x.clone();")
                    lines.append(ind + "    if s.%s.insert(x) {" % ident(n))
                else:
                    lines.append(ind + "for x in %s {" % rv)
                    lines.append(ind + "    let k = x.clone();")
                    lines.append(ind + "    if s.%s.remove(&x) {" % ident(n))
                if watched and n in watched:
                    lines.append(ind + "        s.dirty_%s.push(k.0);" % watched[n])
                else:
                    lines.append(ind + "        let _ = k;")
                lines += [ind + "    }", ind + "}"]
                if cv is not None:
                    lines.append("    }")
            continue
        if isinstance(t, tuple):
            if t[0] == "pop":
                lines += ["    if s.%s.pop_front().is_none() {" % ident(n),
                          '        return Err(StepError::Undefined("tail"));', "    }"]
            else:
                lines.append("    s.%s.extend(%s);" % (ident(n), t[1]))
            continue
        r = width_range(model, n)
        if watched and n in watched:
            lines += ["    for x in s.%s.symmetric_difference(&%s) {" % (ident(n), t),
                      "        s.dirty_%s.push(x.0);" % watched[n], "    }"]
        lines.append("    s.%s = %s;" % (ident(n), t if r is None else 'rt::fit(%s, %d, %d, "%s")?' % (t, r[0], r[1], n)))
    # A list entry changed in place (§3.10.7), last: every value that reads the old entry
    # has been computed, and the key function is not written by the summand.
    for n, kind, f in listops:
        t = ident(n)
        if kind == "ins":
            lines.append("    rt::ins_by_key(s.%s.slot(lk_%s), lu_%s, |x| (%s).get(x));" % (t, t, t, f))
        elif kind == "tail":
            lines.append("    rt::pop_first(s.%s.slot(lk_%s))?;" % (t, t))
        else:
            lines.append("    rt::rem_first(s.%s.slot(lk_%s), &lu_%s);" % (t, t, t))
    return lines


def _list_op(em, model, s, n, e, watched):
    """The new value of the list column n as one entry k changed in place (docs/spec.md
    §3.10.7): ("ins", k, u, F) for n[k -> f(u, n(k), F)] with f an insert ordered by a key
    and n declared kept sorted by F (`key_sorted`), F not written by the summand;
    ("tail", k, None, None) for n[k -> tail(n(k))]; ("rem", k, u, None) for
    n[k -> r(n(k), u)] with r a first-occurrence removal. Else None."""
    if (watched and n in watched) or not (isinstance(e, App) and isinstance(e.head, Op) and e.head.name == "@func_update"
                                         and len(e.args) == 3 and isinstance(e.args[0], Var) and e.args[0].name == n):
        return None
    k, v = e.args[1], e.args[2]
    if not (isinstance(v, App) and isinstance(v.head, Op)):
        return None

    def own(x):
        return isinstance(x, App) and isinstance(x.head, Var) and x.head.name == n and len(x.args) == 1 \
            and show(x.args[0]) == show(k)
    h, srt = v.head.name, em.sorted
    if h == "tail" and len(v.args) == 1 and own(v.args[0]):
        return ("tail", k, None, None)
    if h in srt.removes and own(v.args[0]) and not _occurs(v.args[1], n):
        return ("rem", k, v.args[1], None)
    f = v.args[2] if h in srt.key_inserts else None
    if f is not None and own(v.args[1]) and not _occurs(v.args[0], n) and isinstance(f, Var) \
            and model.config.key_sorted.get(n) == f.name and all(m != f.name for m, _ in s.assigns):
        return ("ins", k, v.args[0], f)
    return None


def _inplace(em, model, s, n, e, fps: set, watched) -> bool:
    """Whether the new value of the function parameter n is applied in place: n is a
    sorted column (docs/spec.md §3.10.6), no index reads it, and every other new value
    of the summand that reads it is computed before the change (see _fire_body: all
    but those of sets and lists moved or changed in place, computed after it)."""
    srt = em.sorted
    if n not in srt.columns or (watched and n in watched) or not srt.preserves(e, n, srt.columns, srt.walks):
        return False
    for m2, e2 in s.assigns:
        if m2 == n or not _occurs(e2, n):
            continue
        late = m2 not in fps and m2 not in em.deques and (
            _set_inplace(em, model, m2, e2) is not None or (_movable(em, model, m2) and not (watched and m2 in watched)))
        if late or m2 in srt.columns:
            return False
    return True


def _apply_inplace(em, n: str, e, scope: dict, lines: list) -> None:
    """Statements that change the sorted column n in place to its new value e: an
    ordered insert or merge at one key, or a sorted walk given the column itself."""
    col = "s.%s" % ident(n)
    stack = [("fe", e, "    ")]
    while stack:
        kind, x, i = stack.pop()
        if kind == "line":
            lines.append(i + x)
            continue
        if isinstance(x, Var):
            continue
        h = x.head.name if isinstance(x, App) and isinstance(x.head, Op) else None
        if h == "if":
            stack.append(("line", "}", i))
            stack.append(("fe", x.args[2], i + "    "))
            stack.append(("line", "} else {", i))
            stack.append(("fe", x.args[1], i + "    "))
            stack.append(("line", "if %s {" % em.expr(x.args[0], scope), i))
        elif h == "@func_update":
            kind2, v = em.sorted.update_value(x.args[2], x.args[1], n)
            k = em.expr(x.args[1], scope)
            if kind2 == "ins":
                lines.append(i + "{ let k = %s; let u = %s; rt::ins_sorted(%s.slot(k), u); }" % (k, em.expr(v, scope), col))
            else:
                lines.append(i + "{ let k = %s; let l = %s; if !l.is_empty() { let t = %s.slot(k); for x in l { rt::ins_sorted(t, x); } } }"
                             % (k, em.expr(v, scope), col))
        else:
            lines.append(i + "{ %s?; }" % em.call(x, scope, target="&mut %s" % col))


def _occurs(e, name: str) -> bool:
    return any(isinstance(x, Var) and x.name == name for x in walk(e))


def _movable(em, model, n) -> bool:
    """A parameter whose new value may be built from its old one by a move: a set or a
    list held as a plain value (not a deque)."""
    return n not in em.deques and em.types.kind(dict(model.lps.params)[n]) in ("FSet", "List")


def _set_inplace(em, model, n, e):
    """The update of a set parameter n by its own additions and removals, as a list of
    (add | sub, operand, condition) applied in order to the old value, or None. The forms:
    n + a, n - a (left-nested), if(c, form, n), if(c, n, form), and n itself. Every operand and
    condition is read on the old state, as the whole new value would be."""
    if n in em.deques or em.types.kind(dict(model.lps.params)[n]) != "FSet":
        return None
    ops = []
    x, cond = e, None
    if isinstance(x, Var) and x.name == n:
        return []
    h = getattr(x, "head", None)
    if isinstance(h, Op) and h.name == "if" and len(x.args) == 3:
        c, a, b = x.args
        if isinstance(b, Var) and b.name == n:
            x, cond = a, (True, c)
        elif isinstance(a, Var) and a.name == n:
            x, cond = b, (False, c)
        else:
            return None
    chain = []
    while True:
        h = getattr(x, "head", None)
        if isinstance(h, Op) and h.name in ("+", "-") and len(x.args) == 2:
            chain.append(("add" if h.name == "+" else "sub", x.args[1]))
            x = x.args[0]
            continue
        break
    if not (isinstance(x, Var) and x.name == n) or not chain:
        return None
    for kind, r in reversed(chain):
        ops.append((kind, r, cond))
    return ops


# ------------------------------------------------------------------ the stepper (docs/spec.md §3.8)

_ORDERS = {"least": 0, "key": 1, "newest": 2, "oldest": 3, "within": 4}


def _stepper_fields(st) -> list:
    sorts = sorted({ix.sort for ix in st.indexes} | set(st.watched.values()))
    return [("dirty_%s" % x, "Vec<u32>") for x in sorts] + [("ix_%s" % ix.name, "Ix") for ix in st.indexes]


def _ix_fn(em: Emitter, ix, part: str, arg: str | None, argty: str | None, res: str, mut: bool = False) -> list:
    args, rhs = ix.eqs[part]
    fps = {n for n, srt in em.lps.params if is_fun(em.lps, srt)}
    scope = {}
    for a in args:
        if a.startswith("ix_p_"):
            p = a[len("ix_p_"):]
            scope[a] = ("(&s.%s)" % ident(p), "ref") if p in fps else ("s.%s" % ident(p), "val")
        else:
            scope[a] = (arg, "val")
    sig = "s: &S" + (", %s: %s" % (arg, argty) if arg else "")
    return ["fn ix%s_%s(%s) -> Result<%s, StepError> {" % (part, ix.name, sig, res),
            "    Ok(%s)" % em.expr(rhs, scope), "}", ""]


def _emit_stepper(em: Emitter, model, st, inputs, outputs) -> list:
    m = st.model
    lps = m.lps
    fps = {n for n, srt in lps.params if is_fun(lps, srt)}
    out = []
    for ix in st.indexes:
        it = type_name(ix.sort)
        out += _ix_fn(em, ix, "mem", "v", it, "bool")
        ncls = max(1, len(ix.classes))
        if "cls" in ix.eqs:
            ct = type_name(ix.cls_sort)
            out += _ix_fn(em, ix, "cls", "v", it, ct)
            out += _ix_fn(em, ix, "adm", "k", ct, "bool")
            out += ["fn ixclsno_%s(k: &%s) -> usize {" % (ix.name, ct), "    match k {"]
            out += ["        %s::%s => %d," % (ct, ident(c), j) for j, c in enumerate(ix.classes)]
            out += ["    }", "}", ""]
        if "key" in ix.eqs:
            out += _ix_fn(em, ix, "key", "v", it, "i128")
        out += _ix_fn(em, ix, "bound", None, None, "i128")
        if "cls" in ix.eqs:
            ct = type_name(ix.cls_sort)
            classes = ", ".join("%s::%s" % (ct, ident(c)) for c in ix.classes)
            out += ["fn ixfirst_%s(s: &S) -> Result<%s, StepError> {" % (ix.name, it),
                    "    let mut best: Option<(i128, u32)> = None;",
                    "    for (c, k) in [%s].into_iter().enumerate() {" % classes,
                    "        if !ixadm_%s(s, k)? {" % ix.name, "            continue;", "        }",
                    "        if let Some(f) = s.ix_%s.front(c) {" % ix.name,
                    "            if best.is_none_or(|b| f < b) {", "                best = Some(f);", "            }", "        }",
                    "    }",
                    '    best.map(|(_, v)| %s(v)).ok_or(StepError::Undefined("first(%s)"))' % (it, ix.name), "}", "",
                    "fn ixsome_%s(s: &S) -> Result<bool, StepError> {" % ix.name,
                    "    for (c, k) in [%s].into_iter().enumerate() {" % classes,
                    "        if ixadm_%s(s, k)? && s.ix_%s.front(c).is_some() {" % (ix.name, ix.name),
                    "            return Ok(true);", "        }", "    }", "    Ok(false)", "}", ""]
        else:
            out += ["fn ixfirst_%s(s: &S) -> Result<%s, StepError> {" % (ix.name, it),
                    '    s.ix_%s.front(0).map(|(_, v)| %s(v)).ok_or(StepError::Undefined("first(%s)"))' % (ix.name, it, ix.name),
                    "}", "", "fn ixsome_%s(s: &S) -> Result<bool, StepError> {" % ix.name,
                    "    Ok(s.ix_%s.front(0).is_some())" % ix.name, "}", ""]
        cls = "ixclsno_%s(&ixcls_%s(s, n)?)" % (ix.name, ix.name) if "cls" in ix.eqs else "0"
        key = "ixkey_%s(s, n)?" % ix.name if "key" in ix.eqs else "0"
        if ix.within:
            key = "s.ix_%s.key_of(v)?" % ix.within
        out += ["fn ixrefresh_%s(s: &mut S, v: u32) -> Result<(), StepError> {" % ix.name,
                "    let n = %s(v);" % it,
                "    if ixmem_%s(s, n)? {" % ix.name,
                "        let (c, k) = (%s, %s);" % (cls, key),
                "        s.ix_%s.refresh(v, true, c, k)" % ix.name,
                "    } else {", "        s.ix_%s.refresh(v, false, 0, 0)" % ix.name, "    }", "}", ""]
    sorts = sorted({ix.sort for ix in st.indexes} | set(st.watched.values()))
    out += ["/// The indexes refreshed at the ids the last summand wrote (docs/spec.md §3.8.3).",
            "fn refresh(s: &mut S) -> Result<(), StepError> {"]
    for srt in sorts:
        out += ["    let d = std::mem::take(&mut s.dirty_%s);" % srt, "    let mut seen = BTreeSet::new();",
                "    for v in d {", "        if !seen.insert(v) {", "            continue;", "        }"]
        out += ["        ixrefresh_%s(s, v)?;" % ix.name for ix in st.indexes if ix.sort == srt]
        out += ["    }"]
    out += ["    Ok(())", "}", ""]
    out += ["fn stepper_init(s: &mut S) -> Result<(), StepError> {"]
    for ix in st.indexes:
        out.append("    s.ix_%s = Ix::new(%d, %d);" % (ix.name, _ORDERS[ix.order], max(1, len(ix.classes))))
    for ix in st.indexes:
        out += ["    let b = u32::try_from(ixbound_%s(s)?).map_err(|_| StepError::Overflow(\"bound\"))?;" % ix.name,
                "    for v in 0..b {", "        ixrefresh_%s(s, v)?;" % ix.name, "    }"]
    out += ["    Ok(())", "}", ""]
    # Inputs: the refined Core's (§3.8.2).
    by_label: dict = {}
    for k in st.inputs:
        s = lps.summands[k]
        a = next(a for a in inputs if a.label == s.label and len(a.sorts) == len(s.args))
        by_label.setdefault((a.label, len(a.sorts)), []).append(k)
        out += _emit_summand(em, m, s, "_s%d" % k, fps, st.watched)
    pscope = {}
    for n, _ in lps.params:
        pscope[n] = ("(&s.%s)" % ident(n), "ref") if n in fps else ("s.%s" % ident(n), "val")
    outs = []
    for j, k in enumerate(st.outputs):
        s = lps.summands[k]
        a = next(a for a in outputs if a.label == s.label and len(a.sorts) == len(s.args))
        out += ["// %s" % s.name, "fn out%d(s: &mut S, out: &mut Vec<Action>) -> Result<bool, StepError> {" % j,
                "    if !(%s) {" % em.expr(s.cond, pscope), "        return Ok(false);", "    }"]
        args = [em.expr(x, pscope) for x in s.args]
        out.append("    let act = Action::%s%s;" % (camel(a.label), "(%s)" % ", ".join(args) if args else ""))
        out += _fire_body(em, m, s, dict(pscope), fps, st.watched)
        out += ["    out.push(act);", "    Ok(true)", "}", ""]
        outs.append("out%d" % j)
    out += ["const OUTS: [fn(&mut S, &mut Vec<Action>) -> Result<bool, StepError>; %d] = [%s];" % (len(outs), ", ".join(outs)), "",
            "/// The output loop (docs/spec.md §3.8.3): the first enabled output in policy",
            "/// order, until none is.",
            "fn run_loop(s: &mut S, out: &mut Vec<Action>) -> Result<(), StepError> {",
            "    let mut n: u32 = 0;", "    loop {", "        let mut fired = false;", "        for f in OUTS {",
            "            if f(s, out)? {", "                fired = true;", "                break;", "            }", "        }",
            "        if !fired {", "            return Ok(());", "        }", "        refresh(s)?;",
            "        n = n.saturating_add(1);", "        if n >= 1 << 24 {", "            return Err(StepError::StepLimit);",
            "        }", "    }", "}", ""]
    # The state's encoding, for comparing the backends.
    out += ["fn state_bytes(s: &S, out: &mut Vec<u8>) {"]
    for n, srt in lps.params:
        t = em.types.resolve(srt)
        if t[0] == "->":
            out += ["    {", "        let f = &s.%s;" % ident(n)] + em.enc_stmt(t[2], "&f.d", "        ") + \
                   ["        codec::len(f.m.len(), out);", "        for (k, v) in f.m.iter() {"] + \
                   em.enc_stmt(t[1][0], "k", "            ") + em.enc_stmt(t[2], "v", "            ") + ["        }", "    }"]
        elif t[0] == "FSet":
            out += ["    codec::len(s.%s.len(), out);" % ident(n), "    for x in s.%s.iter() {" % ident(n)] + \
                   em.enc_stmt(t[1], "x", "        ") + ["    }"]
        else:
            out += em.enc_stmt(t, "&s.%s" % ident(n), "    ")
    out += ["}", ""]
    out += ["/// The stepper of the refined Core (docs/spec.md §3.8.1, §3.8.2, §3.11.2).",
            "#[derive(Debug, Clone)]", "pub struct State {", "    s: S,", "}", "", "impl State {",
            "    pub fn new(config: Config, out: &mut Vec<Action>) -> Result<State, StepError> {",
            "        State::new_with(config, out, |_| {})", "    }", "",
            "    /// The constructor with the initial state changed first, for tests that start",
            "    /// from another initial state.", "    #[doc(hidden)]",
            "    pub fn new_with(config: Config, out: &mut Vec<Action>, f: impl FnOnce(&mut S)) -> Result<State, StepError> {",
            "        let mut s = initial(&config)?;", "        f(&mut s);", "        stepper_init(&mut s)?;",
            "        run_loop(&mut s, out)?;", "        Ok(State { s })", "    }", "",
            "    pub fn step(&mut self, event: &Event, out: &mut Vec<Action>) -> Result<(), StepError> {",
            "        let s = &mut self.s;", "        match event {"]
    for a in inputs:
        n = len(a.sorts)
        names = ["a%d" % j for j in range(n)]
        out.append("            Event::%s%s => {" % (camel(a.label), "(%s)" % ", ".join(names) if n else ""))
        ks = by_label.get((a.label, n), [])
        for i, k in enumerate(ks):
            out.append("                %sif let Some(env) = match_s%d(s%s)? {" % ("} else " if i else "", k, "".join(", " + x for x in names)))
            out.append("                    fire_s%d(s, env)?;" % k)
        if ks:
            out += ["                } else {", "                    return Err(StepError::Refused(event.clone()));", "                }"]
        else:
            out.append("                return Err(StepError::Refused(event.clone()));")
        out.append("            }")
    out += ["        }", "        refresh(s)?;", "        run_loop(s, out)", "    }", "",
            "    /// An estimate of the memory the state holds (§3.13.4).", "    pub fn bytes(&self) -> usize {",
            "        let mut b = Vec::new();", "        state_bytes(&self.s, &mut b);", "        b.len()", "    }", "",
            "    /// Every parameter's value, encoded as entries are (for comparing the backends).",
            "    pub fn state_bytes(&self) -> Vec<u8> {", "        let mut b = Vec::new();", "        state_bytes(&self.s, &mut b);",
            "        b", "    }", "", "    /// The state, for tests.", "    pub fn state(&self) -> &S {", "        &self.s", "    }",
            "", "    /// The members of an index in its order, for tests.", "    pub fn index(&self, name: &str) -> Option<Vec<u32>> {",
            "        match name {"]
    out += ['            "%s" => Some(self.s.ix_%s.ordered()),' % (ix.name, ix.name) for ix in st.indexes]
    out += ["            _ => None,", "        }", "    }", "}", ""]
    return out
