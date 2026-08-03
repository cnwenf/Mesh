import { describe, expect, it } from 'vitest';
import {
  EXPECTED_VERSIONS,
  findRootBarrelImports,
  validateLicenseText,
  validateManifest,
  validateNotice,
  validateStyleSources,
} from './check-appica-foundation.mjs';

describe('Appica foundation supply-chain gate', () => {
  it('accepts exact manifest pins and rejects ranges or misplaced build dependencies', () => {
    const valid = {
      dependencies: { '@appica/ui-react': EXPECTED_VERSIONS['@appica/ui-react'] },
      devDependencies: {
        '@tailwindcss/vite': EXPECTED_VERSIONS['@tailwindcss/vite'],
        tailwindcss: EXPECTED_VERSIONS.tailwindcss,
      },
    };
    expect(validateManifest(valid)).toEqual([]);
    expect(
      validateManifest({
        dependencies: { '@appica/ui-react': '^1.0.0' },
        devDependencies: { tailwindcss: '4.3.3' },
      }),
    ).toHaveLength(2);
  });

  it('requires package/version/source/license copyright notice', () => {
    const full =
      'Appica UI React 1.0.0 https://github.com/appica-dev/appica-ui MIT License Copyright (c) 2026 Appica UI';
    expect(validateNotice(full)).toEqual([]);
    expect(validateNotice('MIT License')).toHaveLength(3);
  });

  it('requires the installed MIT text verbatim', () => {
    const license = 'MIT License\n\nCopyright (c) 2026 Appica UI\n\nPermission granted.';
    expect(validateLicenseText(`Header\n\n${license}\n`, license)).toEqual([]);
    expect(validateLicenseText('MIT License', license)).toHaveLength(1);
  });

  it('rejects the root barrel but allows component/provider subpaths', () => {
    expect(
      findRootBarrelImports([
        { file: 'bad.ts', source: "import { Button } from '@appica/ui-react';" },
        { file: 'dynamic.ts', source: "const ui = import('@appica/ui-react');" },
        { file: 'good.ts', source: "import { Button } from '@appica/ui-react/button';" },
      ]),
    ).toEqual(['bad.ts', 'dynamic.ts']);
  });

  it('requires the Appica package as a Tailwind source', () => {
    const valid = [
      "@import '@appica/ui-react/styles.css';",
      "@import './appica-tokens.css';",
      "@source '@appica/ui-react';",
    ].join('\n');
    expect(validateStyleSources(valid)).toEqual([]);
    expect(validateStyleSources("@import '@appica/ui-react/styles.css';")).toHaveLength(2);
  });
});
