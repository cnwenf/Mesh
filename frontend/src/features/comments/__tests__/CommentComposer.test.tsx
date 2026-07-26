/**
 * CommentComposer 组件测试(comment-inbox.md §4.1/§4.3,README §6.9):
 * @ 补全 agent 副作用提示措辞、trigger preview、显式抑制开关、Cmd+Enter 提交、
 * 草稿本地暂存、乐观提交失败重试。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
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
  it('shows the agent side-effect hint with the exact wording in the autocomplete', () => {
    renderComposer();
    typeInto('@code');
    expect(screen.getByTestId('mention-agent-hint').textContent).toBe('Will trigger a run after posting');
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
});
