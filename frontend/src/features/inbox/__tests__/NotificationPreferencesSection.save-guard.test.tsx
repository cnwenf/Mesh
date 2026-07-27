/**
 * NotificationPreferencesSection 补充覆盖:save() 的 workspaceId===null 守卫(branch L73)。
 * 该守卫在正常 UI 流中不可达(workspaceId 为空时 isLoading 恒为 true、保存按钮不渲染),
 * 故此处 mock useInboxContext:先以非空 workspaceId 完成加载渲染出保存按钮,再翻转为 null
 * 触发重渲染,此时 isLoading 仍为 false(上一次成功加载所致),点击保存即命中空值守卫提前返回。
 * 独立成文件:vi.mock('../useInboxContext') 为模块级,会影响同文件其它用例。
 */
import { useState } from 'react';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { NotificationPreferencesSection } from '../NotificationPreferencesSection';

const mocks = vi.hoisted(() => ({ workspaceId: 'ws-1' as string | null }));
vi.mock('../useInboxContext', () => ({
  useInboxContext: () => ({ status: 'ready' as const, workspaceId: mocks.workspaceId, memberId: 'mem-1' }),
}));

/** 通过内部状态切换 workspaceId,驱动子组件重渲染以命中空值守卫。 */
function Harness(): React.JSX.Element {
  const [ws, setWs] = useState<string | null>('ws-1');
  mocks.workspaceId = ws;
  return (
    <>
      <NotificationPreferencesSection />
      <button type="button" data-testid="flip-ws" onClick={() => setWs(null)}>
        flip
      </button>
    </>
  );
}

beforeEach(() => {
  mocks.workspaceId = 'ws-1';
  vi.unstubAllGlobals();
});
afterEach(() => vi.unstubAllGlobals());

describe('NotificationPreferencesSection save() workspaceId 守卫 (branch L73)', () => {
  it('returns early without a PUT when workspaceId is null at save time', async () => {
    const stub: FetchStub = stubFetch(
      fakeResponse({ body: { data: [] } }), // GET prefs(加载)
      fakeResponse({ body: { data: [] } }), // PUT(若保存被调用)
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<Harness />);
    await screen.findByTestId('notification-prefs');
    // 翻转为 null:effect 重跑提前返回,isLoading 维持 false,保存按钮仍在
    fireEvent.click(screen.getByTestId('flip-ws'));
    await screen.findByTestId('pref-save');
    fireEvent.click(screen.getByTestId('pref-save'));
    // 空值守卫提前返回 → 不应发起 PUT
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(1));
    expect(stub.calls.some((c) => c.init?.method === 'PUT')).toBe(false);
  });
});
