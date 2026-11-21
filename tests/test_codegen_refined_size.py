"""The refined Core binds each selected variable once per expression (docs/spec.md
§3.7.2.1): its selection term is written once for each expression that reads the
variable, however often the expression reads it. Copied in at every occurrence,
the frame model's admission choice (four cases, in six summands) was written 971
times into its core-refined.mcrl2, and compiling the rewriter of a 415-state
probe system took 229 s instead of 133 s (VoxLogicA-2, 2026-11-11). The shape
here is the same: a selection of three cases over two indexes and a set, in a
summand whose condition reads the variable more and more often."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from naima_codegen.policy import read_policy  # noqa: E402
from naima_codegen.refine import refine  # noqa: E402

EXAMPLE = ROOT / "examples" / "jobqueue"
CHOICE = "if(some(retryable), first(retryable), if(some(spent), first(spent), least(Q)))"
COND = "(j in Q && slots > 0) ->\n      c_start(j)"


def c_start(reads: int) -> tuple:
    """The c_start summand of the refined Core when its condition reads j `reads`
    times more, its selection the three-case choice: (the summand's text, the
    occurrences of j in its source)."""
    core = (EXAMPLE / "model" / "core.mcrl2").read_text()
    more = "".join(" && st(j) != QUEUED" if k % 2 else " && tries(j) < 1000" for k in range(reads))
    assert COND in core
    edited = core.replace(COND, "(j in Q && slots > 0%s) ->\n      c_start(j)" % more)
    policy = (EXAMPLE / "model" / "policy.toml").read_text()
    chosen = policy.replace('j = { term = "least(Q)", when = "Q != {}" }',
                            'j = { term = "%s", when = "Q != {}" }' % CHOICE)
    assert chosen != policy
    text = refine(edited, read_policy(chosen)[0]).stepper
    part = text.split("% c_start output", 1)[1].split("\n\n", 1)[0]
    src = edited.split("%@order 2", 1)[1].split("%@observers", 1)[0]
    return part, len(re.findall(r"\bj\b", src))


class SelectionsBoundOnce(unittest.TestCase):

    def test_the_choice_is_written_once_per_expression_that_reads_it(self):
        part, uses = c_start(4)
        copies = part.count("ix_first_retryable(")
        self.assertEqual(copies, part.count("whr j ="), part)
        self.assertLess(copies, uses)

    def test_the_choice_s_copies_do_not_grow_with_the_reads_of_one_expression(self):
        counts = []
        for reads in (0, 4, 16):
            part, uses = c_start(reads)
            counts.append((part.count("ix_first_retryable("), uses))
        self.assertEqual(len({c for c, _ in counts}), 1, counts)
        self.assertEqual([u for _, u in counts], [counts[0][1] + r for r in (0, 4, 16)])


if __name__ == "__main__":
    unittest.main()
