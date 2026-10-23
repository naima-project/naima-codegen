"""The per-id layout (docs/spec.md §3.13.4): the bytes every id of an id sort takes in
the columns of the catalogue's representations (§3.10.3), and the limit G11 holds them
to (§3.6.8).

Per id: a column `I -> X` takes the bytes of one `X` at its width (§3.6.6: a declared
width, else the sort's default, `u64` for `Nat` and `Pos`, `i64` for `Int`, `u32` for an id
sort); `Bool` and an enumeration take one byte, a record the sum of its fields, a union
one byte of tag and its largest constructor's fields. A column of lists or sets of ids is a
per-id list: start and length, eight bytes per id, and the bytes of each entry, shown
separately. Every index over the sort adds one bit per id (its membership), eight bytes for
the stamps of a `newest` or `oldest` one; every frontier set of ids read by `least` adds a
bit (the ordered bitset). The layout is the same in both backends: the catalogue's Python
representations use the same widths (`array.array` type codes, `bytearray`)."""

from __future__ import annotations

from .expr import App, Op, Var, walk
from .lower import Types

_WIDTH_BYTES = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "i32": 4, "i64": 8}


class Layout:
    def __init__(self, model, st=None):
        self.model = model
        self.lps = model.lps
        self.types = Types(self.lps, model.config.id_sorts)
        self.ids = list(model.config.id_sorts)
        self.params = {p.name: p for p in model.params}

    def value_bytes(self, sort, width: str | None = None) -> tuple[int, int]:
        """(bytes per id, bytes per entry of a per-id list) of a value of this sort."""
        s = self.types.resolve(sort)
        if s[0] in ("List", "FSet"):
            e, _ = self.value_bytes(s[1])
            return 8, e
        if s[0] != "id":
            return 8, 0
        name = s[1]
        if name == "Bool":
            return 1, 0
        if name in ("Nat", "Pos", "Int"):
            w = width or self.model.config.widths.get(name) or ("i64" if name == "Int" else "u64")
            return _WIDTH_BYTES[w], 0
        if name in self.ids:
            return _WIDTH_BYTES[self.model.config.widths.get(name) or "u32"], 0
        st = self.lps.structs.get(name)
        if st is None:
            return 8, 0
        cons = st.constructors
        if all(not f for _, f, _ in cons):
            return 1, 0
        sizes = [sum(self.value_bytes(fs)[0] for _, fs in f) for _, f, _ in cons]
        return (sizes[0] if len(cons) == 1 else 1 + max(sizes)), 0

    def compute(self, st=None) -> dict:
        """{id sort: {"columns": {name: bytes}, "entries": {name: bytes}, "index_bits": n,
        "stamps": bytes, "bytes": total per id}}; bits are rounded up to whole bytes."""
        out = {}
        least = set()
        if st is not None:
            for s in st.model.lps.summands:
                for e in [s.cond, *s.args, *(e for _, e in s.assigns)]:
                    for x in walk(e):
                        if isinstance(x, App) and isinstance(x.head, Op) and x.head.name.startswith("ix_least_") \
                                and x.args and isinstance(x.args[0], Var):
                            least.add(x.args[0].name)
        for idsort in self.ids:
            cols, entries = {}, {}
            for n, srt in self.lps.params:
                s = self.types.resolve(srt)
                if s[0] != "->" or len(s[1]) != 1 or self.types.resolve(s[1][0]) != ("id", idsort):
                    continue
                p = self.params.get(n)
                b, e = self.value_bytes(s[2], getattr(p, "width", None))
                cols[n] = b
                if e:
                    entries[n] = e
            bits, stamps = 0, 0
            for ix in (st.indexes if st is not None else []):
                if ix.sort == idsort:
                    bits += 1
                    if ix.order in ("newest", "oldest"):
                        stamps += 8
            for n, srt in self.lps.params:
                if n in least and self.types.resolve(srt) == ("FSet", ("id", idsort)):
                    bits += 1
            total = sum(cols.values()) + (bits + 7) // 8 + stamps
            out[idsort] = {"columns": dict(sorted(cols.items())), "entries": dict(sorted(entries.items())),
                           "index_bits": bits, "stamps": stamps, "bytes": total}
        return out


def layout(model, st=None) -> dict:
    """The manifest's `layout`: the same table for both backends (see the module)."""
    t = Layout(model).compute(st)
    return {"rust": t, "python": t}
