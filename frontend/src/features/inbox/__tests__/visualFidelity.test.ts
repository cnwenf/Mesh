import { readFileSync } from 'node:fs';
import path from 'node:path';

const css = readFileSync(path.resolve(process.cwd(), 'src/features/inbox/inbox.css'), 'utf8');

function declarationBlock(selector: string): string {
  const marker = `${selector} {`;
  const start = css.indexOf(marker);
  expect(start, `missing CSS rule ${selector}`).toBeGreaterThanOrEqual(0);
  const end = css.indexOf('}', start);
  return css.slice(start, end + 1);
}

describe('inbox collaboration visual contract', () => {
  it('uses a compact 20rem list rail and a continuous detail surface', () => {
    const layout = declarationBlock('.mesh-inbox');
    expect(layout).toContain('grid-template-columns: minmax(18rem, 20rem) minmax(0, 1fr);');
    expect(layout).toContain('gap: 0;');

    const list = declarationBlock('.mesh-inbox .mesh-conversation-layout__list');
    expect(list).toContain('border-inline-end: 1px solid var(--color-border-subtle);');
    expect(list).toContain('background: var(--color-surface);');
  });

  it('returns to the routed single-pane layout in compact containers', () => {
    expect(css).toMatch(
      /@container \(max-width: 599px\) \{[\s\S]*?\.mesh-inbox \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\);/,
    );
  });

  it('uses dense border-light notification rows with neutral selection', () => {
    const group = declarationBlock('.mesh-inbox__group');
    expect(group).toContain('border: 0;');
    expect(group).toContain('box-shadow: none;');

    const row = declarationBlock('.mesh-inbox__row');
    expect(row).toContain('min-block-size: 3.25rem;');

    const selected = declarationBlock('.mesh-inbox__row--selected');
    expect(selected).toContain('background: var(--color-surface-selected);');
    expect(selected).toContain('border-inline-start-color: transparent;');
  });

  it('keeps the preview on the same neutral plane instead of nesting a card', () => {
    const preview = declarationBlock('.mesh-inbox-preview');
    expect(preview).toContain('background: var(--color-surface);');
    expect(preview).toContain('border: 0;');
    expect(preview).toContain('box-shadow: none;');
  });
});
