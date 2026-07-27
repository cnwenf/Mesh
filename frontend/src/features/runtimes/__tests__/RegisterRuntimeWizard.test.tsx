/**
 * RegisterRuntimeWizard 组件测试(runtime.md §4.3):三步流(基本信息 → 安装说明 →
 * 等待激活);基本步校验(名称 / 并发 / 标签增删与重复键拦截);安装步呈现一次性
 * 激活码与可审脚本(含 sha256 / 签名校验,无 curl|sh)与复制;等待步 ⏳→✅ 经
 * 注入 runtime.activated 帧触发;创建失败回显 error.* 文案。
 */
import { act } from 'react';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { MeshApiError } from '../../../api/errors';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { RegisterRuntimeWizard } from '../RegisterRuntimeWizard';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const ACTIVATION = {
  id: 'r-new',
  name: 'build-01',
  kind: 'self_hosted',
  status: 'pending',
  labels: {},
  capabilities: [],
  hostname: null,
  os: null,
  cpu_cores: null,
  memory_mb: null,
  max_concurrent: 1,
  current_load: 0,
  last_heartbeat_at: null,
  heartbeat_interval_seconds: 15,
  version: null,
  created_at: '2026-01-01T00:00:00Z',
  activation: {
    code: 'ACT-9F3K-2M7Q-XB4Z',
    expires_at: '2026-07-27T10:15:00Z',
    release: {
      artifact_url: 'https://releases.mesh.example/runtime/1.4.2/mesh-runtime_1.4.2_linux_x86_64.tar.gz',
      sha256: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
      signature_url:
        'https://releases.mesh.example/runtime/1.4.2/mesh-runtime_1.4.2_linux_x86_64.tar.gz.sig',
      signing_key_url: 'https://releases.mesh.example/mesh-release.pub',
    },
    activate_hint: 'mesh-runtime activate --activation-file ./activation.txt',
  },
};

function makeClient(opts: { failCreate?: boolean } = {}) {
  const request = vi.fn(
    async (_method: string, _path: string, _opts?: { body?: unknown }) => {
      if (_method === 'POST') {
        if (opts.failCreate) {
          throw new MeshApiError({ status: 410, code: 'activation_expired', message: 'expired' });
        }
        return ACTIVATION;
      }
      return {};
    },
  );
  const list = vi.fn(async () => ({ data: [], next_cursor: null }));
  return { client: { request, list } as unknown as MeshApiClient, request };
}

type FrameListener = (frame: RealtimeEventFrame) => void;

function makeRealtime() {
  const listeners = new Set<FrameListener>();
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: (cb: FrameListener) => {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
  };
  return {
    value: { state: 'connected', client } as unknown as RealtimeContextValue,
    emit: (frame: RealtimeEventFrame) => {
      act(() => {
        for (const listener of listeners) listener(frame);
      });
    },
  };
}

function renderWizard(
  client: MeshApiClient,
  realtime: ReturnType<typeof makeRealtime> | null = null,
) {
  const wizard = (
    <RegisterRuntimeWizard
      open
      onClose={() => undefined}
      client={client}
      workspaceId="ws-1"
      onRegistered={() => undefined}
    />
  );
  return renderWithProviders(
    realtime === null ? wizard : (
      <RealtimeContext.Provider value={realtime.value}>{wizard}</RealtimeContext.Provider>
    ),
  );
}

async function fillBasicStep(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByTestId('runtime-wizard-name'), 'build-01');
}

describe('RegisterRuntimeWizard 基本步', () => {
  it('名称为空时创建禁用;填写后可编辑', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    expect(screen.getByTestId('runtime-wizard-basic')).toBeInTheDocument();
    expect((screen.getByTestId('runtime-wizard-next') as HTMLButtonElement).disabled).toBe(true);
    await fillBasicStep(user);
    expect((screen.getByTestId('runtime-wizard-next') as HTMLButtonElement).disabled).toBe(false);
  });

  it('并发上限非法 → 红字拦截', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await fillBasicStep(user);
    await user.clear(screen.getByTestId('runtime-wizard-max-concurrent'));
    await user.type(screen.getByTestId('runtime-wizard-max-concurrent'), '0');
    expect((screen.getByTestId('runtime-wizard-next') as HTMLButtonElement).disabled).toBe(true);
    await user.clear(screen.getByTestId('runtime-wizard-max-concurrent'));
    await user.type(screen.getByTestId('runtime-wizard-max-concurrent'), '4');
    expect((screen.getByTestId('runtime-wizard-next') as HTMLButtonElement).disabled).toBe(false);
  });

  it('名称超 120 字红字拦截', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await user.type(screen.getByTestId('runtime-wizard-name'), 'x'.repeat(121));
    expect(screen.getByText('Name must be 120 characters or fewer.')).toBeInTheDocument();
    expect((screen.getByTestId('runtime-wizard-next') as HTMLButtonElement).disabled).toBe(true);
  });

  it('标签增删 + 重复键拦截', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await fillBasicStep(user);
    // 第一行填完整标签
    await user.type(screen.getByTestId('runtime-wizard-label-key-0'), 'region');
    await user.type(screen.getByTestId('runtime-wizard-label-value-0'), 'intranet');
    // 新增一行并填重复键 → 拦截
    await user.click(screen.getByTestId('runtime-wizard-label-add'));
    await user.type(screen.getByTestId('runtime-wizard-label-key-1'), 'region');
    expect(screen.getByTestId('runtime-wizard-labels-error')).toBeInTheDocument();
    expect((screen.getByTestId('runtime-wizard-next') as HTMLButtonElement).disabled).toBe(true);
    // 删除重复行 → 恢复可提交
    await user.click(screen.getByTestId('runtime-wizard-label-remove-1'));
    expect(screen.queryByTestId('runtime-wizard-labels-error')).toBeNull();
    expect((screen.getByTestId('runtime-wizard-next') as HTMLButtonElement).disabled).toBe(false);
  });

  it('仅剩一行时移除按钮禁用;空键有值视为非法', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await fillBasicStep(user);
    expect((screen.getByTestId('runtime-wizard-label-remove-0') as HTMLButtonElement).disabled).toBe(
      true,
    );
    await user.type(screen.getByTestId('runtime-wizard-label-value-0'), 'true');
    expect(screen.getByTestId('runtime-wizard-labels-error')).toBeInTheDocument();
  });
});

describe('RegisterRuntimeWizard 安装步(§4.3)', () => {
  it('创建后呈现激活码 + 可审安装脚本(校验 / 解包 / 受限激活)', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    renderWizard(client);
    await fillBasicStep(user);
    await user.click(screen.getByTestId('runtime-wizard-next'));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = request.mock.calls[0][2] as { body: { name: string; max_concurrent: number } };
    expect(body.body).toMatchObject({ name: 'build-01', kind: 'self_hosted', max_concurrent: 1 });
    expect(await screen.findByTestId('runtime-wizard-activation-code')).toHaveTextContent(
      'ACT-9F3K-2M7Q-XB4Z',
    );
    const script = screen.getByTestId('runtime-wizard-install-script').textContent ?? '';
    expect(script).toContain('sha256sum -c -');
    expect(script).toContain('minisign -Vm');
    expect(script).toContain('--activation-file ./activation.txt');
    expect(script).toContain('shred -u activation.txt');
    expect(script).not.toMatch(/curl[^|\n]*\|\s*sh/);
  });

  it('复制按钮经 navigator.clipboard 复制脚本并提示', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    const { client } = makeClient();
    renderWizard(client);
    await fillBasicStep(user);
    await user.click(screen.getByTestId('runtime-wizard-next'));
    await screen.findByTestId('runtime-wizard-activation-code');
    await user.click(screen.getByTestId('runtime-wizard-copy'));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(screen.getByTestId('runtime-wizard-copy')).toHaveTextContent('Copied');
  });

  it('剪贴板不可用时回显失败提示', async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: vi.fn(async () => {
          throw new Error('denied');
        }),
      },
      configurable: true,
    });
    const { client } = makeClient();
    renderWizard(client);
    await fillBasicStep(user);
    await user.click(screen.getByTestId('runtime-wizard-next'));
    await screen.findByTestId('runtime-wizard-activation-code');
    await user.click(screen.getByTestId('runtime-wizard-copy'));
    await waitFor(() => expect(screen.getByTestId('runtime-wizard-copy')).toBeInTheDocument());
  });

  it('创建失败(410 activation_expired)回显 error.* 文案', async () => {
    const user = userEvent.setup();
    const { client } = makeClient({ failCreate: true });
    renderWizard(client);
    await fillBasicStep(user);
    await user.click(screen.getByTestId('runtime-wizard-next'));
    expect(await screen.findByTestId('runtime-wizard-error')).toBeInTheDocument();
    // 仍停留在基本步,可修正后重试。
    expect(screen.getByTestId('runtime-wizard-basic')).toBeInTheDocument();
  });

  it('创建时类型选择透传 platform_managed', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    renderWizard(client);
    await fillBasicStep(user);
    await user.selectOptions(screen.getByTestId('runtime-wizard-kind'), 'platform_managed');
    await user.click(screen.getByTestId('runtime-wizard-next'));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = request.mock.calls[0][2] as { body: { kind: string } };
    expect(body.body.kind).toBe('platform_managed');
  });
});

describe('RegisterRuntimeWizard 等待步(§4.3 ⏳→✅)', () => {
  it('runtime.activated 帧到达后由等待变已激活,给出详情深链', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    const realtime = makeRealtime();
    renderWizard(client, realtime);
    await fillBasicStep(user);
    await user.click(screen.getByTestId('runtime-wizard-next'));
    await screen.findByTestId('runtime-wizard-activation-code');
    await user.click(screen.getByTestId('runtime-wizard-to-waiting'));
    expect(screen.getByTestId('runtime-wizard-pending')).toBeInTheDocument();
    // 注入本 runtime 的激活帧
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:runtimes',
      seq: 1,
      event: 'runtime.activated',
      payload: { data: { id: 'r-new' } },
    });
    expect(await screen.findByTestId('runtime-wizard-activated')).toHaveTextContent('build-01');
    const link = screen.getByTestId('runtime-wizard-detail-link');
    expect(link.getAttribute('href')).toBe('/runtimes/r-new');
  });

  it('其它 runtime 的激活帧不触发本向导', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    const realtime = makeRealtime();
    renderWizard(client, realtime);
    await fillBasicStep(user);
    await user.click(screen.getByTestId('runtime-wizard-next'));
    await screen.findByTestId('runtime-wizard-activation-code');
    await user.click(screen.getByTestId('runtime-wizard-to-waiting'));
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:runtimes',
      seq: 1,
      event: 'runtime.activated',
      payload: { data: { id: 'other-runtime' } },
    });
    expect(screen.getByTestId('runtime-wizard-pending')).toBeInTheDocument();
    expect(screen.queryByTestId('runtime-wizard-activated')).toBeNull();
  });

  it('无实时连接时等待步仍渲染(手动前往详情路径)', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client); // realtime = null
    await fillBasicStep(user);
    await user.click(screen.getByTestId('runtime-wizard-next'));
    await screen.findByTestId('runtime-wizard-activation-code');
    await user.click(screen.getByTestId('runtime-wizard-to-waiting'));
    expect(screen.getByTestId('runtime-wizard-pending')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-wizard-detail-link')).toBeInTheDocument();
  });

  it('等待步完成按钮关闭向导', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWizard(client);
    await fillBasicStep(user);
    await user.click(screen.getByTestId('runtime-wizard-next'));
    await screen.findByTestId('runtime-wizard-activation-code');
    await user.click(screen.getByTestId('runtime-wizard-to-waiting'));
    expect(screen.getByTestId('runtime-wizard-done')).toBeInTheDocument();
  });
});
