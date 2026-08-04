import { readFileSync } from 'node:fs';
import path from 'node:path';

const css = readFileSync(path.resolve(process.cwd(), 'src/features/agents/agents.css'), 'utf8');

function declarationBlock(selector: string): string {
  const marker = `${selector} {`;
  const start = css.indexOf(marker);
  expect(start, `missing CSS rule ${selector}`).toBeGreaterThanOrEqual(0);
  const end = css.indexOf('}', start);
  return css.slice(start, end + 1);
}

describe('agent detail visual contract', () => {
  it('uses the full inset page surface', () => {
    const page = declarationBlock('.mesh-agents-detail');
    expect(page).toContain('max-inline-size: none;');
    expect(page).toContain('min-block-size: 100%;');
    expect(page).toContain('margin: 0;');
  });

  it('uses flat, neutral detail sections and dense history rows', () => {
    const panel = declarationBlock('.mesh-agents-detail__panel');
    expect(panel).toContain('box-shadow: none;');

    const cells = declarationBlock(
      '.mesh-agents-detail__versions th,\n.mesh-agents-detail__versions td',
    );
    expect(cells).toContain('padding: var(--space-2) var(--space-3);');
    expect(cells).toContain('block-size: 2.75rem;');
  });
});
