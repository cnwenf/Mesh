/**
 * 四层分发链单元测试(§4.3.1 评审 P2):输入控件 > 最上层弹层 > 页面上下文组
 * > 全局组——同键在四层各有 handler 时按键只执行最高层那一个;各层移除后
 * 回落下一层正确。另覆盖 IME 豁免(评审 P1)与跨上下文仲裁。
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Dialog } from '../../design/components/Dialog';
import { ChatComposer } from '../../features/chat/ChatComposer';
import { renderWithProviders } from '../../test-utils/render';
import { pushOverlay } from '../overlayStack';
import { useShortcutRegistry } from '../registry';
import { ShortcutProvider } from '../ShortcutProvider';

beforeEach(() => {
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  document.body.innerHTML = '';
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function register(defs: Array<{ id: string; combo: string; group: 'global' | 'board' | 'issue' | 'chat'; run: () => void }>): void {
  act(() => {
    useShortcutRegistry.getState().registerShortcuts(
      defs.map((def) => ({ ...def, label: def.id })),
    );
  });
}

function setContexts(contexts: Array<'board' | 'issue' | 'chat'>): void {
  act(() => useShortcutRegistry.getState().setContexts(contexts));
}

describe('四层分发链(§4.3.1 评审 P2)', () => {
  it('页面上下文组 > 全局组:同键取最具体 active context', () => {
    const globalSpy = vi.fn();
    const boardSpy = vi.fn();
    register([
      { id: 'g', combo: 'x', group: 'global', run: globalSpy },
      { id: 'b', combo: 'x', group: 'board', run: boardSpy },
    ]);
    render(<ShortcutProvider isMac={false}><div /></ShortcutProvider>);

    setContexts(['board']);
    fireEvent.keyDown(window, { key: 'x' });
    expect(boardSpy).toHaveBeenCalledTimes(1);
    expect(globalSpy).not.toHaveBeenCalled();
  });

  it('issue > board > global 特异性序(多上下文同时激活)', () => {
    const globalSpy = vi.fn();
    const boardSpy = vi.fn();
    const issueSpy = vi.fn();
    register([
      { id: 'g', combo: 's', group: 'global', run: globalSpy },
      { id: 'b', combo: 's', group: 'board', run: boardSpy },
      { id: 'i', combo: 's', group: 'issue', run: issueSpy },
    ]);
    render(<ShortcutProvider isMac={false}><div /></ShortcutProvider>);

    setContexts(['board', 'issue']);
    fireEvent.keyDown(window, { key: 's' });
    expect(issueSpy).toHaveBeenCalledTimes(1);
    expect(boardSpy).not.toHaveBeenCalled();
    expect(globalSpy).not.toHaveBeenCalled();

    // issue 上下文移除 → 回落 board。
    setContexts(['board']);
    fireEvent.keyDown(window, { key: 's' });
    expect(boardSpy).toHaveBeenCalledTimes(1);
  });

  it('chat 独占:chat 激活时 board/global 同键被屏蔽', () => {
    const boardSpy = vi.fn();
    const chatSpy = vi.fn();
    register([
      { id: 'b', combo: 'x', group: 'board', run: boardSpy },
      { id: 'c', combo: 'x', group: 'chat', run: chatSpy },
    ]);
    render(<ShortcutProvider isMac={false}><div /></ShortcutProvider>);

    setContexts(['chat']);
    fireEvent.keyDown(window, { key: 'x' });
    expect(chatSpy).toHaveBeenCalledTimes(1);
    expect(boardSpy).not.toHaveBeenCalled();
  });

  it('输入控件层最高:聚焦输入框时裸键不进入后续各层', () => {
    const boardSpy = vi.fn();
    const globalSpy = vi.fn();
    register([
      { id: 'g', combo: 'x', group: 'global', run: globalSpy },
      { id: 'b', combo: 'x', group: 'board', run: boardSpy },
    ]);
    render(
      <ShortcutProvider isMac={false}>
        <input aria-label="field" />
      </ShortcutProvider>,
    );
    setContexts(['board']);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'x' });
    expect(boardSpy).not.toHaveBeenCalled();
    expect(globalSpy).not.toHaveBeenCalled();
  });

  it('弹层层:overlay 打开时背景上下文组与全局组裸键全屏蔽,仅弹层自身键绑定生效', () => {
    const globalSpy = vi.fn();
    const overlayKeySpy = vi.fn();
    register([{ id: 'g', combo: 'x', group: 'global', run: globalSpy }]);
    render(<ShortcutProvider isMac={false}><div /></ShortcutProvider>);

    let removeOverlay: (() => void) | undefined;
    act(() => {
      removeOverlay = pushOverlay({ id: 'palette', returnFocusTo: null, onKeyDown: overlayKeySpy });
    });
    fireEvent.keyDown(window, { key: 'x' });
    expect(overlayKeySpy).toHaveBeenCalledTimes(1);
    expect(globalSpy).not.toHaveBeenCalled();

    // 弹层移除后回落全局组。
    act(() => removeOverlay?.());
    fireEvent.keyDown(window, { key: 'x' });
    expect(globalSpy).toHaveBeenCalledTimes(1);
  });

  it('弹层 Esc:无输入焦点时关闭顶层并归还焦点', () => {
    document.body.innerHTML = '<main><button type="button" data-testid="trigger">T</button></main>';
    const trigger = document.querySelector<HTMLElement>('[data-testid="trigger"]');
    trigger?.focus();
    render(<ShortcutProvider isMac={false}><div /></ShortcutProvider>);
    act(() => {
      pushOverlay({ id: 'ov', returnFocusTo: trigger });
    });
    fireEvent.keyDown(window, { key: 'Escape' });
    // 焦点回到触发元素(非 body)。
    expect(document.activeElement).toBe(trigger);
  });
});

describe('IME 组合输入豁免(评审 P1)', () => {
  it('isComposing 为真时裸键不触发(中文输入 c 不弹新建)', () => {
    const runC = vi.fn();
    register([{ id: 'new', combo: 'c', group: 'global', run: runC }]);
    render(<ShortcutProvider isMac={false}><div /></ShortcutProvider>);
    fireEvent.keyDown(window, { key: 'c', isComposing: true });
    expect(runC).not.toHaveBeenCalled();
    // 组合结束后恢复。
    fireEvent.keyDown(window, { key: 'c' });
    expect(runC).toHaveBeenCalledTimes(1);
  });

  it('compositionstart → compositionend 期间一切快捷键不触发(含序列键)', () => {
    const runC = vi.fn();
    const runInbox = vi.fn();
    register([
      { id: 'new', combo: 'c', group: 'global', run: runC },
      { id: 'inbox', combo: 'g i', group: 'global', run: runInbox },
    ]);
    render(<ShortcutProvider isMac={false}><div /></ShortcutProvider>);

    fireEvent.compositionStart(window);
    fireEvent.keyDown(window, { key: 'c' });
    fireEvent.keyDown(window, { key: 'g' });
    fireEvent.keyDown(window, { key: 'i' });
    expect(runC).not.toHaveBeenCalled();
    expect(runInbox).not.toHaveBeenCalled();

    fireEvent.compositionEnd(window);
    fireEvent.keyDown(window, { key: 'c' });
    expect(runC).toHaveBeenCalledTimes(1);
  });

  it('聊天 Enter 发送处理器同样检查 isComposing(候选词确认不是发送)', () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(
      <ChatComposer onSend={onSend} quoteMessage={null} onClearQuote={() => undefined} />,
    );
    const textarea = screen.getByTestId('chat-composer-input');
    // 先有内容,canSend 为真。
    fireEvent.change(textarea, { target: { value: '你好' } });
    fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
    // 组合结束后的 Enter 发送(Shift+Enter 仍为换行,不发送)。
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledTimes(1);
  });
});

describe('Dialog Esc 分层(§4.5:弹层内输入框获焦首个 Esc 仅失焦)', () => {
  it('输入框获焦时首个 Esc 仅失焦不关弹层;第二个 Esc 关闭', () => {
    const onClose = vi.fn();
    render(
      <Dialog open onClose={onClose} title="D" closeLabel="close">
        <input aria-label="dialog-field" />
      </Dialog>,
    );
    const input = screen.getByRole('textbox');
    act(() => input.focus());
    expect(document.activeElement).toBe(input);

    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    expect(document.activeElement).not.toBe(input);

    fireEvent.keyDown(document.querySelector('[role="dialog"]') as Element, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
