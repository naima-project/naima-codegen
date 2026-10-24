// The state space of a generated TypeScript module, explored through its transition table
// (docs/spec.md §3.18.3, §3.18.5) and written as an .aut file for ltscompare.
//
//   deno run --allow-read --allow-write explore.ts <module.ts> <config.json> <domains.json> <out.aut>
//
// From the state s0 of the constructor (`start`): in a state where an output is enabled, the first one in
// policy order is the only transition; in a quiescent state, every event over the finite domains is tried
// on every input summand. Domains: {"ids": {"<sort>": n}, "ints": [lo, hi]}.
const [modPath, configPath, domPath, outPath] = Deno.args;
const M = await import("file://" + modPath);
const config = JSON.parse(await Deno.readTextFile(configPath));
const dom = JSON.parse(await Deno.readTextFile(domPath));
const MAX = 1_000_000;

// deno-lint-ignore no-explicit-any
function values(sort: any): unknown[] {
  const range = (lo: number, hi: number) => Array.from({ length: hi - lo + 1 }, (_, k) => lo + k);
  switch (sort.kind) {
    case "bool":
      return [false, true];
    case "id":
      return range(0, dom.ids[sort.sort] - 1);
    case "enum":
      return [...sort.values];
    case "int":
      return range(sort.sort === "Pos" ? Math.max(1, dom.ints[0]) : dom.ints[0], dom.ints[1]);
  }
  throw new Error("no finite domain for an argument of sort " + sort.sort);
}

// deno-lint-ignore no-explicit-any
function events(t: any): unknown[] {
  let out: Record<string, unknown>[] = [{ type: t.type }];
  for (const a of t.args) out = out.flatMap((e) => values(a.sort).map((v) => ({ ...e, [a.name]: v })));
  return out;
}

const evs = M.inputs.map(events);
const index = new Map<string, number>();
const queue: unknown[] = [];
const edges: string[] = [];
const id = (s: unknown) => {
  const k = M.stateKey(s);
  let n = index.get(k);
  if (n === undefined) {
    n = index.size;
    if (n >= MAX) throw new Error("more than " + MAX + " states");
    index.set(k, n);
    queue.push(s);
  }
  return n;
};
id(M.start(config));
for (let i = 0; i < queue.length; i++) {
  const s = queue[i];
  let fired = false;
  for (const t of M.outputs) {
    const r = t.next(s);
    if (r !== null) {
      edges.push(`(${i},"${M.mcrl2Label({ action: r.action })}",${id(r.state)})`);
      fired = true;
      break;
    }
  }
  if (fired) continue;
  M.inputs.forEach((t: { next: (s: unknown, e: unknown) => unknown }, k: number) => {
    for (const e of evs[k]) {
      const n = t.next(s, e);
      if (n !== null) edges.push(`(${i},"${M.mcrl2Label({ event: e })}",${id(n)})`);
    }
  });
}
await Deno.writeTextFile(outPath, [`des (0,${edges.length},${index.size})`, ...edges].join("\n") + "\n");
console.log(JSON.stringify({ states: index.size, transitions: edges.length }));
