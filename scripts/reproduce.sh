#!/bin/sh
# Reproduces the results of this repository from a fresh clone:
#   1. installs the pinned Python (through uv) and Rust toolchain (through rustup),
#   2. regenerates every example and checks it is byte-identical to what is committed,
#   3. runs the test suite,
#   4. runs the scheduler demo with short waits,
#   5. checks the refinement and the properties of every example with the mCRL2 tools.
# Steps that need a tool the machine does not have are skipped, and the summary at the end says which.
# The versions are in versions.env. Usage: scripts/reproduce.sh [--no-tests]
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$ROOT" || exit 2
. "$ROOT/versions.env"

RUN_TESTS=1
for a in "$@"; do
  case $a in
    --no-tests) RUN_TESTS=0 ;;
    *) echo "usage: scripts/reproduce.sh [--no-tests]" >&2; exit 2 ;;
  esac
done

PASSED=""; SKIPPED=""; FAILED=""
ok()   { PASSED="$PASSED
  ok       $1"; echo "ok: $1"; }
skip() { SKIPPED="$SKIPPED
  skipped  $1"; echo "skipped: $1"; }
bad()  { FAILED="$FAILED
  FAILED   $1"; echo "FAILED: $1" >&2; }

OUT=${TMPDIR:-/tmp}/naima-codegen-reproduce.$$
mkdir -p "$OUT" || exit 2
trap 'rm -rf "$OUT"' EXIT

# --- uv and the Python
UV=${NAIMA_UV:-$(command -v uv 2>/dev/null || true)}
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV=$HOME/.local/bin/uv
if [ -n "$UV" ]; then echo "found uv: $UV"; fi
if [ -z "$UV" ]; then
  echo "reproduce: uv is not installed. Install it from https://docs.astral.sh/uv/ and run this again." >&2
  exit 2
fi
if "$UV" python install "$PYTHON" >"$OUT/python.log" 2>&1; then ok "Python $PYTHON (uv)"; else cat "$OUT/python.log" >&2; bad "Python $PYTHON (uv)"; fi
CG="$ROOT/naima-codegen"

# --- the Rust toolchain
HAVE_CARGO=0
if ! command -v cargo >/dev/null 2>&1 && ! command -v rustup >/dev/null 2>&1 && [ -d "${CARGO_HOME:-$HOME/.cargo}/bin" ]; then
  PATH="${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"; export PATH
fi
[ -n "$(command -v rustup 2>/dev/null)" ] && echo "found rustup: $(command -v rustup)"
[ -n "$(command -v cargo 2>/dev/null)" ] && echo "found cargo: $(command -v cargo)"
if command -v rustup >/dev/null 2>&1; then
  if rustup toolchain install "$RUST" --profile minimal -c clippy -c rustfmt >"$OUT/rust.log" 2>&1; then
    ok "Rust $RUST (rustup)"; HAVE_CARGO=1
  else cat "$OUT/rust.log" >&2; bad "Rust $RUST (rustup)"; fi
elif command -v cargo >/dev/null 2>&1 && cargo --version | grep -q " $RUST "; then
  ok "Rust $RUST (found on PATH)"; HAVE_CARGO=1
else
  skip "Rust: neither rustup nor cargo $RUST found, so the Rust tests and the scheduler demo are skipped"
fi

# --- mCRL2
HAVE_MCRL2=0
mcrl2_version() { "$1" --version 2>&1 | sed -n 's/.*mCRL2 toolset \([^ ]*\).*/\1/p' | head -n 1; }
V=
if command -v mcrl22lps >/dev/null 2>&1; then V=$(mcrl2_version "$(command -v mcrl22lps)"); fi
if [ "$V" != "$MCRL2" ]; then
  # not on PATH, or another release: look in the standard install places
  OLDIFS=$IFS; IFS=:
  for d in $MCRL2_DIRS; do
    d=$(printf '%s' "$d" | sed "s#^~#$HOME#; s#{MCRL2}#$MCRL2#")
    if [ -x "$d/mcrl22lps" ] && [ "$(mcrl2_version "$d/mcrl22lps")" = "$MCRL2" ]; then PATH="$d:$PATH"; export PATH; V=$MCRL2; break; fi
  done
  IFS=$OLDIFS
fi
[ "$V" = "$MCRL2" ] && echo "found mCRL2 $V: $(command -v mcrl22lps)"
if [ -z "$V" ]; then
  skip "mCRL2: the tools are not on PATH (release $MCRL2 is needed), so regeneration, refinement and properties are skipped"
elif [ "$V" != "$MCRL2" ]; then
  skip "mCRL2: release $V is on PATH, the pin is $MCRL2, so regeneration, refinement and properties are skipped"
else
  ok "mCRL2 $MCRL2"; HAVE_MCRL2=1
fi

# --- regenerate and compare
if [ $HAVE_MCRL2 = 1 ]; then
  for e in examples/*/; do
    n=$(basename "$e")
    if "$CG" generate --check --project "examples/$n" >"$OUT/gen-$n.log" 2>&1; then ok "examples/$n: generate --check, byte-identical"
    else cat "$OUT/gen-$n.log" >&2; bad "examples/$n: generate --check"; fi
  done
else
  skip "generate --check of every example"
fi

# --- Deno, for the TypeScript tests only
if ! command -v deno >/dev/null 2>&1 && [ -x "$HOME/.deno/bin/deno" ]; then PATH="$HOME/.deno/bin:$PATH"; export PATH; fi
[ -n "$(command -v deno 2>/dev/null)" ] && echo "found deno: $(command -v deno)"

# --- the test suite
if [ $RUN_TESTS = 1 ]; then
  if "$UV" run --frozen --no-dev python -B -m unittest discover -s tests >"$OUT/tests.log" 2>&1; then
    ok "test suite: $(grep -E '^(Ran|OK|FAILED)' "$OUT/tests.log" | tr '\n' ' ')"
  else tail -n 40 "$OUT/tests.log" >&2; bad "test suite"; fi
  # The test suite skips what needs a missing tool; say so instead of counting a skip as a pass.
  [ $HAVE_MCRL2 = 0 ] && skip "test suite: the cases that need mCRL2 skip themselves"
  command -v deno >/dev/null 2>&1 || skip "test suite: the TypeScript backend's tests need Deno $DENO"
else
  skip "test suite (--no-tests)"
fi

# --- the scheduler demo, with short waits
if [ $HAVE_CARGO = 1 ]; then
  if cargo run -q --release --manifest-path examples/scheduler/app/Cargo.toml -- --min 0.2 --max 0.6 --seed 1 \
       >"$OUT/demo.log" 2>"$OUT/demo.err" && grep -q "the run is over" "$OUT/demo.log"; then
    ok "scheduler demo: $(grep -c ' ends$' "$OUT/demo.log") tasks ran and the run is over"
  else cat "$OUT/demo.log" "$OUT/demo.err" >&2; bad "scheduler demo"; fi
else
  skip "scheduler demo"
fi

# --- refinement and properties
if [ $HAVE_MCRL2 = 1 ]; then
  for e in examples/*/; do
    n=$(basename "$e")
    if "$CG" check-refinement --project "examples/$n" >"$OUT/ref-$n.log" 2>&1; then ok "examples/$n: refinement checks"
    else cat "$OUT/ref-$n.log" >&2; bad "examples/$n: refinement checks"; fi
    if [ -d "examples/$n/properties" ]; then
      if "$CG" check-refinement --properties --project "examples/$n" >"$OUT/prop-$n.log" 2>&1; then
        ok "examples/$n: $(grep -c ': holds' "$OUT/prop-$n.log") properties hold"
      else cat "$OUT/prop-$n.log" >&2; bad "examples/$n: properties"; fi
    fi
  done
  # The generic event loop, modelled over an abstract stepper (docs/event-loop.md).
  mcrl22lps -q tests/loop-model/event-loop.mcrl2 "$OUT/loop.lps" >"$OUT/loop.log" 2>&1 && lps2lts -q "$OUT/loop.lps" "$OUT/loop.lts" >>"$OUT/loop.log" 2>&1
  if [ -s "$OUT/loop.lts" ]; then
    for f in tests/loop-model/*.mcf; do
      if lts2pbes -q -f "$f" "$OUT/loop.lts" "$OUT/p.pbes" >>"$OUT/loop.log" 2>&1 && [ "$(pbessolve -q "$OUT/p.pbes" 2>>"$OUT/loop.log")" = "true" ]; then
        ok "event loop: $(basename "$f" .mcf) holds"
      else cat "$OUT/loop.log" >&2; bad "event loop: $(basename "$f" .mcf)"; fi
    done
  else cat "$OUT/loop.log" >&2; bad "event loop model: state space"; fi
else
  skip "refinement checks and properties of every example, and of the event loop's model"
fi

echo
echo "Summary"
printf '%s\n' "$PASSED" "$SKIPPED" "$FAILED" | grep -v '^$'
[ -z "$FAILED" ]
