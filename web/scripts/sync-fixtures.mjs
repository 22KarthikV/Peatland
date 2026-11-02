// Copies contracts/fixtures into src/mocks/fixtures so MSW handlers (and the Docker build,
// whose context is web/ only) do not need to reach outside web/. contracts/ is never edited.
import { cpSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const src = resolve(root, "../contracts/fixtures");
const dest = resolve(root, "src/mocks/fixtures");
rmSync(dest, { recursive: true, force: true });
mkdirSync(dest, { recursive: true });
cpSync(src, dest, { recursive: true });
console.log(`copied ${src} -> ${dest}`);
