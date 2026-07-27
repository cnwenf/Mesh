/**
 * CommentComposer 键盘交互与提交分支补齐(coverage fill):
 * 提及候选打开时的 ArrowDown/ArrowUp/Enter/Escape 导航、Ctrl+Enter 提交、
 * 提交进行中再次提交的守卫。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { CommentComposer } from '../CommentComposer';
import type { MentionCandidate } from '../mentions';

const CANDIDATES: MentionCandidate[] = [
  { id: 'mem-1', name: 'Alice', member_type: 'human' },
  { id: 'mem-2', name: 'code-reviewer', member_type: 'agent' },
];

function renderComposer(onSubmit: () => Promise<void> = () => Promise.resolve()): void {
  renderWithProviders(
    <CommentComposer draftKey="iss-cov" candidates={CANDIDATES} onSubmit={onSubmit} />,
  );
}

function typeInto(value: string): void {
  fireEvent.change(screen.getByTestId('composer-input'), { target: { value } });
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('CommentComposer keyboard navigation', () => {
  it('navigates the open mention list with ArrowDown/ArrowUp and selects with Enter', () => {
    renderComposer();
    typeInto('@code');
    const input = screen.getByTestId('composer-input');
    // 候选已打开(filtered.length>0),进入键盘导航分支
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    fireEvent.keyDown(input, { key: 'Enter' });
    // Enter 选中 code-reviewer 并关闭补全,值中插入 mention 链接
    const after = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    expect(after.value).toContain('mention://member/mem-2');
    expect(screen.queryByTestId('mention-item-mem-2')).toBeNull();
  });

  it('closes the mention list with Escape', () => {
    renderComposer();
    typeInto('@code');
    expect(screen.getByTestId('mention-item-mem-2')).toBeTruthy();
    fireEvent.keyDown(screen.getByTestId('composer-input'), { key: 'Escape' });
    expect(screen.queryByTestId('mention-item-mem-2')).toBeNull();
  });

  it('ignores navigation keys when the mention list is closed', () => {
    renderComposer();
    typeInto('plain text');
    const input = screen.getByTestId('composer-input');
    // mentionOpen=false → 这些键不触发导航,也不提交(无修饰键)
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect((input as HTMLTextAreaElement).value).toBe('plain text');
  });
});

describe('CommentComposer submit branches', () => {
  it('submits with Ctrl+Enter (no meta key)', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderComposer(onSubmit);
    typeInto('ctrl submit');
    fireEvent.keyDown(screen.getByTestId('composer-input'), { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][1]).toEqual({ suppressTriggers: false });
  });

  it('does not start a second submit while one is in flight', async () => {
    let resolveSubmit: () => void = () => undefined;
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveSubmit = resolve;
        }),
    );
    renderComposer(onSubmit);
    typeInto('guarded');
    const input = screen.getByTestId('composer-input');
    // 第一次提交进入 sending,等待挂起的 Promise
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    // 第二次提交被 submitState==='sending' 守卫拦下
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
    await act(async () => {
      resolveSubmit();
    });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});
