// 存证截图去重校验:扫描 e2e/evidence 下所有 PNG,若有任意两张字节完全相同
// (md5 一致)即判失败,杜绝「拷贝同一张图糊弄不同步骤存证」(验收 #1)。
// MES-128 的 manifest 还会 fail-closed 校验目录、README、矩阵文件名、数量与 PNG 宽度。
// 用法: node scripts/check-evidence-unique.mjs [dir ...]
// 缺省扫描 frontend/e2e/evidence。退出码 0=通过,1=证据缺失/重复。
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const ROOT = join(HERE, '..');
const dirs = (
  process.argv.slice(2).length ? process.argv.slice(2) : [join(ROOT, 'e2e', 'evidence')]
).map((d) => (d.startsWith('/') ? d : join(ROOT, d)));
const MES128_DIR = join(ROOT, 'e2e', 'evidence', 'mes111-b5');

function validateMes128Evidence() {
  const readme = join(MES128_DIR, 'README.md');
  const manifestPath = join(MES128_DIR, 'manifest.json');
  if (!existsSync(readme) || !existsSync(manifestPath)) {
    throw new Error('MES-128 evidence requires mes111-b5/README.md and manifest.json');
  }
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const expected = new Map();
  for (const [mode, viewport] of Object.entries(manifest.modes ?? {})) {
    for (const theme of manifest.themes ?? []) {
      for (const page of manifest.pages ?? []) {
        expected.set(`${mode}-${page.key}-${theme}.png`, viewport.width);
      }
    }
  }
  if (expected.size !== manifest.expected_screenshots) {
    throw new Error(
      `MES-128 manifest count mismatch: matrix=${expected.size}, expected_screenshots=${manifest.expected_screenshots}`,
    );
  }
  const actual = readdirSync(MES128_DIR).filter((name) => name.toLowerCase().endsWith('.png'));
  const missing = [...expected.keys()].filter((name) => !actual.includes(name));
  const unexpected = actual.filter((name) => !expected.has(name));
  if (missing.length > 0 || unexpected.length > 0) {
    throw new Error(
      `MES-128 evidence matrix mismatch; missing=[${missing.join(', ')}], unexpected=[${unexpected.join(', ')}]`,
    );
  }
  for (const [name, width] of expected) {
    const png = readFileSync(join(MES128_DIR, name));
    if (png.length < 24 || png.toString('ascii', 1, 4) !== 'PNG') {
      throw new Error(`MES-128 evidence is not a readable PNG: ${name}`);
    }
    const actualWidth = png.readUInt32BE(16);
    if (actualWidth !== width) {
      throw new Error(
        `MES-128 evidence width mismatch: ${name} is ${actualWidth}px, expected ${width}px`,
      );
    }
  }
}

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
const requested = process.argv.slice(2);
const validatesMes128 =
  requested.length === 0 || dirs.some((dir) => resolve(dir) === resolve(MES128_DIR));
if (validatesMes128) {
  try {
    validateMes128Evidence();
  } catch (error) {
    console.error(
      `[evidence-matrix] FAIL: ${error instanceof Error ? error.message : String(error)}`,
    );
    process.exit(1);
  }
}
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
