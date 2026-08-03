import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

describe('搜索业务层设计组件边界', () => {
  it('CommandPalette 只依赖 Mesh design 稳定接口,不绑定第三方组件 API', () => {
    const source = readFileSync(
      path.resolve(process.cwd(), 'src/shortcuts/CommandPalette.tsx'),
      'utf8',
    );

    expect(source).not.toContain("from '@appica/");
    expect(source).toMatch(/import \{[^}]*InputControl[^}]*\} from '\.\.\/design';/s);
    expect(source).not.toContain('inputSize=');
  });
});
