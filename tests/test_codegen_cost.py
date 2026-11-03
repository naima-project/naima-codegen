"""The part of the cost rule the generator applies to its Rust today
(docs/spec.md §3.13.1): a summand's code that copies or scans a whole frontier
set or list is refused (G8) unless the declaration lists it in `cost_known`."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from naima_codegen import rustback  # noqa: E402


class WholeOperations(unittest.TestCase):
    def test_copies_and_scans_are_found_per_summand(self):
        text = "\n".join([
            "// c_a",
            "fn match1(s: &S) -> bool {",
            "    let x = m_f(s.RQ.clone(), (&s.life));",
            "    for v in s.DCQ.symmetric_difference(&n) {}",
            "}",
            "fn fire1(s: &mut S) {",
            "    let n = std::mem::take(&mut s.UQ);",
            "    let m = (s.ZQ).contains(&v);",
            "}",
            "pub fn m_k(a0: &BTreeSet<Node>) {",
            "    let y = s.RQ.clone();",
            "}",
        ])
        got = rustback.whole_operations(text, {"RQ", "DCQ", "UQ", "ZQ"})
        self.assertEqual(got, {"c_a": ["copy RQ", "scan DCQ"]})


if __name__ == "__main__":
    unittest.main()
