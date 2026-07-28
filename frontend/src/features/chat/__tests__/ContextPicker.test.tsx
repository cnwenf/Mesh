/**
 * ContextPicker 测试(chat-session.md §4.2):issue 搜索 + 选择(onPick id)、
 * 清除(onPick null)、取消(onClose)、空查询不搜索、搜索失败清空。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { ContextPicker } from '../ContextPicker';

const issue = { id: 'iss-1', identifier: 'WEB-1', title: 'Bug' };

let client: MeshApiClient;

afterEach(() => vi.unstubAllGlobals());

function stubIssues(response: Response, calls: string[]): void {
  const fetchImpl = (async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return response;
  }) as typeof fetch;
  vi.stubGlobal('fetch', fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
}

function renderPicker(props: Partial<React.ComponentProps<typeof ContextPicker>> = {}) {
  return renderWithProviders(
    <ContextPicker
      open
      client={client}
      workspaceId="ws-1"
      onClose={vi.fn()}
      onPick={vi.fn()}
      {...props}
    />,
  );
}

describe('ContextPicker(§4.2)', () => {
  it('搜索 issue → 点选触发 onPick(issue.id)', async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    stubIssues(fakeResponse({ body: { data: [issue], next_cursor: null } }), calls);
    const onPick = vi.fn();
    renderPicker({ onPick });
    await user.type(screen.getByTestId('chat-context-picker-search'), 'Bug');
    await screen.findByTestId('chat-context-picker-results');
    await user.click(screen.getByTestId('chat-context-picker-option-iss-1'));
    expect(onPick).toHaveBeenCalledWith('iss-1');
  });

  it('清除按钮触发 onPick(null)', async () => {
    const user = userEvent.setup();
    stubIssues(fakeResponse({ body: { data: [], next_cursor: null } }), []);
    const onPick = vi.fn();
    renderPicker({ onPick });
    await user.click(screen.getByTestId('chat-context-picker-clear'));
    expect(onPick).toHaveBeenCalledWith(null);
  });

  it('取消按钮触发 onClose', async () => {
    const user = userEvent.setup();
    stubIssues(fakeResponse({ body: { data: [], next_cursor: null } }), []);
    const onClose = vi.fn();
    renderPicker({ onClose });
    await user.click(screen.getByTestId('chat-context-picker-cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('空查询不触发搜索并清空结果', async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    stubIssues(fakeResponse({ body: { data: [issue], next_cursor: null } }), calls);
    renderPicker();
    const input = screen.getByTestId('chat-context-picker-search');
    await user.type(input, ' ');
    await user.clear(input);
    expect(screen.queryByTestId('chat-context-picker-results')).toBeNull();
    expect(calls.length).toBe(0);
  });

  it('搜索失败清空结果(不崩溃)', async () => {
    const user = userEvent.setup();
    stubIssues(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
      [],
    );
    renderPicker();
    await user.type(screen.getByTestId('chat-context-picker-search'), 'Bug');
    await waitFor(() => expect(screen.queryByTestId('chat-context-picker-results')).toBeNull());
  });
});
