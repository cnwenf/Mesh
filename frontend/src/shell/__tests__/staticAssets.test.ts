import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const frontendRoot = process.cwd();

describe('static shell assets', () => {
  it('references the local SVG favicon from the document head', () => {
    const index = readFileSync(resolve(frontendRoot, 'index.html'), 'utf8');
    expect(index).toContain('<link rel="icon" type="image/svg+xml" href="/favicon.svg" />');
  });

  it('keeps the favicon self-contained and inert', () => {
    const favicon = readFileSync(resolve(frontendRoot, 'public/favicon.svg'), 'utf8');
    expect(favicon).toContain('viewBox="0 0 32 32"');
    expect(favicon).toContain('<title id="title">Mesh</title>');
    expect(favicon).not.toMatch(/<(?:script|image|foreignObject)\b/iu);
    expect(favicon).not.toMatch(/\b(?:href|onload|onclick)=/iu);
  });
});
