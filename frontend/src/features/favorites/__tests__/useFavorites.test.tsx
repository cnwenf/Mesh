/**
 * useFavorites 收藏状态 hook 测试(README §6.19,L222):
 * 列表加载 → 成员集合;toggle 乐观更新 + PUT/DELETE;失败回滚 + danger toast;
 * workspaceId 缺失不发请求;列表失败降级空集合。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import { screen } from '@testing-library/react';
import { useFavorites } from '../useFavorites';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

function ToastLayer(props: { readonly children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

function renderFavoritesHook(
  workspaceId: string | null = 'ws-1',
): ReturnType<typeof renderHook<ReturnType<typeof useFavorites>, []>> {
  return renderHook(() => useFavorites(workspaceId, 'issue'), {
    wrapper: ({ children }) => (
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>{children}</ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    ),
  });
}

function stubFavoritesFetch(
  handler: (url: string, init?: RequestInit) => Response,
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const listResponse = (ids: readonly string[]): Response =>
  fakeResponse({
    body: {
      data: ids.map((targetId) => ({ target_type: 'issue', target_id: targetId })),
      next_cursor: null,
    },
  });

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useFavorites', () => {
  it('加载收藏列表并给出成员集合', async () => {
    const fetchMock = stubFavoritesFetch(() => listResponse(['iss-1', 'iss-3']));

    const { result } = renderFavoritesHook();

    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(fetchMock.mock.calls[0]?.[0]).toContain('/api/v1/favorites');
    expect(fetchMock.mock.calls[0]?.[0]).toContain('target_type=issue');
    expect(result.current.favoriteIds.has('iss-1')).toBe(true);
    expect(result.current.favoriteIds.has('iss-2')).toBe(false);
  });

  it('toggle 未收藏目标:乐观加入集合并 PUT', async () => {
    const fetchMock = stubFavoritesFetch((url, init) => {
      if ((init?.method ?? 'GET') === 'GET') return listResponse([]);
      if (url.endsWith('/favorites/issue/iss-9') && init?.method === 'PUT') {
        return fakeResponse({ status: 201, body: { data: null } });
      }
      throw new Error(`unexpected ${url}`);
    });

    const { result } = renderFavoritesHook();
    await waitFor(() => expect(result.current.isLoaded).toBe(true));

    await act(async () => {
      await result.current.toggle('iss-9');
    });

    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).endsWith('/favorites/issue/iss-9') && init?.method === 'PUT',
      ),
    ).toBe(true);
    expect(result.current.favoriteIds.has('iss-9')).toBe(true);
    expect(result.current.isToggling).toBe(false);
  });

  it('toggle 已收藏目标:从集合移除并 DELETE', async () => {
    const fetchMock = stubFavoritesFetch((url, init) => {
      if ((init?.method ?? 'GET') === 'GET') return listResponse(['iss-1']);
      if (url.endsWith('/favorites/issue/iss-1') && init?.method === 'DELETE') {
        return fakeResponse({ status: 204 });
      }
      throw new Error(`unexpected ${url}`);
    });

    const { result } = renderFavoritesHook();
    await waitFor(() => expect(result.current.favoriteIds.has('iss-1')).toBe(true));

    await act(async () => {
      await result.current.toggle('iss-1');
    });

    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          String(url).endsWith('/favorites/issue/iss-1') && init?.method === 'DELETE',
      ),
    ).toBe(true);
    expect(result.current.favoriteIds.has('iss-1')).toBe(false);
  });

  it('toggle 失败:回滚集合并呈现 danger toast', async () => {
    stubFavoritesFetch((_url, init) => {
      if ((init?.method ?? 'GET') === 'GET') return listResponse([]);
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      });
    });

    const { result } = renderFavoritesHook();
    await waitFor(() => expect(result.current.isLoaded).toBe(true));

    await act(async () => {
      await result.current.toggle('iss-9');
    });

    expect(result.current.favoriteIds.has('iss-9')).toBe(false);
    expect(screen.getByText('An internal error occurred. Please try again.')).toBeTruthy();
  });

  it('workspaceId 为 null:不发请求,toggle 空操作', async () => {
    const fetchMock = stubFavoritesFetch(() => listResponse([]));

    const { result } = renderFavoritesHook(null);

    await act(async () => undefined);
    expect(fetchMock.mock.calls.length).toBe(0);
    await act(async () => {
      await result.current.toggle('iss-1');
    });
    expect(fetchMock.mock.calls.length).toBe(0);
    expect(result.current.favoriteIds.size).toBe(0);
  });

  it('列表拉取失败:降级为空集合(isLoaded=true,不打断宿主页面)', async () => {
    stubFavoritesFetch(() =>
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
    );

    const { result } = renderFavoritesHook();

    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(result.current.favoriteIds.size).toBe(0);
  });
});
