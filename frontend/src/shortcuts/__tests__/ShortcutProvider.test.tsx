import { render, screen, act, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useShortcutRegistry } from '../registry';
import type { ShortcutDef } from '../registry';
import { SEQUENCE_WINDOW_MS, ShortcutProvider, detectMac, formatCombo } from '../ShortcutProvider';

const clock = { now: 1000 };

beforeEach(() => {
  clock.now = 1000;
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function registerShortcuts(defs: ShortcutDef[]): void {
  act(() => {
    useShortcutRegistry.getState().registerShortcuts(defs);
  });
}

describe('formatCombo(展示用组合键格式化)', () => {
  it('mod 在 macOS 展示为 Cmd,其他平台为 Ctrl', () => {
    expect(formatCombo('mod+k', true)).toBe('Cmd+K');
    expect(formatCombo('mod+k', false)).toBe('Ctrl+K');
  });

  it('单字符键大写;符号键原样', () => {
    expect(formatCombo('c', false)).toBe('C');
    expect(formatCombo('g', false)).toBe('G');
    expect(formatCombo('/', false)).toBe('/');
    expect(formatCombo('?', false)).toBe('?');
  });

  it('具名键映射为展示名', () => {
    expect(formatCombo('esc', false)).toBe('Esc');
    expect(formatCombo('space', false)).toBe('Space');
    expect(formatCombo('enter', false)).toBe('Enter');
    expect(formatCombo('return', false)).toBe('Enter');
    expect(formatCombo('arrowup', false)).toBe('↑');
    expect(formatCombo('shift+a', false)).toBe('Shift+A');
  });

  it('未知多字符键名原样保留', () => {
    expect(formatCombo('pageup', false)).toBe('pageup');
  });

  it('alt 在 macOS 展示为 Option', () => {
    expect(formatCombo('alt+k', true)).toBe('Option+K');
    expect(formatCombo('alt+k', false)).toBe('Alt+K');
  });

  it('缺省平台检测(jsdom 无平台信息 → 非 mac)', () => {
    expect(formatCombo('mod+k')).toBe('Ctrl+K');
  });
});

describe('detectMac', () => {
  it('navigator.platform 为 Mac 系 → true', () => {
    vi.stubGlobal('navigator', { platform: 'MacIntel' });
    expect(detectMac()).toBe(true);
    vi.stubGlobal('navigator', { platform: 'iPhone' });
    expect(detectMac()).toBe(true);
  });

  it('Windows/Linux 平台 → false', () => {
    vi.stubGlobal('navigator', { platform: 'Win32' });
    expect(detectMac()).toBe(false);
    vi.stubGlobal('navigator', { platform: 'Linux x86_64' });
    expect(detectMac()).toBe(false);
  });

  it('platform 为空时回退 userAgentData.platform', () => {
    vi.stubGlobal('navigator', { platform: '', userAgentData: { platform: 'macOS' } });
    expect(detectMac()).toBe(true);
    vi.stubGlobal('navigator', { platform: '', userAgentData: { platform: 'Windows' } });
    expect(detectMac()).toBe(false);
  });

  it('无 navigator 环境 → false', () => {
    vi.stubGlobal('navigator', undefined);
    expect(detectMac()).toBe(false);
  });
});

describe('ShortcutProvider(§6.12 快捷键体系)', () => {
  it('mod+k 打开命令面板(非 mac 为 Ctrl+K)', () => {
    const onOpenPalette = vi.fn();
    render(
      <ShortcutProvider isMac={false} onOpenPalette={onOpenPalette}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
  });

  it('mac 平台 mod=meta(Ctrl 不触发);非 mac 平台 meta 不触发', () => {
    const onOpenPalette = vi.fn();
    const { unmount } = render(
      <ShortcutProvider isMac onOpenPalette={onOpenPalette}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
    unmount();

    render(
      <ShortcutProvider isMac={false} onOpenPalette={onOpenPalette}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
  });

  it('未提供 onOpenPalette 时按 mod+k 不报错', () => {
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );
    expect(() => fireEvent.keyDown(window, { key: 'k', ctrlKey: true })).not.toThrow();
  });

  it('输入框豁免:聚焦输入框时裸键不触发,但 Ctrl/Cmd 组合仍生效', async () => {
    const runC = vi.fn();
    registerShortcuts([{ id: 'new', combo: 'c', label: 'New issue', group: 'global', run: runC }]);
    const onOpenPalette = vi.fn();
    const user = userEvent.setup();
    render(
      <ShortcutProvider isMac={false} onOpenPalette={onOpenPalette}>
        <label>
          Name
          <input />
        </label>
      </ShortcutProvider>,
    );
    await user.click(screen.getByRole('textbox'));
    await user.keyboard('c');
    expect(runC).not.toHaveBeenCalled();
    await user.keyboard('{Control>}k{/Control}');
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
  });

  it('裸 ? 打开帮助层;输入框内 ? 不触发', () => {
    const onOpenHelp = vi.fn();
    render(
      <ShortcutProvider isMac={false} onOpenHelp={onOpenHelp}>
        <input aria-label="field" />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: '?', shiftKey: true });
    expect(onOpenHelp).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: '?', shiftKey: true });
    expect(onOpenHelp).toHaveBeenCalledTimes(1);
  });

  it('注册的全局快捷键 c / / 按 combo 路由', () => {
    const runC = vi.fn();
    const runSlash = vi.fn();
    registerShortcuts([
      { id: 'new', combo: 'c', label: 'New issue', group: 'global', run: runC },
      { id: 'search', combo: '/', label: 'Focus search', group: 'global', run: runSlash },
    ]);
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'c' });
    expect(runC).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: '/' });
    expect(runSlash).toHaveBeenCalledTimes(1);
  });

  it('未注册的裸键不触发任何动作', () => {
    const runC = vi.fn();
    registerShortcuts([{ id: 'new', combo: 'c', label: 'New', group: 'global', run: runC }]);
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'x' });
    expect(runC).not.toHaveBeenCalled();
  });

  it('上下文分组过滤:非 global 定义仅在 activeContexts 命中时触发', () => {
    const runIssue = vi.fn();
    registerShortcuts([{ id: 'edit', combo: 'e', label: 'Edit', group: 'issue', run: runIssue }]);
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'e' });
    expect(runIssue).not.toHaveBeenCalled();
    act(() => useShortcutRegistry.getState().setContexts(['issue']));
    fireEvent.keyDown(window, { key: 'e' });
    expect(runIssue).toHaveBeenCalledTimes(1);
  });

  it('序列键 G→I 在 1s 窗口内触发 g i(注入时钟)', () => {
    const runInbox = vi.fn();
    registerShortcuts([{ id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox }]);
    render(
      <ShortcutProvider isMac={false} now={() => clock.now}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'g' });
    clock.now += 500;
    fireEvent.keyDown(window, { key: 'i' });
    expect(runInbox).toHaveBeenCalledTimes(1);
  });

  it('序列键超出窗口失效(默认窗口常量 SEQUENCE_WINDOW_MS)', () => {
    expect(SEQUENCE_WINDOW_MS).toBe(1000);
    const runInbox = vi.fn();
    registerShortcuts([{ id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox }]);
    render(
      <ShortcutProvider isMac={false} now={() => clock.now}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'g' });
    clock.now += SEQUENCE_WINDOW_MS + 1;
    fireEvent.keyDown(window, { key: 'i' });
    expect(runInbox).not.toHaveBeenCalled();
  });

  it('序列键第二键不匹配时不触发', () => {
    const runInbox = vi.fn();
    registerShortcuts([{ id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox }]);
    render(
      <ShortcutProvider isMac={false} now={() => clock.now}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'g' });
    fireEvent.keyDown(window, { key: 'x' });
    expect(runInbox).not.toHaveBeenCalled();
  });

  it('序列键同样受上下文分组约束(g b 属 board)', () => {
    const runBoard = vi.fn();
    registerShortcuts([{ id: 'board', combo: 'g b', label: 'Board', group: 'board', run: runBoard }]);
    render(
      <ShortcutProvider isMac={false} now={() => clock.now}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'g' });
    fireEvent.keyDown(window, { key: 'b' });
    expect(runBoard).not.toHaveBeenCalled();
    act(() => useShortcutRegistry.getState().setContexts(['board']));
    fireEvent.keyDown(window, { key: 'g' });
    fireEvent.keyDown(window, { key: 'b' });
    expect(runBoard).toHaveBeenCalledTimes(1);
  });

  it('输入框内的 g 不进入序列待决态', () => {
    const runInbox = vi.fn();
    registerShortcuts([{ id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox }]);
    render(
      <ShortcutProvider isMac={false} now={() => clock.now}>
        <input aria-label="field" />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'g' });
    fireEvent.keyDown(window, { key: 'i' });
    expect(runInbox).not.toHaveBeenCalled();
  });

  it('其他 mod 组合(mod+k 之外)按 combo 路由注册定义', () => {
    const runJump = vi.fn();
    registerShortcuts([{ id: 'jump', combo: 'mod+j', label: 'Jump', group: 'global', run: runJump }]);
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'j', ctrlKey: true });
    expect(runJump).toHaveBeenCalledTimes(1);
  });

  it('alt 组合按 combo 路由,且终结序列待决态', () => {
    const runAltX = vi.fn();
    const runInbox = vi.fn();
    registerShortcuts([
      { id: 'altx', combo: 'alt+x', label: 'Alt X', group: 'global', run: runAltX },
      { id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox },
    ]);
    render(
      <ShortcutProvider isMac={false} now={() => clock.now}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'x', altKey: true });
    expect(runAltX).toHaveBeenCalledTimes(1);
    // g 进入待决 → mod 组合终结待决态 → 随后的 i 不再命中序列
    fireEvent.keyDown(window, { key: 'g' });
    fireEvent.keyDown(window, { key: 'j', ctrlKey: true });
    fireEvent.keyDown(window, { key: 'i' });
    expect(runInbox).not.toHaveBeenCalled();
  });

  it('space/escape 等键名归一化后参与 combo 匹配', () => {
    const runSpace = vi.fn();
    const runEsc = vi.fn();
    registerShortcuts([
      { id: 'space', combo: 'space', label: 'Space', group: 'global', run: runSpace },
      { id: 'esc', combo: 'esc', label: 'Esc', group: 'global', run: runEsc },
    ]);
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: ' ' });
    expect(runSpace).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(runEsc).toHaveBeenCalledTimes(1);
  });

  it('纯修饰键按下被忽略', () => {
    const onOpenHelp = vi.fn();
    render(
      <ShortcutProvider isMac={false} onOpenHelp={onOpenHelp}>
        <div />
      </ShortcutProvider>,
    );
    for (const key of ['Control', 'Meta', 'Shift', 'Alt']) {
      fireEvent.keyDown(window, { key });
    }
    expect(onOpenHelp).not.toHaveBeenCalled();
  });

  it('卸载后移除 window 监听器', () => {
    const runC = vi.fn();
    registerShortcuts([{ id: 'new', combo: 'c', label: 'New', group: 'global', run: runC }]);
    const { unmount } = render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );
    unmount();
    fireEvent.keyDown(window, { key: 'c' });
    expect(runC).not.toHaveBeenCalled();
  });
});
