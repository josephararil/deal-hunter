// Copies ../state/deals_history.json into public/data.json so the static app has
// something to fetch. Run before dev/build — never hand-edit public/data.json.
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "state", "deals_history.json");
const dest = join(here, "..", "public", "data.json");

const data = existsSync(src)
  ? readFileSync(src, "utf-8")
  : JSON.stringify({ entries: [] }, null, 2);

writeFileSync(dest, data);
console.log(`synced ${src} -> ${dest}`);
