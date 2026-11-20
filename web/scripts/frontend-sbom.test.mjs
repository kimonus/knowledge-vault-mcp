import assert from "node:assert/strict";
import test from "node:test";
import { runtimeSbom } from "./frontend-sbom.mjs";

function fixture() {
  return {
    bomFormat: "CycloneDX",
    metadata: { component: { "bom-ref": "app@1.0.0" } },
    components: [
      {
        "bom-ref": "react",
        name: "react",
        version: "19.3.1",
        scope: "optional",
      },
      { "bom-ref": "dom", name: "react-dom", version: "19.3.1" },
      { "bom-ref": "scheduler", name: "scheduler", version: "0.28.0" },
      { "bom-ref": "test-library", name: "test-library", version: "1.0.0" },
    ],
    dependencies: [
      { ref: "app@1.0.0", dependsOn: ["react", "dom", "test-library"] },
      { ref: "react", dependsOn: [] },
      { ref: "dom", dependsOn: ["react", "scheduler"] },
      { ref: "scheduler", dependsOn: [] },
      { ref: "test-library", dependsOn: ["react", "dom"] },
    ],
  };
}
const manifest = {
  dependencies: { react: "19.3.1", "react-dom": "19.3.1" },
  devDependencies: { "test-library": "1.0.0" },
};

test("retains runtime roots shared with dev peers and transitive dependencies", () => {
  const source = fixture();
  const before = structuredClone(source);
  const result = runtimeSbom(source, manifest);
  assert.deepEqual(
    result.components.map((component) => component.name),
    ["react", "react-dom", "scheduler"],
  );
  assert.deepEqual(result.dependencies[0].dependsOn, ["react", "dom"]);
  assert.deepEqual(result.dependencies[2].dependsOn, ["react", "scheduler"]);
  assert.ok(
    !result.dependencies.some(
      (dependency) => dependency.ref === "test-library",
    ),
  );
  assert.deepEqual(source, before);
});

test("fails closed for missing or ambiguous exact runtime roots", () => {
  assert.throws(
    () => runtimeSbom(fixture(), { dependencies: { react: "19.3.2" } }),
    /Missing or ambiguous/,
  );
  const ambiguous = fixture();
  ambiguous.components.push({
    "bom-ref": "duplicate-react",
    name: "react",
    version: "19.3.1",
  });
  ambiguous.dependencies[0].dependsOn.push("duplicate-react");
  ambiguous.dependencies.push({ ref: "duplicate-react", dependsOn: [] });
  assert.throws(() => runtimeSbom(ambiguous, manifest), /Missing or ambiguous/);
  const missing = fixture();
  missing.dependencies[0].dependsOn = ["test-library"];
  assert.throws(() => runtimeSbom(missing, manifest), /Missing or ambiguous/);
});

test("fails closed for range, alias, prerelease or absent production pins", () => {
  for (const pin of ["^19.3.1", "latest", "npm:react@19.3.1", "19.3.1-rc.1"])
    assert.throws(
      () => runtimeSbom(fixture(), { dependencies: { react: pin } }),
      /exact stable pin/,
    );
  assert.throws(() => runtimeSbom(fixture(), {}), /No production/);
});

test("fails closed for dangling, duplicate and missing graph references", () => {
  const dangling = fixture();
  dangling.dependencies[2].dependsOn.push("missing");
  assert.throws(() => runtimeSbom(dangling, manifest), /dangling/);
  const missing = fixture();
  missing.dependencies = missing.dependencies.filter(
    (dependency) => dependency.ref !== "scheduler",
  );
  assert.throws(
    () => runtimeSbom(missing, manifest),
    /Missing dependency graph/,
  );
  const duplicate = fixture();
  duplicate.components.push(duplicate.components[0]);
  assert.throws(() => runtimeSbom(duplicate, manifest), /duplicate component/);
  const duplicateGraph = fixture();
  duplicateGraph.dependencies.push(duplicateGraph.dependencies[1]);
  assert.throws(() => runtimeSbom(duplicateGraph, manifest), /Duplicate/);
});

test("preserves dependency cycles without traversal loops", () => {
  const cyclic = fixture();
  cyclic.dependencies[3].dependsOn.push("dom");
  assert.equal(runtimeSbom(cyclic, manifest).components.length, 3);
});
