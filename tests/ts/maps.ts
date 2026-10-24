// The compiled user maps of a generated TypeScript module on given arguments (docs/spec.md §3.4.8).
//
//   deno run --allow-read maps.ts <module.ts> <cases.json>
//
// cases: [{"map": name, "args": [...], "points": [...] | null}]; a stored function is {"$col": [default, [[k, v]...]]}.
// Prints one JSON line per case: the result (at each point, for a function) or {"error": class name}.
const [modPath, casesPath] = Deno.args;
const M = await import("file://" + modPath);
const cases = JSON.parse(await Deno.readTextFile(casesPath));
const out: string[] = [];
for (const c of cases) {
  const args = c.args.map((a: unknown) => {
    if (a !== null && typeof a === "object" && !Array.isArray(a) && "$col" in a) {
      const [d, kv] = (a as { $col: [unknown, [unknown, unknown][]] }).$col;
      const col = new M.internals.Col(d);
      for (const [k, v] of kv) col.set(k, v);
      return col;
    }
    return a;
  });
  try {
    const r = M.internals.maps[c.map](...args);
    out.push(JSON.stringify(c.points === null ? [r] : c.points.map((p: unknown) => r.get(p))));
  } catch (e) {
    out.push(JSON.stringify({ error: (e as Error).name }));
  }
}
console.log(out.join("\n"));
