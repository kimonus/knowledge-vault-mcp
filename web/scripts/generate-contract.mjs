import { readFile, writeFile } from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";

const contract = JSON.parse(
  await readFile(
    new URL(
      "../../dashboard/src/knowledge_vault_dashboard/contract.json",
      import.meta.url,
    ),
    "utf8",
  ),
);
const schemas = {};
const paths = {};
const name = (path) => path.replaceAll(/[{}]/g, "").replaceAll("/", "_");
function lift(schema, prefix) {
  const copy = structuredClone(schema);
  const defs = copy.$defs ?? {};
  delete copy.$defs;
  const rewrite = (value) => {
    if (Array.isArray(value)) return value.map(rewrite);
    if (value && typeof value === "object")
      return Object.fromEntries(
        Object.entries(value).map(([key, val]) => [
          key,
          key === "$ref"
            ? val.replace("#/$defs/", `#/components/schemas/${prefix}_`)
            : rewrite(val),
        ]),
      );
    return value;
  };
  for (const [key, val] of Object.entries(defs))
    schemas[`${prefix}_${key}`] = rewrite(val);
  schemas[prefix] = rewrite(copy);
  return { $ref: `#/components/schemas/${prefix}` };
}
for (const [operation, pair] of Object.entries(contract.schemas)) {
  if (operation === "capabilities") continue;
  const prefix = name(operation);
  const entry = {
    operationId: prefix,
    responses: {
      200: {
        description: "Untrusted retained knowledge",
        content: {
          "application/json": {
            schema: lift(pair.output, `${prefix}_response`),
          },
        },
      },
    },
  };
  if (pair.input)
    entry.requestBody = {
      required: true,
      content: {
        "application/json": { schema: lift(pair.input, `${prefix}_request`) },
      },
    };
  paths[`/dashboard/api/v1/${operation}`] = {
    [pair.input ? "post" : "get"]: entry,
  };
}
const spec = {
  openapi: "3.1.0",
  info: { title: "Knowledge Vault browser read contract", version: "1" },
  paths,
  components: { schemas },
};
let output = astToString(await openapiTS(spec, { defaultNonNullable: false }));
output +=
  "\n// The operation names are the fixed BFF allowlist, not arbitrary proxy paths.\n";
output +=
  "export interface ReadRequests {\n" +
  Object.entries(contract.schemas)
    .filter(([op, pair]) => op !== "capabilities" && pair.input)
    .map(
      ([op]) =>
        `  ${JSON.stringify(op)}: components["schemas"][${JSON.stringify(name(op) + "_request")}];`,
    )
    .join("\n") +
  "\n}\n";
output +=
  "export interface ReadResponses {\n" +
  Object.keys(contract.schemas)
    .filter((op) => op !== "capabilities")
    .map(
      (op) =>
        `  ${JSON.stringify(op)}: components["schemas"][${JSON.stringify(name(op) + "_response")}];`,
    )
    .join("\n") +
  "\n}\n";
const target = new URL("../src/api/schema.d.ts", import.meta.url);
if (process.argv.includes("--check")) {
  if ((await readFile(target, "utf8")) !== output)
    throw new Error("Generated browser contract drift; run npm run generate");
} else await writeFile(target, output);
