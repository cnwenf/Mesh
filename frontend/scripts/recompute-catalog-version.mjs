// 重算 i18n 目录版本哈希(与 catalogLoader.ts computeCatalogVersion 同算法)。
import { readFileSync, writeFileSync } from 'node:fs';

const SEPARATOR = String.fromCharCode(0);

function djb2(messages) {
  let hash = 5381;
  for (const key of Object.keys(messages).sort()) {
    const entry = `${key}=${messages[key]}${SEPARATOR}`;
    for (let index = 0; index < entry.length; index += 1) {
      hash = (Math.imul(hash, 33) + entry.charCodeAt(index)) >>> 0;
    }
  }
  return hash.toString(16).padStart(8, '0');
}

for (const locale of ['en', 'zh-CN']) {
  const path = `src/i18n/catalogs/${locale}.json`;
  const catalog = JSON.parse(readFileSync(path, 'utf8'));
  const previous = catalog.version;
  catalog.version = djb2(catalog.messages);
  writeFileSync(path, `${JSON.stringify(catalog, null, 2)}\n`);
  console.log(locale, previous, '->', catalog.version);
}
