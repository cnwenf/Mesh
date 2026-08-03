#!/usr/bin/env node
/** MES-158: pinned dependency, license notice, and subpath-import supply-chain gate. */
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const EXPECTED_VERSIONS = Object.freeze({
  '@appica/ui-react': '1.0.0',
  '@tailwindcss/vite': '4.3.3',
  tailwindcss: '4.3.3',
});
export function validateManifest(manifest) {
  const errors = [];
  if (manifest.dependencies?.['@appica/ui-react'] !== EXPECTED_VERSIONS['@appica/ui-react']) {
    errors.push('@appica/ui-react must be pinned to 1.0.0');
  }
  for (const name of ['@tailwindcss/vite', 'tailwindcss']) {
    if (manifest.devDependencies?.[name] !== EXPECTED_VERSIONS[name]) {
      errors.push(`${name} must be pinned to ${EXPECTED_VERSIONS[name]}`);
    }
  }
  return errors;
}

export function validateNotice(notice) {
  const required = [
    'Appica UI React 1.0.0',
    'https://github.com/appica-dev/appica-ui',
    'MIT License',
    'Copyright (c) 2026 Appica UI',
  ];
  return required
    .filter((value) => !notice.includes(value))
    .map((value) => `notice missing: ${value}`);
}

export function validateLicenseText(notice, installedLicense) {
  return notice.includes(installedLicense.trim())
    ? []
    : ['THIRD_PARTY_NOTICES.md must contain the installed Appica MIT license verbatim'];
}

export function findRootBarrelImports(sources) {
  const rootImport = /(?:from\s*|import\s*(?:\(\s*)?)['"]@appica\/ui-react['"]/;
  return sources.filter(({ source }) => rootImport.test(source)).map(({ file }) => file);
}

export function validateStyleSources(baseCss) {
  const errors = [];
  if (!baseCss.includes("@import '@appica/ui-react/styles.css';")) {
    errors.push('base.css must import the Appica stylesheet');
  }
  if (!baseCss.includes("@import './appica-tokens.css';")) {
    errors.push('base.css must import the generated Appica token bridge');
  }
  if (!baseCss.includes("@source '@appica/ui-react';")) {
    errors.push('base.css must enable the Appica package as a Tailwind source');
  }
  return errors;
}

async function walkSource(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await walkSource(absolute)));
    else if (/\.(?:ts|tsx)$/.test(entry.name)) files.push(absolute);
  }
  return files;
}

export async function checkFoundation(root = ROOT) {
  const manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
  const installed = JSON.parse(
    await readFile(path.join(root, 'node_modules/@appica/ui-react/package.json'), 'utf8'),
  );
  const installedLicense = await readFile(
    path.join(root, 'node_modules/@appica/ui-react/LICENSE'),
    'utf8',
  );
  const notice = await readFile(path.join(root, 'THIRD_PARTY_NOTICES.md'), 'utf8');
  const baseCss = await readFile(path.join(root, 'src/design/base.css'), 'utf8');
  const sourceFiles = await walkSource(path.join(root, 'src'));
  const sources = await Promise.all(
    sourceFiles.map(async (file) => ({
      file: path.relative(root, file).split(path.sep).join('/'),
      source: await readFile(file, 'utf8'),
    })),
  );

  const errors = [
    ...validateManifest(manifest),
    ...validateNotice(notice),
    ...validateLicenseText(notice, installedLicense),
    ...validateStyleSources(baseCss),
    ...findRootBarrelImports(sources).map((file) => `${file}: use an Appica subpath import`),
  ];
  if (installed.version !== EXPECTED_VERSIONS['@appica/ui-react'] || installed.license !== 'MIT') {
    errors.push('installed @appica/ui-react must resolve to version 1.0.0 with MIT license');
  }
  if (errors.length > 0) throw new Error(errors.join('\n'));
}

const isMain = process.argv[1] === fileURLToPath(import.meta.url);
if (isMain) {
  checkFoundation()
    .then(() =>
      console.log('Appica foundation gate passed: pinned 1.0.0, MIT notice, subpath imports'),
    )
    .catch((error) => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 1;
    });
}
