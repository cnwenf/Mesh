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
  vi.useRealTimers();
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

  it('原生与 ARIA 交互控件的 Enter/Space 保留给控件，不触发页面裸键', () => {
    const runEnter = vi.fn();
    const runSpace = vi.fn();
    registerShortcuts([
      { id: 'open', combo: 'enter', label: 'Open selected', group: 'global', run: runEnter },
      { id: 'toggle', combo: 'space', label: 'Toggle selected', group: 'global', run: runSpace },
    ]);
    render(
      <ShortcutProvider isMac={false}>
        <button type="button">Native button</button>
        <a href="/target">Native link</a>
        <details>
          <summary>Native summary</summary>
          Details
        </details>
        <div role="button" tabIndex={0}>
          Custom button
        </div>
        <div tabIndex={0}>Plain focus target</div>
      </ShortcutProvider>,
    );

    for (const element of [
      screen.getByRole('button', { name: 'Native button' }),
      screen.getByRole('link', { name: 'Native link' }),
      screen.getByText('Native summary'),
      screen.getByRole('button', { name: 'Custom button' }),
    ]) {
      fireEvent.keyDown(element, { key: 'Enter' });
      fireEvent.keyDown(element, { key: ' ' });
    }
    expect(runEnter).not.toHaveBeenCalled();
    expect(runSpace).not.toHaveBeenCalled();

    fireEvent.keyDown(screen.getByText('Plain focus target'), { key: 'Enter' });
    fireEvent.keyDown(screen.getByText('Plain focus target'), { key: ' ' });
    expect(runEnter).toHaveBeenCalledTimes(1);
    expect(runSpace).toHaveBeenCalledTimes(1);
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

  it("'/' 命中聚焦搜索快捷键时阻止浏览器默认行为", () => {
    const runSlash = vi.fn();
    registerShortcuts([
      { id: 'search', combo: '/', label: 'Focus search', group: 'global', run: runSlash },
    ]);
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );

    const event = new KeyboardEvent('keydown', { key: '/', bubbles: true, cancelable: true });
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(runSlash).toHaveBeenCalledTimes(1);
  });

  it('IME 组合态全局豁免面板、帮助、裸键、序列键与 Enter', () => {
    const onOpenPalette = vi.fn();
    const onOpenHelp = vi.fn();
    const runC = vi.fn();
    const runInbox = vi.fn();
    const runEnter = vi.fn();
    registerShortcuts([
      { id: 'new', combo: 'c', label: 'New issue', group: 'global', run: runC },
      { id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox },
      { id: 'send', combo: 'enter', label: 'Send', group: 'global', run: runEnter },
    ]);
    render(
      <ShortcutProvider
        isMac={false}
        now={() => clock.now}
        onOpenPalette={onOpenPalette}
        onOpenHelp={onOpenHelp}
      >
        <div />
      </ShortcutProvider>,
    );

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true, isComposing: true });
    fireEvent.keyDown(window, { key: '?', shiftKey: true, isComposing: true });
    fireEvent.keyDown(window, { key: 'c', isComposing: true });
    fireEvent.keyDown(window, { key: 'Enter', isComposing: true });
    // 组合态的首键不得建立待决序列。
    fireEvent.keyDown(window, { key: 'g', isComposing: true });
    fireEvent.keyDown(window, { key: 'i' });

    expect(onOpenPalette).not.toHaveBeenCalled();
    expect(onOpenHelp).not.toHaveBeenCalled();
    expect(runC).not.toHaveBeenCalled();
    expect(runEnter).not.toHaveBeenCalled();
    expect(runInbox).not.toHaveBeenCalled();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    // 已有待决序列时，组合态第二键同样不分发、不消费待决态。
    fireEvent.keyDown(window, { key: 'g' });
    fireEvent.keyDown(window, { key: 'i', isComposing: true });
    expect(runInbox).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('G —');

    fireEvent.keyDown(window, { key: 'i' });
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(runInbox).toHaveBeenCalledTimes(1);
    expect(runEnter).toHaveBeenCalledTimes(1);
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
    registerShortcuts([
      { id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox },
    ]);
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

  it('序列首键显示可访问的 G — 待决状态，超时后自动清除', () => {
    vi.useFakeTimers();
    const runInbox = vi.fn();
    registerShortcuts([
      { id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox },
    ]);
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );

    fireEvent.keyDown(window, { key: 'g' });
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('G —');
    expect(status).toHaveAttribute('aria-live', 'polite');

    act(() => vi.advanceTimersByTime(SEQUENCE_WINDOW_MS));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'i' });
    expect(runInbox).not.toHaveBeenCalled();
  });

  it('Esc 清除序列待决状态且后续第二键不触发', () => {
    const runInbox = vi.fn();
    registerShortcuts([
      { id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox },
    ]);
    render(
      <ShortcutProvider isMac={false}>
        <input aria-label="field" />
      </ShortcutProvider>,
    );

    fireEvent.keyDown(window, { key: 'g' });
    expect(screen.getByRole('status')).toHaveTextContent('G —');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'i' });
    expect(runInbox).not.toHaveBeenCalled();
  });

  it('序列键超出窗口失效(默认窗口常量 SEQUENCE_WINDOW_MS)', () => {
    expect(SEQUENCE_WINDOW_MS).toBe(1000);
    const runInbox = vi.fn();
    registerShortcuts([
      { id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox },
    ]);
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
    registerShortcuts([
      { id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox },
    ]);
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
    registerShortcuts([
      { id: 'board', combo: 'g b', label: 'Board', group: 'board', run: runBoard },
    ]);
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
    registerShortcuts([
      { id: 'inbox', combo: 'g i', label: 'Inbox', group: 'global', run: runInbox },
    ]);
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
    registerShortcuts([
      { id: 'jump', combo: 'mod+j', label: 'Jump', group: 'global', run: runJump },
    ]);
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'j', ctrlKey: true });
    expect(runJump).toHaveBeenCalledTimes(1);
  });

  it('页面上下文快捷键覆盖同 combo 的 global 快捷键(§4.3.1 特异性仲裁)', () => {
    const runGlobal = vi.fn();
    const runBoard = vi.fn();
    registerShortcuts([
      { id: 'global-create', combo: 'c', label: 'New issue', group: 'global', run: runGlobal },
      { id: 'board-create', combo: 'c', label: 'New card here', group: 'board', run: runBoard },
    ]);
    act(() => useShortcutRegistry.getState().setContexts(['board']));
    render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );

    fireEvent.keyDown(window, { key: 'c' });

    expect(runBoard).toHaveBeenCalledTimes(1);
    expect(runGlobal).not.toHaveBeenCalled();
  });

  it('组件已处理并 preventDefault 的按键不会被 window 快捷键重复提交', () => {
    const runSubmit = vi.fn();
    registerShortcuts([
      {
        id: 'issue-submit',
        combo: 'mod+enter',
        label: 'Submit comment',
        group: 'issue',
        run: runSubmit,
      },
    ]);
    act(() => useShortcutRegistry.getState().setContexts(['issue']));
    render(
      <ShortcutProvider isMac={false}>
        <textarea aria-label="composer" onKeyDown={(event) => event.preventDefault()} />
      </ShortcutProvider>,
    );

    fireEvent.keyDown(screen.getByRole('textbox', { name: 'composer' }), {
      key: 'Enter',
      ctrlKey: true,
    });

    expect(runSubmit).not.toHaveBeenCalled();
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

  it('卸载后移除 window 监听器并清理序列超时器', () => {
    vi.useFakeTimers();
    const runC = vi.fn();
    registerShortcuts([{ id: 'new', combo: 'c', label: 'New', group: 'global', run: runC }]);
    const { unmount } = render(
      <ShortcutProvider isMac={false}>
        <div />
      </ShortcutProvider>,
    );
    fireEvent.keyDown(window, { key: 'g' });
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    fireEvent.keyDown(window, { key: 'c' });
    expect(runC).not.toHaveBeenCalled();
  });
});
