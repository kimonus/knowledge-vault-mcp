import { cp, mkdir, readdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const source = fileURLToPath(new URL("../dist/", import.meta.url));
const target = fileURLToPath(
  new URL(
    "../../dashboard/src/knowledge_vault_dashboard/static/",
    import.meta.url,
  ),
);
// The target contains only generated public files. No settings/credential injection at build time.
for (const file of await readdir(new URL("../dist/assets/", import.meta.url))) {
  if (!/^[A-Za-z0-9_-]+\.(js|css)$/.test(file))
    throw new Error("Unexpected build asset");
}
await rm(target, { recursive: true, force: true });
await mkdir(target, { recursive: true });
await cp(source, target, { recursive: true });
