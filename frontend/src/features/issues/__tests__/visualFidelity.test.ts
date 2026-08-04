import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(path.resolve(process.cwd(), 'src/features/issues/issues.css'), 'utf8');

describe('Issues visual-fidelity contracts', () => {
  it('uses the dense Inter table treatment with subtle row separators', () => {
    expect(css).toMatch(
      /\.mesh-issues\s*\{[\s\S]*?font-family:\s*var\(--font-family\)[\s\S]*?font-size:\s*var\(--font-size-body-sm\)/,
    );
    expect(css).toMatch(
      /\.mesh-issues__row\s*\{[\s\S]*?block-size:\s*44px[\s\S]*?border-block-end:\s*1px solid var\(--color-border-subtle\)/,
    );
  });
});
