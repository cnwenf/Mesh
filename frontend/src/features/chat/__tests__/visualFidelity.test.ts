import { readFileSync } from 'node:fs';
import path from 'node:path';

const css = readFileSync(path.resolve(process.cwd(), 'src/features/chat/chat.css'), 'utf8');

function declarationBlock(selector: string): string {
  const marker = `${selector} {`;
  const start = css.indexOf(marker);
  expect(start, `missing CSS rule ${selector}`).toBeGreaterThanOrEqual(0);
  const end = css.indexOf('}', start);
  return css.slice(start, end + 1);
}

describe('chat collaboration visual contract', () => {
  it('uses a compact 20rem list rail with a seamless detail surface', () => {
    const layout = declarationBlock('.mesh-chat');
    expect(layout).toContain('grid-template-columns: minmax(18rem, 20rem) minmax(0, 1fr);');
    expect(layout).toContain('gap: 0;');

    const sessions = declarationBlock('.mesh-chat__sessions');
    expect(sessions).toContain('border-inline-end: 1px solid var(--color-border-subtle);');
    expect(sessions).toContain('background: var(--color-surface);');
  });

  it('returns to the routed single-pane layout in compact containers', () => {
    expect(css).toMatch(
      /@container \(max-width: 599px\) \{[\s\S]*?\.mesh-chat \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\);/,
    );
  });

  it('keeps session rows dense, borderless, and quietly selected', () => {
    const row = declarationBlock('.mesh-chat__session');
    expect(row).toContain('min-block-size: 3.5rem;');
    expect(row).toContain('border: 0;');
    expect(row).toContain('background: transparent;');

    const selected = declarationBlock(
      '.mesh-chat__session--selected,\n.mesh-chat__session--selected:hover',
    );
    expect(selected).toContain('background: var(--color-surface-selected);');
    expect(selected).toContain('box-shadow: none;');
  });
});
