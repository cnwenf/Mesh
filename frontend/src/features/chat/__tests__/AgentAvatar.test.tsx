/**
 * AgentAvatar 测试(chat-session.md §4.1):有 avatar_url 渲染 <img>;null 回退首字母占位;
 * 加载失败(onError)回退占位;空名称回退 '?';avatar_url 变更重置失败标记。
 * 组件不依赖任何 Context,直接 render(无需 Provider 栈)。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AgentAvatar } from '../AgentAvatar';
import type { ChatAgentRef } from '../types';

function agent(overrides: Partial<ChatAgentRef> = {}): ChatAgentRef {
  return { id: 'a-1', name: 'Builder', avatar_url: null, ...overrides };
}

describe('AgentAvatar(§4.1)', () => {
  it('avatar_url 存在时渲染 img', () => {
    render(
      <AgentAvatar
        agent={agent({ avatar_url: 'http://x/a.png' })}
        testId="chat-session-avatar-s1"
      />,
    );
    const img = screen.getByTestId('chat-session-avatar-s1');
    expect(img.tagName).toBe('IMG');
    expect(img).toHaveAttribute('src', 'http://x/a.png');
    expect(img).toHaveAttribute('alt', 'Builder');
  });

  it('avatar_url 为 null 时回退首字母占位', () => {
    render(<AgentAvatar agent={agent()} testId="av" />);
    const fallback = screen.getByTestId('av');
    expect(fallback.tagName).toBe('SPAN');
    expect(fallback).toHaveTextContent('B');
  });

  it('空名称回退 "?"', () => {
    render(<AgentAvatar agent={agent({ name: '   ' })} testId="av" />);
    expect(screen.getByTestId('av')).toHaveTextContent('?');
  });

  it('img 加载失败(onError)回退首字母占位', () => {
    render(<AgentAvatar agent={agent({ avatar_url: 'http://x/broken.png' })} testId="av" />);
    fireEvent.error(screen.getByTestId('av'));
    expect(screen.getByTestId('av').tagName).toBe('SPAN');
    expect(screen.getByTestId('av')).toHaveTextContent('B');
  });

  it('avatar_url 变更重置失败标记(新头像可再次尝试)', () => {
    const { rerender } = render(
      <AgentAvatar agent={agent({ avatar_url: 'http://x/old.png' })} testId="av" />,
    );
    fireEvent.error(screen.getByTestId('av'));
    expect(screen.getByTestId('av').tagName).toBe('SPAN');
    // 同一实例换头像 → effect 重置 broken,重新渲染 img
    rerender(<AgentAvatar agent={agent({ avatar_url: 'http://x/new.png' })} testId="av" />);
    expect(screen.getByTestId('av').tagName).toBe('IMG');
    expect(screen.getByTestId('av')).toHaveAttribute('src', 'http://x/new.png');
  });
});
