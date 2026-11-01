// The acceptor of a generated TypeScript module on given entries, with its state after each
// (docs/spec.md §3.8.9).
//
//   deno run --allow-read accept.ts <module.ts> <runs.json>
//
// runs: [{"config": {...}, "entries": [{"event": ...} | {"action": ...}]}], each run from a new acceptor.
// Prints one JSON line per entry: the acceptor's state, or {"error": class name}.
const [modPath, runsPath] = Deno.args;
const M = await import("file://" + modPath);
const runs = JSON.parse(await Deno.readTextFile(runsPath));
const out: string[] = [];
for (const { config, entries } of runs) {
  const acc = new M.Acceptor(config);
  for (const e of entries) {
    try {
      acc.accept(e);
      out.push(JSON.stringify(acc.state));
    } catch (x) {
      out.push(JSON.stringify({ error: (x as Error).name }));
    }
  }
}
console.log(out.join("\n"));
