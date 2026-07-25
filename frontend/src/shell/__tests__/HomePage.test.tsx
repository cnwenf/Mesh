/**
 * HomePage — 各演示区渲染与交互;realtime 区无上下文时提示,
 * 有上下文时(桩 client + mock fetch,不触真实网络)验证订阅/分页/创建/乐观重命名/帧合并/错误 toast。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSettingsStore } from '../../state/settingsStore';
import { renderWithProviders } from '../../test-utils/render';
import { HomePage } from '../pages/HomePage';
import { RealtimeContext } from '../AppShell';
import type { RealtimeContextValue } from '../AppShell';
import type { IssueSummary } from '../../types/entities';
import type { RealtimeFrame } from '../../types/realtime';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

interface StubClient {
  subscribe: ReturnType<typeof vi.fn>;
  unsubscribe: ReturnType<typeof vi.fn>;
  onFrame: ReturnType<typeof vi.fn>;
  emit: (frame: RealtimeFrame) => void;
}

function createStubClient(): StubClient {
  const frameListeners = new Set<(frame: RealtimeFrame) => void>();
  return {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((cb: (frame: RealtimeFrame) => void) => {
      frameListeners.add(cb);
      return () => {
        frameListeners.delete(cb);
      };
    }),
    emit: (frame: RealtimeFrame) => {
      for (const cb of frameListeners) cb(frame);
    },
  };
}

function renderWithRealtime(client: StubClient): ReturnType<typeof renderWithProviders> {
  const value: RealtimeContextValue = { state: 'connected', client: client as never };
  const ui: ReactElement = (
    <RealtimeContext.Provider value={value}>
      <HomePage />
    </RealtimeContext.Provider>
  );
  return renderWithProviders(ui);
}

const ISSUE_1: IssueSummary = {
  id: 'id-1',
  identifier: 'DEM-1',
  title: 'First issue',
  status_category: 'todo',
  updated_at: '2026-07-25T10:00:00Z',
};
const ISSUE_2: IssueSummary = {
  id: 'id-2',
  identifier: 'DEM-2',
  title: 'Second issue',
  status_category: 'in_progress',
  updated_at: '2026-07-25T10:01:00Z',
};

describe('HomePage 演示区(无网络依赖)', () => {
  beforeEach(() => {
    useSettingsStore.getState().resetPreferences();
  });

  it('渲染全部演示区', () => {
    renderWithProviders(<HomePage />);
    expect(screen.getByTestId('demo-theme')).toBeInTheDocument();
    expect(screen.getByTestId('demo-locale')).toBeInTheDocument();
    expect(screen.getByTestId('demo-shortcuts')).toBeInTheDocument();
    expect(screen.getByTestId('demo-states')).toBeInTheDocument();
    expect(screen.getByTestId('demo-realtime')).toBeInTheDocument();
  });

  it('demo-theme 按钮即时切换主题', () => {
    renderWithProviders(<HomePage />);
    fireEvent.click(screen.getByTestId('demo-theme-dark'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    fireEvent.click(screen.getByTestId('demo-theme-light'));
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('demo-locale 按钮切换目录语言(ICU 文案随之变化)', () => {
    renderWithProviders(<HomePage />);
    const icu = screen.getByTestId('demo-icu');
    expect(icu.textContent).toBe('3 comments');
    fireEvent.click(screen.getByTestId('demo-locale-zh'));
    expect(icu.textContent).toBe('3 条评论');
    fireEvent.click(screen.getByTestId('demo-locale-en'));
    expect(icu.textContent).toBe('3 comments');
  });

  it('demo-count 更新 ICU 复数文案(含 =0 分支)', () => {
    renderWithProviders(<HomePage />);
    const input = screen.getByTestId('demo-count');
    fireEvent.change(input, { target: { value: '1' } });
    expect(screen.getByTestId('demo-icu').textContent).toBe('1 comment');
    fireEvent.change(input, { target: { value: '0' } });
    expect(screen.getByTestId('demo-icu').textContent).toBe('No comments');
    fireEvent.change(input, { target: { value: '5' } });
    expect(screen.getByTestId('demo-icu').textContent).toBe('5 comments');
  });

  it('demo-relative 呈现相对时间(非空)', () => {
    renderWithProviders(<HomePage />);
    expect(screen.getByTestId('demo-relative').textContent?.length).toBeGreaterThan(0);
  });

  it('demo-states 呈现 loading/empty/retry 三态,retry 触发 toast', () => {
    renderWithProviders(<HomePage />);
    expect(screen.getByText('Nothing here yet')).toBeInTheDocument();
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Retry'));
    expect(screen.getByText('If the problem persists, refresh the page.')).toBeInTheDocument();
  });

  it('无实时上下文时 realtime 区呈现登录提示', () => {
    renderWithProviders(<HomePage />);
    expect(screen.getByTestId('demo-realtime-hint')).toBeInTheDocument();
  });
});

describe('HomePage 实时演示(桩 client + mock fetch)', () => {
  beforeEach(() => {
    useSettingsStore.getState().resetPreferences();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('挂载时订阅演示频道并以游标分页播种列表', async () => {
    let getCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET';
        if (method === 'GET') {
          getCalls += 1;
          if (getCalls === 1) return jsonResponse({ data: [ISSUE_1], next_cursor: 'page-2' });
          return jsonResponse({ data: [ISSUE_2], next_cursor: null });
        }
        return jsonResponse({}, 500);
      }),
    );
    const client = createStubClient();
    renderWithRealtime(client);

    await waitFor(() => expect(screen.getByTestId('demo-issue-DEM-1')).toBeInTheDocument());
    expect(client.subscribe).toHaveBeenCalledWith('workspace:ws-1:issues');

    // load more 拉第二页
    fireEvent.click(screen.getByTestId('demo-load-more'));
    await waitFor(() => expect(screen.getByTestId('demo-issue-DEM-2')).toBeInTheDocument());
    // 末页后 load more 隐藏
    await waitFor(() => expect(screen.queryByTestId('demo-load-more')).not.toBeInTheDocument());
  });

  it('创建新工作项(POST)后出现在列表', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET';
        if (method === 'GET') return jsonResponse({ data: [ISSUE_1], next_cursor: null });
        if (method === 'POST') {
          return jsonResponse({ data: { ...ISSUE_2, id: 'id-new', identifier: 'DEM-9' } });
        }
        return jsonResponse({}, 500);
      }),
    );
    renderWithRealtime(createStubClient());
    await waitFor(() => expect(screen.getByTestId('demo-issue-DEM-1')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('demo-new-title'), { target: { value: 'Fresh' } });
    fireEvent.click(screen.getByTestId('demo-create'));
    await waitFor(() => expect(screen.getByTestId('demo-issue-DEM-9')).toBeInTheDocument());
  });

  it('空标题不发起创建请求', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ data: [], next_cursor: null }));
    vi.stubGlobal('fetch', fetchMock);
    renderWithRealtime(createStubClient());
    // 等待首屏 GET 落定(hasMore→false,load more 隐藏),避免异步状态更新逸出 act
    await waitFor(() => expect(screen.queryByTestId('demo-load-more')).not.toBeInTheDocument());
    const callsBefore = fetchMock.mock.calls.length;
    fireEvent.click(screen.getByTestId('demo-create'));
    expect(fetchMock.mock.calls.length).toBe(callsBefore);
  });

  it('乐观重命名(PATCH)成功后更新行标题', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET';
        if (method === 'GET') return jsonResponse({ data: [ISSUE_1], next_cursor: null });
        if (method === 'PATCH') return jsonResponse({ data: { ...ISSUE_1, title: 'First issue ✓' } });
        return jsonResponse({}, 500);
      }),
    );
    renderWithRealtime(createStubClient());
    await waitFor(() => expect(screen.getByTestId('demo-issue-DEM-1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('demo-rename-DEM-1'));
    await waitFor(() => expect(screen.getByText('First issue ✓')).toBeInTheDocument());
  });

  it('实时帧经 mergeEntityFrame 合并进列表', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ data: [ISSUE_1], next_cursor: null })),
    );
    const client = createStubClient();
    renderWithRealtime(client);
    await waitFor(() => expect(screen.getByTestId('demo-issue-DEM-1')).toBeInTheDocument());

    const frame: RealtimeFrame = {
      seq: 1,
      type: 'issue.created',
      topic: 'workspace:ws-1:issues',
      ts: '2026-07-25T10:05:00Z',
      data: { id: 'id-3', identifier: 'DEM-3', title: 'From frame', status_category: 'todo', updated_at: '2026-07-25T10:05:00Z' },
    };
    client.emit(frame);
    await waitFor(() => expect(screen.getByTestId('demo-issue-DEM-3')).toBeInTheDocument());
  });

  it('创建失败呈现错误 toast(error.network)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET';
        if (method === 'GET') return jsonResponse({ data: [], next_cursor: null });
        return Promise.reject(new TypeError('network down'));
      }),
    );
    renderWithRealtime(createStubClient());
    fireEvent.change(screen.getByTestId('demo-new-title'), { target: { value: 'Boom' } });
    fireEvent.click(screen.getByTestId('demo-create'));
    await waitFor(() =>
      expect(screen.getByText('Network error. Please check your connection and try again.')).toBeInTheDocument(),
    );
  });
});
