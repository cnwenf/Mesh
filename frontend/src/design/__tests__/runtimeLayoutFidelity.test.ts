import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const publicFlowCss = readFileSync(
  path.resolve(process.cwd(), 'src/design/components/publicFlow.css'),
  'utf8',
);
const patternsCss = readFileSync(
  path.resolve(process.cwd(), 'src/design/patterns/patterns.css'),
  'utf8',
);
const settingsPage = readFileSync(
  path.resolve(process.cwd(), 'src/shell/pages/SettingsPage.tsx'),
  'utf8',
);
const relativeTimeCss = readFileSync(
  path.resolve(process.cwd(), 'src/i18n/RelativeTime.css'),
  'utf8',
);

describe('runtime-observed layout fidelity', () => {
  it('keeps public authentication flows on the measured 384px frame', () => {
    expect(publicFlowCss).toMatch(
      /\.mesh-public-flow__frame\s*\{[\s\S]*?max-inline-size:\s*var\(--content-public-flow\)/,
    );
    expect(publicFlowCss).toMatch(
      /\.mesh-public-flow__card\s*\{[\s\S]*?padding:\s*var\(--space-4\)/,
    );
    expect(publicFlowCss).toMatch(
      /\.mesh-public-flow__title\s*\{[\s\S]*?font-size:\s*var\(--font-size-public-flow-title\)[\s\S]*?line-height:\s*var\(--line-height-public-flow-title\)/,
    );
  });

  it('uses a full-bleed settings canvas with 224px navigation and 704px content', () => {
    expect(settingsPage).toContain('className="mesh-settings-page"');
    expect(patternsCss).toMatch(
      /\.mesh-settings-layout\s*\{[\s\S]*?--settings-nav-width:\s*224px[\s\S]*?gap:\s*0/,
    );
    expect(patternsCss).toMatch(
      /\.mesh-settings-layout__content\s*\{[\s\S]*?max-inline-size:\s*var\(--content-settings\)/,
    );
  });

  it('turns compact settings navigation into a horizontal, scrollable tab rail', () => {
    expect(patternsCss).toMatch(
      /@media \(max-width: 599px\)[\s\S]*?\.mesh-settings-layout\s*\{[\s\S]*?align-content:\s*start[\s\S]*?\.mesh-settings-layout__list\s*\{[\s\S]*?flex-direction:\s*row[\s\S]*?overflow-x:\s*auto/,
    );
  });

  it('keeps the touch relative-time disclosure inside compact viewports', () => {
    expect(relativeTimeCss).toMatch(
      /@media \(max-width: 599px\)[\s\S]*?\.mesh-relative-time \.mesh-tooltip\s*\{[\s\S]*?position:\s*fixed[\s\S]*?inset-inline:\s*var\(--space-4\)[\s\S]*?white-space:\s*normal[\s\S]*?overflow-wrap:\s*anywhere/,
    );
  });
});
