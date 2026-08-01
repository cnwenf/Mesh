import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync('src/features/projects/projects.css', 'utf8');

function declarations(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? '';
}

describe('projects layout contract', () => {
  it('长描述、里程碑和更新正文均允许无空格内容换行', () => {
    expect(declarations('.mesh-projects__description')).toContain('overflow-wrap: anywhere');
    expect(declarations('.mesh-projects__milestone-title')).toContain('overflow-wrap: anywhere');
    expect(declarations('.mesh-projects__update-message')).toContain('overflow-wrap: anywhere');
  });

  it('项目业务组件使用 compact container 重排且保留 44px 触控命中区', () => {
    expect(css).toContain('@container (max-width: 599px)');
    expect(css).toMatch(/\.mesh-projects__health-button\s*\{[^}]*min-block-size:\s*44px/s);
    expect(css).toMatch(
      /\.mesh-projects__settings-layout\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/s,
    );
  });

  it('业务样式不新增原始颜色或 z-index', () => {
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(css).not.toMatch(/\b(?:rgb|hsl)a?\(/i);
    expect(css).not.toMatch(/\bz-index\s*:/i);
  });
});
