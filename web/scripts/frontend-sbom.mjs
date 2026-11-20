import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

// npm's --omit=dev also omits runtime packages shared with development peer graphs.
// Start with the full installed inventory, then follow the exact production roots.
export function runtimeSbom(bom, manifest) {
  const root = bom.metadata?.component?.["bom-ref"];
  if (
    bom.bomFormat !== "CycloneDX" ||
    typeof root !== "string" ||
    !root ||
    !Array.isArray(bom.components) ||
    !Array.isArray(bom.dependencies)
  )
    throw new Error("Expected a complete CycloneDX dependency graph");
  const components = new Map();
  for (const component of bom.components) {
    const ref = component["bom-ref"];
    if (typeof ref !== "string" || !ref || ref === root || components.has(ref))
      throw new Error("Missing or duplicate component reference");
    components.set(ref, component);
  }
  const graph = new Map();
  for (const dependency of bom.dependencies) {
    if (
      (dependency.ref !== root && !components.has(dependency.ref)) ||
      graph.has(dependency.ref) ||
      !Array.isArray(dependency.dependsOn) ||
      dependency.dependsOn.some((ref) => !components.has(ref))
    )
      throw new Error("Duplicate or dangling dependency reference");
    graph.set(dependency.ref, dependency.dependsOn);
  }
  if (!graph.has(root)) throw new Error("Missing root dependency graph");
  const declared = Object.entries(manifest.dependencies ?? {});
  if (!declared.length) throw new Error("No production dependency roots");
  const roots = declared.map(([name, version]) => {
    if (typeof version !== "string" || !/^\d+\.\d+\.\d+$/.test(version))
      throw new Error(
        `Production dependency must have an exact stable pin: ${name}`,
      );
    const matches = graph.get(root).filter((ref) => {
      const component = components.get(ref);
      const fullName = component.group
        ? `${component.group}/${component.name}`
        : component.name;
      return fullName === name && component.version === version;
    });
    if (matches.length !== 1)
      throw new Error(`Missing or ambiguous pinned production root: ${name}`);
    return matches[0];
  });
  const retained = new Set();
  const pending = [...roots];
  while (pending.length) {
    const ref = pending.pop();
    if (retained.has(ref)) continue;
    if (!graph.has(ref))
      throw new Error(`Missing dependency graph entry: ${ref}`);
    retained.add(ref);
    pending.push(...graph.get(ref));
  }
  return {
    ...bom,
    components: bom.components.filter((component) =>
      retained.has(component["bom-ref"]),
    ),
    dependencies: [
      { ref: root, dependsOn: roots },
      ...bom.dependencies.filter((dependency) => retained.has(dependency.ref)),
    ],
  };
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const [source, manifestPath, target] = process.argv.slice(2);
  if (!source || !manifestPath || !target)
    throw new Error(
      "Usage: frontend-sbom.mjs full.sbom.json package.json output.json",
    );
  const bom = JSON.parse(await readFile(source, "utf8"));
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  await writeFile(
    target,
    `${JSON.stringify(runtimeSbom(bom, manifest), null, 2)}\n`,
  );
}
