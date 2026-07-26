// 存证截图去重校验:扫描 e2e/evidence 下所有 PNG,若有任意两张字节完全相同
// (md5 一致)即判失败,杜绝「拷贝同一张图糊弄不同步骤存证」(验收 #1)。
// 用法: node scripts/check-evidence-unique.mjs [dir ...]
// 缺省扫描 frontend/e2e/evidence。退出码 0=通过,1=发现重复。
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const ROOT = join(HERE, '..');
const dirs = (process.argv.slice(2).length
  ? process.argv.slice(2)
  : [join(ROOT, 'e2e', 'evidence')]
).map((d) => (d.startsWith('/') ? d : join(ROOT, d)));

function walk(dir) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...walk(full));
    else if (name.toLowerCase().endsWith('.png')) out.push(full);
  }
  return out;
}

const byHash = new Map();
let dupes = 0;
for (const dir of dirs) {
  for (const file of walk(dir)) {
    const hash = createHash('md5').update(readFileSync(file)).digest('hex');
    const rel = relative(ROOT, file);
    if (byHash.has(hash)) {
      dupes += 1;
      console.error(`DUP  ${hash}  ${byHash.get(hash)}  ==  ${rel}`);
    } else {
      byHash.set(hash, rel);
    }
  }
}

if (dupes > 0) {
  console.error(`\n[evidence-unique] FAIL: ${dupes} 组重复截图(每步存证必须互不相同)`);
  process.exit(1);
}
console.log(`[evidence-unique] OK: ${byHash.size} 张截图均唯一`);
