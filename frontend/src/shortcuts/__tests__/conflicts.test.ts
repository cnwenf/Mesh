/**
 * CI 静态断言(§4.3.1 规则 2 / §5.1):枚举快捷键声明全表,按 active context
 * 组合检查 combo 唯一性(同优先级冲突 = 编程错误)与跨上下文仲裁胜者。
 */
import { describe, expect, it } from 'vitest';
import { arbitrateShortcut } from '../registry';
import type { ShortcutDef } from '../registry';
import { SHORTCUT_DECLS } from '../shortcutDefs';

/** 声明表 → 运行时 ShortcutDef 形态(run 为空操作,仅用于仲裁计算)。 */
const DEFS: ShortcutDef[] = SHORTCUT_DECLS.map((decl) => ({
  id: decl.id,
  combo: decl.combo,
  label: decl.labelKey,
  group: decl.group,
  run: () => undefined,
}));

describe('快捷键声明全表静态断言(§4.3.1 / §5.1)', () => {
  it('同优先级(同 group)combo 唯一——冲突即编程错误', () => {
    const seen = new Map<string, string>();
    for (const decl of SHORTCUT_DECLS) {
      const key = decl.group + '::' + decl.combo;
      const previous = seen.get(key);
      expect(
        previous,
        "combo '" + decl.combo + "' duplicated in group '" + decl.group + "' by '" +
          decl.id + "' and '" + String(previous) + "'",
      ).toBeUndefined();
      seen.set(key, decl.id);
    }
  });

  it('跨上下文仲裁:issue > board > global(同键取最具体 active context)', () => {
    // s 同时在 board 与 issue 声明。
    const issueWins = arbitrateShortcut(DEFS, 's', ['board', 'issue']);
    expect(issueWins?.group).toBe('issue');
    // board 激活时 board 胜出于 global(c:全局新建 vs 看板当前列新建)。
    const boardWins = arbitrateShortcut(DEFS, 'c', ['board']);
    expect(boardWins?.group).toBe('board');
    expect(boardWins?.id).toBe('board.new.card');
    // 仅全局激活 → 全局新建。
    const globalWins = arbitrateShortcut(DEFS, 'c', []);
    expect(globalWins?.group).toBe('global');
  });

  it('chat 独占语义:esc/enter 在 chat 上下文取 chat 定义', () => {
    const esc = arbitrateShortcut(DEFS, 'esc', ['chat']);
    expect(esc?.group).toBe('chat');
    expect(esc?.id).toBe('chat.blur');
    const enter = arbitrateShortcut(DEFS, 'enter', ['chat']);
    expect(enter?.id).toBe('chat.send');
  });

  it('未激活上下文的定义不参与仲裁', () => {
    // board 未激活时 e(issue 编辑)不命中。
    expect(arbitrateShortcut(DEFS, 'e', ['board'])).toBeNull();
    expect(arbitrateShortcut(DEFS, 'e', ['issue'])?.id).toBe('issue.edit');
  });

  it('labelKey 一律为 shortcuts.* i18n 键(文案外部化,§5.1)', () => {
    for (const decl of SHORTCUT_DECLS) {
      expect(decl.labelKey.startsWith('shortcuts.')).toBe(true);
    }
  });

  it('序列键全集 G→I/B/M/A 齐备(§4.3 全局组)', () => {
    const sequences = SHORTCUT_DECLS.filter((decl) => decl.combo.startsWith('g ')).map(
      (decl) => decl.combo,
    );
    expect(sequences.sort()).toEqual(['g a', 'g b', 'g i', 'g m']);
  });
});
