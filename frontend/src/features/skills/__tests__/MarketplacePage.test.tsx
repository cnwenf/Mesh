/**
 * 技能市场页组件测试:卡片渲染 + 认证徽标 + 含脚本提示 + 导入入口。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { MarketplacePage } from '../MarketplacePage';

const ME = {
  user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'T',
      workspace_slug: 't',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

const ENTRIES = [
  {
    id: '1',
    name: '接口文档生成',
    summary: 'OpenAPI',
    version: '2.0.0',
    manifest_url: 'https://m/1.json',
    downloads: 500,
    rating: 4.8,
    certified: true,
    has_scripts: false,
    tags: ['docs'],
  },
  {
    id: '2',
    name: '依赖扫描',
    summary: 'CVE',
    version: '1.1.0',
    manifest_url: 'https://m/2.json',
    downloads: 1200,
    rating: 4.2,
    certified: false,
    has_scripts: true,
    tags: ['security'],
  },
];

function setup(entries: unknown = ENTRIES): { calls: { url: string; method: string }[] } {
  const calls: { url: string; method: string }[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/marketplace/skills'))
      return fakeResponse({ body: { data: entries, next_cursor: null } });
    if (method === 'POST' && url.endsWith('/skills/import')) {
      return fakeResponse({
        status: 202,
        body: {
          data: {
            task_id: 't-1',
            source_type: 'marketplace',
            uri: 'https://m/1.json',
            ref: null,
            status: 'awaiting_review',
            stage: 'review',
            percent: 100,
            preview: {
              name: '依赖扫描',
              version: '1.1.0',
              summary: 'CVE',
              instructions_preview: 'x',
              scripts: [
                {
                  path: 's.sh',
                  runtime: 'shell',
                  entrypoint: true,
                  required_capabilities: ['exec:shell'],
                },
              ],
              references: [],
              requested_capabilities: ['exec:shell'],
            },
            requires_approval: true,
            skill_id: 's-9',
            skill_version_id: 'v-9',
            installation_id: null,
            granted_capabilities: [],
            error: null,
            decision_comment: null,
            reviewed_by: null,
            reviewed_at: null,
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        },
      });
    }
    return fakeResponse({ body: { data: [] } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return { calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('MarketplacePage', () => {
  it('渲染卡片 + 认证徽标 + 含脚本提示', async () => {
    setup();
    renderWithProviders(<MarketplacePage />);
    const certified = await screen.findByTestId('market-entry-1');
    const unreviewed = screen.getByTestId('market-entry-2');
    expect(screen.getByText('接口文档生成')).toBeTruthy();
    expect(screen.getByText('依赖扫描')).toBeTruthy();
    expect(screen.getAllByText(/含脚本|Contains scripts/).length).toBeGreaterThan(0);
    expect(certified).toHaveTextContent(/Marketplace|市场/);
    expect(certified).toHaveTextContent(/Reviewed|已审查/);
    expect(unreviewed).toHaveTextContent(/Untrusted|不受信任/);
  });

  it('搜索带 q 参数', async () => {
    const { calls } = setup();
    renderWithProviders(<MarketplacePage />);
    await screen.findByTestId('market-entry-1');
    fireEvent.change(screen.getByLabelText(/Search skills|搜索技能/), { target: { value: 'doc' } });
    await waitFor(() => {
      expect(calls.some((c) => c.url.includes('q=doc'))).toBe(true);
    });
  });

  it('点击导入:有 manifest_url 时打开向导', async () => {
    setup();
    renderWithProviders(<MarketplacePage />);
    await screen.findByTestId('market-import-1');
    fireEvent.click(screen.getByTestId('market-import-1'));
    // initialUri auto-starts the fetch → lands on the preview/review step.
    await screen.findByTestId('import-scripts');
  });

  it('空市场 → 空态', async () => {
    setup([]);
    renderWithProviders(<MarketplacePage />);
    await screen.findByTestId('marketplace-title');
    expect(screen.queryByTestId('market-entry-1')).toBeNull();
  });

  it('无 manifest_url 点击导入 → 提示', async () => {
    const noUrl = [{ ...ENTRIES[0], manifest_url: '' }];
    setup(noUrl);
    renderWithProviders(<MarketplacePage />);
    await screen.findByTestId('market-import-1');
    fireEvent.click(screen.getByTestId('market-import-1'));
    // no wizard opens when manifest_url is empty
    expect(screen.queryByTestId('import-scripts')).toBeNull();
  });

  it('只读成员隐藏导入按钮', async () => {
    const meMember = { ...ME, memberships: [{ ...ME.memberships[0], role: 'member' }] };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: meMember } });
      return fakeResponse({ body: { data: ENTRIES, next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<MarketplacePage />);
    await screen.findByTestId('market-entry-1');
    expect(screen.queryByTestId('market-import-1')).toBeNull();
  });

  it('预览按钮打开预览对话框', async () => {
    setup();
    renderWithProviders(<MarketplacePage />);
    await screen.findByTestId('market-preview-2');
    fireEvent.click(screen.getByTestId('market-preview-2'));
    const preview = await screen.findByTestId('market-preview-dialog');
    expect(preview).toHaveTextContent(/No|否/);
    expect(preview).toHaveTextContent(/Contains scripts|含脚本/);
  });

  it('预览对话框内导入(无 manifest)→ 提示', async () => {
    const noManifest = [{ ...ENTRIES[0], manifest_url: '' }];
    setup(noManifest);
    renderWithProviders(<MarketplacePage />);
    fireEvent.click(await screen.findByTestId('market-preview-1'));
    await screen.findByTestId('market-preview-dialog');
    fireEvent.click(screen.getByTestId('market-preview-import'));
    // dialog closes + no wizard opens (manifest empty path)
    await waitFor(() => expect(screen.queryByTestId('import-step-source')).toBeNull());
  });

  it('预览与导入对话框均可通过各自关闭回调退出', async () => {
    setup();
    renderWithProviders(<MarketplacePage />);

    fireEvent.click(await screen.findByTestId('market-preview-1'));
    await screen.findByTestId('market-preview-dialog');
    fireEvent.click(screen.getByRole('button', { name: /Close dialog|关闭对话框/ }));
    await waitFor(() => expect(screen.queryByTestId('market-preview-dialog')).toBeNull());

    fireEvent.click(screen.getByTestId('market-import-1'));
    await screen.findByTestId('import-scripts');
    fireEvent.click(screen.getByRole('button', { name: /Close dialog|关闭对话框/ }));
    await waitFor(() => expect(screen.queryByTestId('import-scripts')).toBeNull());
  });

  it('导入向导完成拒绝后清理所选市场条目', async () => {
    setup();
    renderWithProviders(<MarketplacePage />);

    fireEvent.click(await screen.findByTestId('market-import-2'));
    await screen.findByTestId('import-scripts');
    fireEvent.click(screen.getByTestId('import-reject'));
    await waitFor(() => expect(screen.queryByTestId('import-scripts')).toBeNull());
  });
});
