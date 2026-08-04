import { readFileSync } from 'node:fs';
import path from 'node:path';

const css = readFileSync(path.resolve(process.cwd(), 'src/features/members/members.css'), 'utf8');

function declarationBlock(selector: string): string {
  const marker = `${selector} {`;
  const start = css.indexOf(marker);
  expect(start, `missing CSS rule ${selector}`).toBeGreaterThanOrEqual(0);
  const end = css.indexOf('}', start);
  return css.slice(start, end + 1);
}

describe('people roster visual contract', () => {
  it('fills the inset page surface instead of centering a narrow card', () => {
    const page = declarationBlock('.mesh-members');
    expect(page).toContain('max-inline-size: none;');
    expect(page).toContain('min-block-size: 100%;');
    expect(page).toContain('margin-inline: 0;');
  });

  it('uses a dense, minimally bordered people table', () => {
    const wrap = declarationBlock('.mesh-members__table-wrap');
    expect(wrap).toContain('border: 0;');
    expect(wrap).toContain('box-shadow: none;');

    const cells = declarationBlock('.mesh-members__table th,\n.mesh-members__table td');
    expect(cells).toContain('padding: var(--space-2) var(--space-3);');
    expect(cells).toContain('block-size: 3.5rem;');
  });

  it('keeps compact cards shadowless on phone layouts', () => {
    const card = declarationBlock('.mesh-members__card');
    expect(card).toContain('box-shadow: none;');
  });
});
