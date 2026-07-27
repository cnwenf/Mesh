/**
 * AgentWizard 组件测试(agent.md §4.4):四步导航、每步校验、预设套用、
 * 从模板/从现有 agent 复制(M-F3)、创建与编辑两条完成路径、错误回显。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { MeshApiError } from '../../../api/errors';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { AgentWizard } from '../AgentWizard';
import type { AgentDetail } from '../types';

afterEach(() => {
  vi.restoreAllMocks();
});

const DETAIL: AgentDetail = {
  id: 'a-1',
  member: null,
  display_name: '小测',
  name: '小测',
  avatar_url: null,
  role_tag: '测试工程师',
  badge_kind: 'ai',
  lifecycle_status: 'active',
  visibility: 'workspace',
  trigger_on_assign: true,
  owner_user_id: 'u-1',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  slug: null,
  bio: 'bio',
  system_instructions: '旧指令',
  model_config: { model_tier: 'balanced', temperature: 0.2, top_p: 1, max_tokens: 8192 },
  default_runtime_id: null,
  active_config_version_id: 'v-1',
  current_version: null,
};

function makeClient(opts: { failCreate?: boolean; failCopy?: boolean; failCreateApi?: boolean } = {}) {
  const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: { id: 'a-new' } } }));
  const request = vi.fn(async (_m: string, _p: string, bodyOpts?: { body?: unknown }) => {
    await fetchImpl(_p, {
      method: _m,
      body: bodyOpts?.body ? JSON.stringify(bodyOpts.body) : undefined,
    });
    if (opts.failCreate && _m === 'POST' && _p.endsWith('/agents')) {
      const err = new Error('request failed') as Error & { code?: string };
      err.code = 'validation_error';
      throw err;
    }
    return { id: 'a-new' };
  });
  const list = vi.fn(async () => {
    await fetchImpl('/agents', {});
    return { data: [{ id: 'src-1', display_name: '源 agent' }], nextCursor: null };
  });
  // getAgent (copy-from) returns DETAIL via request GET — or throws on failCopy.
  request.mockImplementation(async (m, p) => {
    if (m === 'GET') {
      if (opts.failCopy) throw new Error('copy failed');
      return DETAIL;
    }
    if (opts.failCreateApi && m === 'POST' && p.endsWith('/agents')) {
      throw new MeshApiError({
        status: 422,
        code: 'validation_error',
        message: 'name taken',
        details: { fields: [{ field: 'name', issue: 'taken' }] },
      });
    }
    if (opts.failCreate && m === 'POST' && p.endsWith('/agents')) {
      const err = new Error('request failed') as Error & { code?: string };
      err.code = 'validation_error';
      throw err;
    }
    return { id: 'a-new' };
  });
  return {
    client: { request, list, fetchImpl } as unknown as MeshApiClient,
    request,
    list,
    calls,
  };
}

function renderWizard(client: MeshApiClient, agent: AgentDetail | null = null) {
  return renderWithProviders(
    <AgentWizard
      open
      onClose={() => undefined}
      client={client}
      workspaceId="ws-1"
      agent={agent}
      onSaved={() => undefined}
    />,
  );
}

describe('AgentWizard 创建流程', () => {
  it('名称为空时下一步禁用;填写后可编辑', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    expect(screen.getByTestId('agent-wizard-basic')).toBeInTheDocument();
    expect((screen.getByTestId('agent-wizard-next') as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    expect((screen.getByTestId('agent-wizard-next') as HTMLButtonElement).disabled).toBe(false);
  });

  it('avatar 非 https 拦截下一步', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.type(screen.getByTestId('agent-wizard-avatar'), 'javascript:alert(1)');
    expect((screen.getByTestId('agent-wizard-next') as HTMLButtonElement).disabled).toBe(true);
  });

  it('四步走通并 POST 创建,携带 top_p / preset', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    expect(screen.getByTestId('agent-wizard-model')).toBeInTheDocument();
    // 模型步 top_p 越界拦截
    const topP = screen.getByTestId('agent-wizard-top-p') as HTMLInputElement;
    await user.clear(topP);
    await user.type(topP, '2');
    expect((screen.getByTestId('agent-wizard-next') as HTMLButtonElement).disabled).toBe(true);
    await user.clear(topP);
    await user.type(topP, '1');
    await user.click(screen.getByTestId('agent-wizard-next'));
    expect(screen.getByTestId('agent-wizard-skills')).toBeInTheDocument();
    await user.click(screen.getByTestId('agent-wizard-next'));
    expect(screen.getByTestId('agent-wizard-visibility')).toBeInTheDocument();
    await user.click(screen.getByTestId('agent-wizard-finish'));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = request.mock.calls[0][2] as { body: { model_config: { top_p: number } } };
    expect(body.body.model_config.top_p).toBe(1);
  });

  it('预设套用填充模型参数', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.selectOptions(screen.getByTestId('agent-wizard-preset'), 'fast_triage');
    expect((screen.getByTestId('agent-wizard-temperature') as HTMLInputElement).value).toBe('0.3');
  });

  it('从模板创建预填 profile', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.selectOptions(screen.getByTestId('agent-wizard-template'), 'docs');
    expect((screen.getByTestId('agent-wizard-name') as HTMLInputElement).value).toBe('文档助手');
  });

  it('从现有 agent 复制预填(getAgent)', async () => {
    const user = userEvent.setup();
    const { client, list, request } = makeClient();
    renderWizard(client);
    await waitFor(() => expect(list).toHaveBeenCalled());
    // 等候选项渲染完成再选(list 被调 ≠ options 已入 DOM)。
    await screen.findByRole('option', { name: '源 agent' });
    await user.selectOptions(screen.getByTestId('agent-wizard-copy-from'), 'src-1');
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect((screen.getByTestId('agent-wizard-name') as HTMLInputElement).value).toBe('小测');
  });

  it('创建失败回显 error.* 文案', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient({ failCreate: true });
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-finish'));
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect(await screen.findByTestId('agent-wizard-error')).toBeInTheDocument();
  });

  it('模型步各控件 onChange 覆盖(top_p/模型/预设/推理强度)', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.type(screen.getByTestId('agent-wizard-top-p'), '0');
    await user.selectOptions(
      screen.getByTestId('agent-wizard-model-select'),
      'mainstream-llm-balanced',
    );
    await user.selectOptions(screen.getByTestId('agent-wizard-preset'), 'creative_draft');
    await user.selectOptions(screen.getByTestId('agent-wizard-effort'), 'high');
    expect((screen.getByTestId('agent-wizard-temperature') as HTMLInputElement).value).toBe('0.9');
  });

  it('从现有 agent 复制失败回显 toast', async () => {
    const user = userEvent.setup();
    const { client, list } = makeClient({ failCopy: true });
    renderWizard(client);
    await waitFor(() => expect(list).toHaveBeenCalled());
    await screen.findByRole('option', { name: '源 agent' });
    await user.selectOptions(screen.getByTestId('agent-wizard-copy-from'), 'src-1');
    // 失败时不预填名字(保持空),且 wizard 不崩溃。
    await waitFor(() =>
      expect((screen.getByTestId('agent-wizard-name') as HTMLInputElement).value).toBe(''),
    );
  });

  it('可见性步切换 visibility 与 triggerOnAssign', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    expect(screen.getByTestId('agent-wizard-visibility')).toBeInTheDocument();
    await user.click(screen.getByTestId('agent-wizard-visibility-private'));
    await user.click(screen.getByTestId('agent-wizard-trigger-on-assign'));
  });
});

describe('AgentWizard 编辑流程', () => {
  it('预填现有值并完成 PATCH + PATCH /config', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    renderWizard(client, DETAIL);
    expect((screen.getByTestId('agent-wizard-name') as HTMLInputElement).value).toBe('小测');
    // 直达最后一步并完成
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-finish'));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  });

  it('后退不丢数据', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-back'));
    expect((screen.getByTestId('agent-wizard-name') as HTMLInputElement).value).toBe('小测');
  });

  it('基本步:role_tag / bio 输入,名称超 120 字红字拦截', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-role-tag'), '测试工程师');
    await user.type(screen.getByTestId('agent-wizard-bio'), '负责回归');
    await user.type(screen.getByTestId('agent-wizard-name'), 'x'.repeat(121));
    expect(screen.getByText('Name must be 120 characters or fewer.')).toBeInTheDocument();
    expect((screen.getByTestId('agent-wizard-next') as HTMLButtonElement).disabled).toBe(true);
  });

  it('模型步:tier 单选 / 说明书 / temperature / maxTokens 交互,越界拦截下一步', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-tier-strong_reasoning'));
    await user.type(screen.getByTestId('agent-wizard-instructions'), '你是测试工程师。');
    await user.clear(screen.getByTestId('agent-wizard-temperature'));
    await user.type(screen.getByTestId('agent-wizard-temperature'), '0.4');
    // max_tokens 清空 → 越界红字 + 下一步禁用。
    await user.clear(screen.getByTestId('agent-wizard-max-tokens'));
    expect((screen.getByTestId('agent-wizard-next') as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByTestId('agent-wizard-max-tokens'), '2048');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-finish'));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = request.mock.calls[0][2] as {
      body: { model_config: { model_tier: string; temperature: number; max_tokens: number } };
    };
    expect(body.body.model_config).toMatchObject({
      model_tier: 'strong_reasoning',
      temperature: 0.4,
      max_tokens: 2048,
    });
  });

  it('模板/复制下拉选「无」为 no-op', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.selectOptions(screen.getByTestId('agent-wizard-template'), '');
    await user.selectOptions(screen.getByTestId('agent-wizard-copy-from'), '');
    expect((screen.getByTestId('agent-wizard-name') as HTMLInputElement).value).toBe('');
  });

  it('创建时填写 avatar / 说明书 / 模型,body 携带非空值', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.type(screen.getByTestId('agent-wizard-avatar'), 'https://cdn.example/a.png');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.type(screen.getByTestId('agent-wizard-instructions'), '你是测试工程师。');
    await user.selectOptions(screen.getByTestId('agent-wizard-model-select'), 'mainstream-llm-light');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-finish'));
    await waitFor(() => expect(request).toHaveBeenCalled());
    // 创建路径:单 POST /agents,avatar / 说明书 / 模型都在 body。
    const body = request.mock.calls[0][2] as {
      body: {
        avatar_url: string | null;
        system_instructions: string | null;
        model_config: { model?: string };
      };
    };
    expect(body.body.avatar_url).toBe('https://cdn.example/a.png');
    expect(body.body.system_instructions).toBe('你是测试工程师。');
    expect(body.body.model_config.model).toBe('mainstream-llm-light');
  });

  it('编辑态字段清空后保存 → avatar / 说明书落 null', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    renderWizard(client, { ...DETAIL, avatar_url: 'https://cdn.example/o.png', bio: null, role_tag: null, system_instructions: null, model_config: {} });
    await user.clear(screen.getByTestId('agent-wizard-avatar'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-finish'));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    const patchBody = request.mock.calls[0][2] as {
      body: { avatar_url: string | null; system_instructions?: string | null };
    };
    expect(patchBody.body.avatar_url).toBe(null);
  });

  it('从现有 agent 复制:稀疏源字段回退默认值(?? 分支)', async () => {
    const user = userEvent.setup();
    const { client, list, request } = makeClient();
    request.mockImplementation(async (m) => {
      if (m === 'GET') {
        return {
          ...DETAIL,
          name: '稀疏源',
          avatar_url: null,
          role_tag: null,
          bio: null,
          system_instructions: null,
          model_config: {},
          visibility: 'workspace',
          trigger_on_assign: true,
        };
      }
      return { id: 'a-new' };
    });
    renderWizard(client);
    await waitFor(() => expect(list).toHaveBeenCalled());
    await screen.findByRole('option', { name: '源 agent' });
    await user.selectOptions(screen.getByTestId('agent-wizard-copy-from'), 'src-1');
    await waitFor(() =>
      expect((screen.getByTestId('agent-wizard-name') as HTMLInputElement).value).toBe('稀疏源'),
    );
    // 各 ?? 回退:role_tag/bio 空串(基本步)。
    expect((screen.getByTestId('agent-wizard-role-tag') as HTMLInputElement).value).toBe('');
    expect((screen.getByTestId('agent-wizard-bio') as HTMLInputElement).value).toBe('');
    // 模型步:model_config {} → temperature 0.2、top_p 1、max_tokens 8192。
    await user.click(screen.getByTestId('agent-wizard-next'));
    expect((screen.getByTestId('agent-wizard-temperature') as HTMLInputElement).value).toBe('0.2');
    expect((screen.getByTestId('agent-wizard-top-p') as HTMLInputElement).value).toBe('1');
    expect((screen.getByTestId('agent-wizard-max-tokens') as HTMLInputElement).value).toBe('8192');
  });

  it('可见性步:private 切回 workspace 触发 onChange', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-visibility-private'));
    await user.click(screen.getByTestId('agent-wizard-visibility-workspace'));
    expect(
      (screen.getByTestId('agent-wizard-visibility-workspace') as HTMLInputElement).checked,
    ).toBe(true);
  });

  it('创建失败(MeshApiError)映射 error.* 文案', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient({ failCreateApi: true });
    renderWizard(client);
    await user.type(screen.getByTestId('agent-wizard-name'), '小测');
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-finish'));
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect(await screen.findByTestId('agent-wizard-error')).toBeInTheDocument();
  });
});
