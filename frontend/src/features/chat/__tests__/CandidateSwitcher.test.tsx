/**
 * CandidateSwitcher 测试(chat-session.md §4.2 / §3.2):
 * ‹ › 仅切换本地查看索引(懒加载候选,不写库,无 /select);「使用此条」独立落库(select);
 * 已持久化选中候选呈现「已选用」标记(check 图标 + 文案)且禁用「使用此条」;端点禁用;失败 toast。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { CandidateSwitcher } from '../CandidateSwitcher';
import type { ChatMessage } from '../types';

function candidate(id: string, index: number): ChatMessage {
  return {
    id,
    session_id: 'sess-1',
    role: 'agent',
    content: `reply ${index}`,
    generation_id: null,
    generation_status: 'done',
    parent_id: 'p-1',
    selected_candidate: index === 0,
    quote_message_id: null,
    prompt_tokens: null,
    completion_tokens: null,
    error_message: null,
    started_at: null,
    finished_at: null,
    created_at: '2026-07-01T00:00:00Z',
    attachments: [],
    candidate_count: 3,
    candidate_index: index,
  };
}

/** 查看气泡渲染桩:暴露当前查看候选 id 供断言。 */
function renderCandidate(message: ChatMessage): React.JSX.Element {
  return <div data-testid="chat-candidate-viewing">{message.id}</div>;
}

const LIST = [candidate('m-0', 0), candidate('m-1', 1), candidate('m-2', 2)];

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  stub = stubFetch(
    fakeResponse({ body: { data: LIST, next_cursor: null } }),
    fakeResponse({ body: { data: { parent_id: 'p-1', selected_message_id: 'm-1' } } }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

function renderSwitcher(props: Partial<React.ComponentProps<typeof CandidateSwitcher>> = {}) {
  const selectedId = props.selectedId ?? 'm-0';
  const index = props.index ?? 0;
  return renderWithProviders(
    <CandidateSwitcher
      client={client}
      workspaceId="ws-1"
      sessionId="sess-1"
      parentId="p-1"
      selectedId={selectedId}
      index={index}
      count={3}
      selectedMessage={candidate(selectedId, index)}
      renderCandidate={renderCandidate}
      onSelected={vi.fn()}
      {...props}
    />,
  );
}

describe('CandidateSwitcher(§4.2 本地翻页 + 显式选用)', () => {
  it('初始渲染查看选中候选 + 位置 i/n + 「✓ 已选用」标记', () => {
    renderSwitcher();
    expect(screen.getByTestId('chat-candidate-viewing')).toHaveTextContent('m-0');
    expect(screen.getByTestId('chat-candidate-indicator').textContent).toContain('1');
    expect(screen.getByTestId('chat-candidate-indicator').textContent).toContain('3');
    expect(screen.getByTestId('chat-candidate-selected-mark')).toBeInTheDocument();
  });

  it('查看选中候选时禁用「使用此条」', () => {
    renderSwitcher();
    expect(screen.getByTestId('chat-candidate-use-m-0')).toBeDisabled();
  });

  it('起点处禁用上一页', () => {
    renderSwitcher({ selectedId: 'm-0', index: 0 });
    expect(screen.getByTestId('chat-candidate-prev-m-0')).toBeDisabled();
    expect(screen.getByTestId('chat-candidate-next-m-0')).not.toBeDisabled();
  });

  it('终点处禁用下一页(index 越界钳制)', () => {
    renderSwitcher({ selectedId: 'm-2', index: 2 });
    expect(screen.getByTestId('chat-candidate-next-m-2')).toBeDisabled();
    expect(screen.getByTestId('chat-candidate-prev-m-2')).not.toBeDisabled();
  });

  it('下一页:懒加载候选并仅切换本地查看索引(不调 select)', async () => {
    const user = userEvent.setup();
    const onSelected = vi.fn();
    renderSwitcher({ selectedId: 'm-0', index: 0, onSelected });
    await user.click(screen.getByTestId('chat-candidate-next-m-0'));
    // 查看索引移动到 m-1(本地,气泡随之切换)
    await waitFor(() =>
      expect(screen.getByTestId('chat-candidate-viewing')).toHaveTextContent('m-1'),
    );
    // 仅一次候选列表请求(parent_id),绝无 /select 写库
    expect(stub.calls.length).toBe(1);
    expect(stub.calls[0].url).toContain('parent_id=p-1');
    expect(stub.calls.some((c) => c.url.includes('/select'))).toBe(false);
    expect(onSelected).not.toHaveBeenCalled();
    // 翻离选中项后出现「使用此条」可用,选中标记消失
    expect(screen.getByTestId('chat-candidate-use-m-0')).toBeEnabled();
    expect(screen.queryByTestId('chat-candidate-selected-mark')).toBeNull();
  });

  it('「使用此条」:select 落库 + onSelected(当前查看候选)', async () => {
    const user = userEvent.setup();
    const onSelected = vi.fn();
    renderSwitcher({ selectedId: 'm-0', index: 0, onSelected });
    // 先翻到 m-1,再选用
    await user.click(screen.getByTestId('chat-candidate-next-m-0'));
    await waitFor(() =>
      expect(screen.getByTestId('chat-candidate-viewing')).toHaveTextContent('m-1'),
    );
    await user.click(screen.getByTestId('chat-candidate-use-m-0'));
    await waitFor(() => expect(onSelected).toHaveBeenCalledTimes(1));
    expect(onSelected).toHaveBeenCalledWith(expect.objectContaining({ id: 'm-1' }));
    // 首次为候选列表,二次为 select(路径为父消息 id)
    expect(stub.calls[0].url).toContain('parent_id=p-1');
    expect(stub.calls[1].url).toContain('/messages/p-1/select');
  });

  it('select 失败时 toast 且不调用 onSelected', async () => {
    vi.unstubAllGlobals();
    const failStub = stubFetch(
      fakeResponse({ body: { data: LIST, next_cursor: null } }),
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
    );
    vi.stubGlobal('fetch', failStub.fetchImpl);
    const user = userEvent.setup();
    const onSelected = vi.fn();
    renderSwitcher({ selectedId: 'm-0', index: 0, onSelected });
    await user.click(screen.getByTestId('chat-candidate-next-m-0'));
    await waitFor(() =>
      expect(screen.getByTestId('chat-candidate-viewing')).toHaveTextContent('m-1'),
    );
    await user.click(screen.getByTestId('chat-candidate-use-m-0'));
    await waitFor(() => expect(failStub.calls.length).toBe(2));
    expect(onSelected).not.toHaveBeenCalled();
  });

  it('二次翻页命中候选缓存(不再请求列表),仍不写库', async () => {
    const user = userEvent.setup();
    renderSwitcher({ selectedId: 'm-0', index: 0 });
    await user.click(screen.getByTestId('chat-candidate-next-m-0'));
    await waitFor(() =>
      expect(screen.getByTestId('chat-candidate-viewing')).toHaveTextContent('m-1'),
    );
    await user.click(screen.getByTestId('chat-candidate-next-m-0'));
    await waitFor(() =>
      expect(screen.getByTestId('chat-candidate-viewing')).toHaveTextContent('m-2'),
    );
    // 复用缓存:仅一次列表请求,无 select
    const listCalls = stub.calls.filter((c) => c.url.includes('parent_id=p-1'));
    expect(listCalls.length).toBe(1);
    expect(stub.calls.some((c) => c.url.includes('/select'))).toBe(false);
  });

  it('上一页(index>0)查看前一候选(本地)', async () => {
    const user = userEvent.setup();
    renderSwitcher({ selectedId: 'm-1', index: 1 });
    await user.click(screen.getByTestId('chat-candidate-prev-m-1'));
    await waitFor(() =>
      expect(screen.getByTestId('chat-candidate-viewing')).toHaveTextContent('m-0'),
    );
    expect(stub.calls.some((c) => c.url.includes('/select'))).toBe(false);
  });

  it('服务端候选数少于 count 时翻页钳制到实际末位', async () => {
    vi.unstubAllGlobals();
    const shortStub = stubFetch(
      fakeResponse({ body: { data: [candidate('m-0', 0)], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', shortStub.fetchImpl);
    const user = userEvent.setup();
    renderSwitcher({ selectedId: 'm-0', index: 0, count: 3 });
    await user.click(screen.getByTestId('chat-candidate-next-m-0'));
    // 实际仅 1 个候选 → 钳制回索引 0,仍查看 m-0
    await waitFor(() => expect(shortStub.calls.length).toBe(1));
    expect(screen.getByTestId('chat-candidate-viewing')).toHaveTextContent('m-0');
  });

  it('翻页时候选列表加载失败 → toast 且查看索引不变', async () => {
    vi.unstubAllGlobals();
    const failStub = stubFetch(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
    );
    vi.stubGlobal('fetch', failStub.fetchImpl);
    const user = userEvent.setup();
    renderSwitcher({ selectedId: 'm-0', index: 0 });
    await user.click(screen.getByTestId('chat-candidate-next-m-0'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
    // 加载失败:仍查看初始候选 m-0
    expect(screen.getByTestId('chat-candidate-viewing')).toHaveTextContent('m-0');
  });

  it('「使用此条」目标缺位(服务端候选少于初始索引)→ 安静返回不写库', async () => {
    vi.unstubAllGlobals();
    // 仅 1 个候选;初始 index=2 指向缺位目标。selectedMessage 与 selectedId 不同名以启用按钮。
    const shortStub = stubFetch(
      fakeResponse({ body: { data: [candidate('m-0', 0)], next_cursor: null } }),
      fakeResponse({ body: { data: { parent_id: 'p-1', selected_message_id: 'm-x' } } }),
    );
    vi.stubGlobal('fetch', shortStub.fetchImpl);
    const user = userEvent.setup();
    const onSelected = vi.fn();
    renderSwitcher({
      selectedId: 'm-2',
      index: 2,
      count: 3,
      selectedMessage: candidate('m-0', 0),
      onSelected,
    });
    // 查看项(m-0)非选中项(m-2)→「使用此条」可用
    expect(screen.getByTestId('chat-candidate-use-m-2')).toBeEnabled();
    await user.click(screen.getByTestId('chat-candidate-use-m-2'));
    await waitFor(() => expect(shortStub.calls.length).toBe(1));
    // 目标 list[2] 缺位 → 不 select、不回调
    expect(shortStub.calls.some((c) => c.url.includes('/select'))).toBe(false);
    expect(onSelected).not.toHaveBeenCalled();
  });
});
