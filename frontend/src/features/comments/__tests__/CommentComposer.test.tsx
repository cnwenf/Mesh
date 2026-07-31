/**
 * CommentComposer 组件测试(comment-inbox.md §4.1/§4.3,README §6.9):
 * @ 补全 agent 副作用提示措辞、trigger preview、显式抑制开关、Cmd+Enter 提交、
 * 草稿本地暂存、乐观提交失败重试。
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

function renderComposer(onSubmit = vi.fn().mockResolvedValue(undefined)): void {
  renderWithProviders(
    <CommentComposer draftKey="iss-test" candidates={CANDIDATES} onSubmit={onSubmit} />,
  );
}

function typeInto(value: string): void {
  const input = screen.getByTestId('composer-input') as HTMLTextAreaElement;
  fireEvent.change(input, { target: { value } });
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('CommentComposer', () => {
  it('shows the agent will-run badge (sparkle icon + wording) in the autocomplete', () => {
    renderComposer();
    typeInto('@code');
    const hint = screen.getByTestId('mention-agent-hint');
    // 文案键(§9.5.2「发布后将触发一次运行」),locale=en 渲染实际英文
    expect(hint.textContent).toContain('Will trigger a run after posting');
    // sparkle 图标(非仅颜色信号)
    expect(hint.querySelector('.mesh-comments__mention-run-icon')).not.toBeNull();
    expect(screen.getByTestId('mention-item-mem-2')).toBeTruthy();
  });

  it('inserts a mention chip on selection and shows the trigger preview', () => {
    renderComposer();
    typeInto('@code');
    fireEvent.mouseDown(screen.getByTestId('mention-item-mem-2'));
    const input = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    expect(input.value).toContain('mention://member/mem-2');
    expect(screen.getByTestId('trigger-preview').textContent).toContain('code-reviewer');
    expect(screen.getByTestId('trigger-hint')).toBeTruthy();
  });

  it('hides the trigger preview when the suppress switch is on', () => {
    renderComposer();
    typeInto('@code');
    fireEvent.mouseDown(screen.getByTestId('mention-item-mem-2'));
    fireEvent.click(screen.getByTestId('composer-suppress'));
    expect(screen.queryByTestId('trigger-preview')).toBeNull();
  });

  it('submits with suppress_triggers derived from the switch (Cmd+Enter)', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderComposer(onSubmit);
    typeInto('@code');
    fireEvent.mouseDown(screen.getByTestId('mention-item-mem-2'));
    fireEvent.click(screen.getByTestId('composer-suppress'));
    const input = screen.getByTestId('composer-input');
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][1]).toEqual({ suppressTriggers: true });
  });

  it('clears the draft after a successful submit', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderComposer(onSubmit);
    typeInto('a comment');
    fireEvent.click(screen.getByTestId('composer-submit'));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(window.localStorage.getItem('mesh.comments.draft.iss-test')).toBeNull();
  });

  it('persists the draft to localStorage while typing', () => {
    renderComposer();
    typeInto('draft body');
    expect(window.localStorage.getItem('mesh.comments.draft.iss-test')).toBe('draft body');
  });

  it('keeps the draft and offers retry on submit failure', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('boom'));
    renderComposer(onSubmit);
    typeInto('will fail');
    fireEvent.click(screen.getByTestId('composer-submit'));
    await screen.findByTestId('composer-error');
    expect(window.localStorage.getItem('mesh.comments.draft.iss-test')).toBe('will fail');
    // retry re-invokes submit
    onSubmit.mockResolvedValue(undefined);
    fireEvent.click(screen.getByTestId('composer-retry'));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
  });

  it('preserves body AND mentions on failure and shows recoverable wording (§9.5.4)', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('boom'));
    renderComposer(onSubmit);
    // 输入正文并插入一个 agent 提及
    typeInto('please review ');
    typeInto('please review @code');
    fireEvent.mouseDown(screen.getByTestId('mention-item-mem-2'));
    const input = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    expect(input.value).toContain('mention://member/mem-2');
    fireEvent.click(screen.getByTestId('composer-submit'));
    const error = await screen.findByTestId('composer-error');
    // 四部分错误文案键(发生了什么/保留什么/怎么办)
    expect(error.textContent).toContain("Couldn't post the comment");
    // 正文 + 提及均保留在输入框与草稿中
    const after = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    expect(after.value).toContain('mention://member/mem-2');
    expect(window.localStorage.getItem('mesh.comments.draft.iss-test')).toContain('mention://member/mem-2');
    // retry 按钮可达
    expect(screen.getByTestId('composer-retry')).toBeTruthy();
  });

  it('toggles the markdown preview', () => {
    renderComposer();
    typeInto('**bold**');
    fireEvent.click(screen.getByTestId('composer-preview-toggle'));
    expect(screen.getByTestId('composer-preview').innerHTML).toContain('<strong>bold</strong>');
  });

  it('disables submit when the body is empty', () => {
    renderComposer();
    expect((screen.getByTestId('composer-submit') as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the draft autosave indicator: saving → saved (§9.5.1)', () => {
    vi.useFakeTimers();
    try {
      renderComposer();
      // 初始无提示
      expect(screen.queryByTestId('draft-status')).toBeNull();
      fireEvent.change(screen.getByTestId('composer-input'), { target: { value: 'typing' } });
      // dirty 阶段即显示「保存中」
      expect(screen.getByTestId('draft-status').textContent).toContain('Saving draft…');
      // 防抖窗口(600ms)到期 → saving,过渡(200ms)后 → saved
      act(() => {
        vi.advanceTimersByTime(600);
      });
      expect(screen.getByTestId('draft-status').textContent).toContain('Saving draft…');
      act(() => {
        vi.advanceTimersByTime(200);
      });
      expect(screen.getByTestId('draft-status').textContent).toContain('Draft saved ·');
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows a one-time "draft restored" hint when a saved draft loads (§9.5.1)', () => {
    window.localStorage.setItem('mesh.comments.draft.iss-test', 'earlier draft');
    renderComposer();
    expect(screen.getByTestId('draft-restored')).toBeTruthy();
    // 用户一编辑,提示消失
    fireEvent.change(screen.getByTestId('composer-input'), { target: { value: 'new text' } });
    expect(screen.queryByTestId('draft-restored')).toBeNull();
  });
});
