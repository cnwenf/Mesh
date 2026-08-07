/**
 * BulkBindDialog 一绑多 agent 测试(L247 批量操作):多选提交、部分成功汇总
 * (含 error marker)、全选切换、空态禁用、网络失败 danger toast。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient, getToken } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { env } from '../../../env';
import { renderWithProviders } from '../../../test-utils/render';
import { BulkBindDialog } from '../BulkBindDialog';
import type { BulkBindAgentOption } from '../BulkBindDialog';
import type { SkillInstallation } from '../types';

const INSTALLATION = {
  id: 'i-1',
  workspace_id: 'ws-1',
  skill_id: 's-1',
  skill_version_id: 'v-1',
  scope: 'workspace',
  agent_id: null,
  install_status: 'installed',
  auto_update: false,
  granted_capabilities: [],
  installed_by: 'm-1',
  installed_at: '2026-01-01T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
} as const satisfies SkillInstallation;

// id 为 agents 实体表主键(skills/bulk-bind 的 agent_ids 按 Agent.id 解析),
// 不是成员名册行 id —— 命名上显式区分,防止两类 id 再次混用。
const AGENTS: readonly BulkBindAgentOption[] = [
  { id: 'agent-entity-1', displayName: 'Planner' },
  { id: 'agent-entity-2', displayName: 'Coder' },
];

interface BulkBindStub {
  readonly calls: { url: string; body: unknown }[];
  readonly fetchImpl: typeof fetch;
}

function stubBulkBind(body: unknown, status = 200): BulkBindStub {
  const calls: { url: string; body: unknown }[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), body: JSON.parse(String(init?.body ?? '{}')) });
    return fakeResponse({ status, body });
  }) as typeof fetch;
  return { calls, fetchImpl };
}

function renderDialog(agents: readonly BulkBindAgentOption[] = AGENTS, stub: BulkBindStub = stubBulkBind({ data: { bound: [], errors: [] } })) {
  const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken, fetchImpl: stub.fetchImpl });
  const onDone = vi.fn();
  const onClose = vi.fn();
  renderWithProviders(
    <BulkBindDialog
      open
      onClose={onClose}
      client={client}
      workspaceId="ws-1"
      installation={INSTALLATION}
      agents={agents}
      onDone={onDone}
    />,
  );
  return { onDone, onClose };
}

describe('BulkBindDialog (L247)', () => {
  it('submits the selected agents to skills/bulk-bind and reports success', async () => {
    const stub = stubBulkBind({ data: { bound: [{ binding_id: 'b-1' }, { binding_id: 'b-2' }], errors: [] } });
    const { onDone, onClose } = renderDialog(AGENTS, stub);

    fireEvent.click(screen.getByTestId('bulk-bind-agent-agent-entity-1'));
    fireEvent.click(screen.getByTestId('bulk-bind-agent-agent-entity-2'));
    fireEvent.click(screen.getByTestId('bulk-bind-confirm'));

    await waitFor(() => expect(stub.calls.length).toBe(1));
    expect(stub.calls[0].url).toContain('/workspaces/ws-1/skills/bulk-bind');
    expect(stub.calls[0].body).toEqual({
      skill_installation_id: 'i-1',
      agent_ids: ['agent-entity-1', 'agent-entity-2'],
    });
    expect(await screen.findByText('Bulk bind: 2 succeeded, 0 failed')).toBeTruthy();
    expect(onDone).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('partial failure shows warn toast with per-agent markers and still closes', async () => {
    const stub = stubBulkBind({
      data: {
        bound: [{ binding_id: 'b-1' }],
        errors: [{ agent_id: 'agent-entity-2', code: 'conflict', message: 'already bound' }],
      },
    });
    const { onDone, onClose } = renderDialog(AGENTS, stub);

    fireEvent.click(screen.getByTestId('bulk-bind-select-all'));
    fireEvent.click(screen.getByTestId('bulk-bind-confirm'));

    const toast = await screen.findByText(/Bulk bind: 1 succeeded, 1 failed/);
    // error marker 取 agent_id 前 8 位('agent-entity-2' → 'agent-en')。
    expect(toast.textContent).toContain('agent-en: conflict');
    expect(onDone).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('select-all toggles every agent; confirm disabled with none selected', () => {
    renderDialog();

    const confirm = screen.getByTestId('bulk-bind-confirm') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(screen.getByTestId('bulk-bind-select-all'));
    expect((screen.getByTestId('bulk-bind-agent-agent-entity-1') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId('bulk-bind-agent-agent-entity-2') as HTMLInputElement).checked).toBe(true);
    expect(confirm.disabled).toBe(false);
    fireEvent.click(screen.getByTestId('bulk-bind-select-all'));
    expect((screen.getByTestId('bulk-bind-agent-agent-entity-1') as HTMLInputElement).checked).toBe(false);
    expect(confirm.disabled).toBe(true);
  });

  it('empty agent roster shows the empty note and disables confirm', () => {
    renderDialog([]);
    expect(screen.getByTestId('bulk-bind-empty')).toBeTruthy();
    expect((screen.getByTestId('bulk-bind-confirm') as HTMLButtonElement).disabled).toBe(true);
  });

  it('network failure shows a danger toast and keeps the dialog open', async () => {
    const stub = stubBulkBind({ error: { code: 'internal', message: 'boom' } }, 500);
    const { onClose } = renderDialog(AGENTS, stub);

    fireEvent.click(screen.getByTestId('bulk-bind-agent-agent-entity-1'));
    fireEvent.click(screen.getByTestId('bulk-bind-confirm'));

    expect(await screen.findByText('Bulk bind failed.')).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId('bulk-bind-body')).toBeTruthy();
  });
});
