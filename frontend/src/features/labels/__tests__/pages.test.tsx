/**
 * 标签 / 自定义字段设置子页测试:覆盖 workspace===null 守卫(经 mock 注入,因真实
 * WorkspaceGate 在 ready 前不渲染子树,该守卫 otherwise 不可达)、admin 门禁双分支、
 * 面板接通。getApiClient 经 mock 返回永不 resolve 的列表,使面板停在 loading,避免网络噪声。
 */
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { ToastProvider } from '../../../design';
import { WorkspaceCustomFieldsPage } from '../pages/WorkspaceCustomFieldsPage';
import { WorkspaceLabelsPage } from '../pages/WorkspaceLabelsPage';

const wsState = vi.hoisted(() => ({
  value: {
    status: 'ready' as string,
    workspace: null as null | { id: string; slug: string },
    error: null,
    isAdmin: true,
    isOwner: true,
    refresh: () => Promise.resolve(),
    patch: () => Promise.resolve({}),
  },
}));

vi.mock('../../../workspace/WorkspaceProvider', () => ({
  useWorkspace: () => wsState.value,
  // 透传:让子组件无条件运行,以便直接断言子组件自身的守卫分支。
  WorkspaceGate: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock('../../../api/instance', () => ({
  getApiClient: () => ({
    list: () => new Promise(() => undefined),
    request: () => new Promise(() => undefined),
  }),
}));

function setWs(workspace: null | { id: string; slug: string }, isAdmin: boolean): void {
  wsState.value = { ...wsState.value, workspace, isAdmin };
}

function renderLabels(): ReturnType<typeof render> {
  return render(
    <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
      <ToastProvider regionLabel="notifications">
        <WorkspaceLabelsPage />
      </ToastProvider>
    </I18nProvider>,
  );
}

function renderFields(): ReturnType<typeof render> {
  return render(
    <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
      <ToastProvider regionLabel="notifications">
        <WorkspaceCustomFieldsPage />
      </ToastProvider>
    </I18nProvider>,
  );
}

beforeEach(() => setWs({ id: 'ws-1', slug: 'acme' }, true));

describe('WorkspaceLabelsPage', () => {
  it('renders nothing while workspace is null (guard branch)', () => {
    setWs(null, true);
    renderLabels();
    expect(screen.queryByTestId('labels-panel')).toBeNull();
    expect(screen.queryByTestId('ws-labels-denied')).toBeNull();
  });

  it('renders the labels panel for admins', () => {
    setWs({ id: 'ws-1', slug: 'acme' }, true);
    renderLabels();
    expect(screen.getByTestId('ws-labels-page')).toBeTruthy();
    expect(screen.getByTestId('labels-panel')).toBeTruthy();
  });

  it('renders the permission block for non-admins', () => {
    setWs({ id: 'ws-1', slug: 'acme' }, false);
    renderLabels();
    expect(screen.getByTestId('ws-labels-denied')).toBeTruthy();
    expect(screen.queryByTestId('labels-panel')).toBeNull();
  });
});

describe('WorkspaceCustomFieldsPage', () => {
  it('renders nothing while workspace is null (guard branch)', () => {
    setWs(null, true);
    renderFields();
    expect(screen.queryByTestId('custom-fields-panel')).toBeNull();
    expect(screen.queryByTestId('ws-fields-denied')).toBeNull();
  });

  it('renders the custom fields panel for owners', () => {
    setWs({ id: 'ws-1', slug: 'acme' }, true);
    renderFields();
    expect(screen.getByTestId('ws-fields-page')).toBeTruthy();
    expect(screen.getByTestId('custom-fields-panel')).toBeTruthy();
  });

  it('renders the permission block for guests', () => {
    setWs({ id: 'ws-1', slug: 'acme' }, false);
    renderFields();
    expect(screen.getByTestId('ws-fields-denied')).toBeTruthy();
    expect(screen.queryByTestId('custom-fields-panel')).toBeNull();
  });
});
