import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createI18n } from "vue-i18n";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function loadCatalog(relPath) {
  const source = readFileSync(resolve(root, relPath), "utf8").replace(/^\s*export default\s*/, "return ");
  return new Function(source)();
}

function flatten(obj, prefix = "", out = new Map()) {
  for (const [key, value] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      flatten(value, full, out);
    } else {
      out.set(full, String(value));
    }
  }
  return out;
}

const en = await loadCatalog("src/i18n/en.ts");
const zh = await loadCatalog("src/i18n/zh-Hans.ts");

const enMap = flatten(en);
const zhMap = flatten(zh);

const missingInZh = [...enMap.keys()].filter((k) => !zhMap.has(k));
const extraInZh = [...zhMap.keys()].filter((k) => !enMap.has(k));

if (missingInZh.length || extraInZh.length) {
  if (missingInZh.length) console.error("Missing in zh-Hans.ts:", missingInZh);
  if (extraInZh.length) console.error("Extra in zh-Hans.ts:", extraInZh);
  process.exit(1);
}

// Verify every single message compiles cleanly through vue-i18n (catches unescaped @ or broken syntax)
const compileErrors = [];
const i18n = createI18n({
  legacy: false,
  locale: "en",
  messages: { en, "zh-hans": zh },
  missingWarn: false,
  fallbackWarn: false,
});

for (const [locale, map] of [
  ["en", enMap],
  ["zh-hans", zhMap],
]) {
  i18n.global.locale.value = locale;
  for (const key of map.keys()) {
    try {
      i18n.global.t(key, {
        servers: 1,
        running: 1,
        queued: 1,
        count: 1,
        label: "GPU",
        index: 1,
        server: "main",
        value: "1",
        user: "u",
        hours: 1,
        used: 1,
        limit: 1,
        time: "12:00",
        from: "a",
        to: "b",
        free: "1",
        total: "2",
        name: "g",
        delivered: 1,
        read: 1,
        page: 1,
      });
    } catch (err) {
      compileErrors.push(`${locale}:${key} -> ${err.message}`);
    }
  }
}

if (compileErrors.length) {
  console.error("vue-i18n message compilation errors:\n" + compileErrors.join("\n"));
  process.exit(1);
}

console.log(`i18n coverage & syntax verified: ${enMap.size} keys matched between en and zh-Hans.`);
