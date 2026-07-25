import { describe, expect, it } from 'vitest';
import * as shortcuts from '../index';

describe('shortcuts 桶导出', () => {
  it('暴露注册表/Provider/命令面板/帮助层与格式化工具', () => {
    expect(shortcuts.useShortcutRegistry).toBeTypeOf('function');
    expect(shortcuts.useShortcutRegistry.getState).toBeTypeOf('function');
    expect(shortcuts.ShortcutProvider).toBeTypeOf('function');
    expect(shortcuts.formatCombo).toBeTypeOf('function');
    expect(shortcuts.detectMac).toBeTypeOf('function');
    expect(shortcuts.SEQUENCE_WINDOW_MS).toBe(1000);
    expect(shortcuts.CommandPalette).toBeTypeOf('function');
    expect(shortcuts.ShortcutHelp).toBeTypeOf('function');
  });
});
