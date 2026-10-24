"""The errors of the generator (docs/spec.md §3.14.1).

Every error names the file, the summand or construct, and the section it
breaks. The front end collects every error it finds before it refuses, so that
one run lists them all.
"""

from __future__ import annotations

from dataclasses import dataclass

CODES = {
    "G1": "the mCRL2 toolset version differs from the pin (§3.2.1)",
    "G2": "mcrl22lps or lpspp failed (§3.2.2)",
    "G3": "a map, equation, constant, quantifier or function-sorted argument outside §3.4 (§3.4.8 included)",
    "G4": "Core is not a linear process, its parameters differ from the declaration, a label does not start with c_, or a sum variable has a parameter's name (§3.5)",
    "G5": "a sum variable is neither an action argument, a field of a constructor in one, nor equality-fixed (§3.5.5)",
    "G6": "the interface declaration does not match the model (§3.6)",
    "G7": "a catalogue definition in the model differs from the catalogue's text (§3.3.6)",
    "G8": "an operation outside its allowed cost class or unsupported by its representation",
    "G9": "the policy is incomplete or names what is not there; an index not local (§3.7.1, §3.7.6)",
    "G10": "a construction assertion failed (§3.9.1)",
    "G11": "the per-node layout exceeds 128 bytes (§3.13.4)",
    "G12": "a sort outside §3.3, or an action argument of a sort outside §3.5.7",
    "G13": "a newest or oldest index reads a parameter written other than at ids visible in the assignments (§3.7.4)",
}


@dataclass(frozen=True)
class Diagnostic:
    """One refusal: its code, the file, the construct and the section broken."""

    code: str
    file: str
    where: str
    section: str
    message: str

    def __str__(self) -> str:
        return "%s %s: %s: %s (docs/spec.md %s)" % (self.code, self.file, self.where, self.message, self.section)


class GeneratorError(Exception):
    """The generator refuses its inputs; `diagnostics` lists every reason."""

    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = sorted(diagnostics, key=lambda d: (int(d.code[1:]), d.file, d.where, d.message))
        super().__init__("\n".join(str(d) for d in self.diagnostics))

    @property
    def codes(self) -> set[str]:
        return {d.code for d in self.diagnostics}
