import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(path.resolve(process.cwd(), 'src/shortcuts/shortcuts.css'), 'utf8');

describe('搜索浮层响应式 CSS 契约', () => {
  it('矮视口使用可收缩的 Dialog body,结果与帮助内容承担内部滚动', () => {
    expect(css).toContain('.mesh-dialog:has(.mesh-palette) > .mesh-dialog__body');
    expect(css).toMatch(
      /\.mesh-dialog:has\(\.mesh-palette\) > \.mesh-dialog__body[\s\S]*?min-block-size:\s*0/,
    );
    expect(css).toMatch(/\.mesh-palette__list\s*\{[\s\S]*?flex:\s*1[\s\S]*?min-block-size:\s*0/);
    expect(css).toMatch(
      /\.mesh-shortcut-help\s*\{[\s\S]*?min-block-size:\s*0[\s\S]*?overflow:\s*auto/,
    );
  });

  it('320px 手机 sheet 使用动态视口/安全区且所有操作按钮至少 44px 高', () => {
    expect(css).toContain('@media (max-width: 599px)');
    expect(css).toContain('max-block-size: 88dvh');
    expect(css).toContain('env(safe-area-inset-bottom)');
    expect(css).toMatch(
      /@media \(max-width: 599px\)[\s\S]*?\.mesh-dialog:has\(\.mesh-palette\),[\s\S]*?\.mesh-dialog:has\(\.mesh-shortcut-help\)\s*\{[\s\S]*?min-block-size:\s*0/,
    );
    expect(css).toMatch(
      /\.mesh-palette__retry,[\s\S]*?\.mesh-palette__create,[\s\S]*?\.mesh-shortcut-help__restore\s*\{[\s\S]*?min-block-size:\s*2\.75rem/,
    );
  });

  it('forced-colors 选中行的图标、副标题与快捷键显式使用系统高亮文本色', () => {
    expect(css).toMatch(
      /@media \(forced-colors: active\)[\s\S]*?\.mesh-palette__option\.mesh-palette__option--active \.mesh-palette__option-icon,[\s\S]*?\.mesh-palette__option\.mesh-palette__option--active \.mesh-palette__subtitle,[\s\S]*?\.mesh-palette__option\.mesh-palette__option--active \.mesh-palette__combo[\s\S]*?forced-color-adjust:\s*none[\s\S]*?color:\s*HighlightText/,
    );
  });
});
