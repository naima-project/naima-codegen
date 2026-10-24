// Entries of the action record through a generated TypeScript module: their bytes, decoded back, and the
// acceptor of the unrefined Core (docs/spec.md §3.8.9, §3.8.10).
//
//   deno run --allow-read traces.ts <module.ts> <runs.json>
//
// runs: [{"config": {...}, "entries": [{"event": ...} | {"action": ...}]}]. Prints per entry
// "<hex> <decoded back: true|false> <ok | rejection class>".
const [modPath, runsPath] = Deno.args;
const M = await import("file://" + modPath);
const runs = JSON.parse(await Deno.readTextFile(runsPath));
const hex = (b: Uint8Array) => [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
const out: string[] = [];
for (const { config, entries } of runs) {
  const acc = new M.Acceptor(config);
  for (const e of entries) {
    const b = M.encode(e);
    const back = JSON.stringify(M.decode(b)) === JSON.stringify(e);
    let res = "ok";
    try {
      acc.accept(e);
    } catch (x) {
      if (!(x instanceof M.Reject)) throw x;
      res = (x as Error).name;
    }
    out.push(`${hex(b)} ${back} ${res}`);
  }
}
console.log(out.join("\n"));
