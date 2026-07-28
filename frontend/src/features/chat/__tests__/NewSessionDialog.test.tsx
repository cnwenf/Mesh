/**
 * NewSessionDialog 测试(chat-session.md §4.1):打开拉 agent、选 agent 才可创建、
 * issue 上下文搜索 + 选择 + 清除、可选项目上下文选择、创建回调携带 issue/project 上下文、
 * 加载失败态。经路由式 fetch 桩按端点应答(agents / projects / issues)。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { NewSessionDialog } from '../NewSessionDialog';

const agent = { id: 'a-1', display_name: 'Builder', name: 'builder' };
const issue = { id: 'iss-1', identifier: 'WEB-1', title: 'Bug' };
const project = { id: 'prj-1', name: 'Atlas', key: 'ATL' };

let client: MeshApiClient;

afterEach(() => vi.unstubAllGlobals());

interface Recorder {
  calls: string[];
}

function stubApi(router: (url: string) => Response | null, recorder?: Recorder): void {
  const fetchImpl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    recorder?.calls.push(url);
    const response = router(url);
    if (response !== null) return response;
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', fetchImpl);
}

/** 默认路由:agents + projects 正常,issues 空。 */
function defaultRouter(
  overrides: { agents?: Response; projects?: Response; issues?: Response } = {},
) {
  return (url: string): Response | null => {
    if (url.includes('/agents'))
      return overrides.agents ?? fakeResponse({ body: { data: [agent], next_cursor: null } });
    if (url.includes('/projects'))
      return overrides.projects ?? fakeResponse({ body: { data: [project], next_cursor: null } });
    if (url.includes('/issues'))
      return overrides.issues ?? fakeResponse({ body: { data: [], next_cursor: null } });
    return null;
  };
}

function renderDialog(props: Partial<React.ComponentProps<typeof NewSessionDialog>> = {}) {
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
  return renderWithProviders(
    <NewSessionDialog
      open
      client={client}
      workspaceId="ws-1"
      onClose={vi.fn()}
      onCreate={vi.fn().mockResolvedValue(undefined)}
      {...props}
    />,
  );
}

describe('NewSessionDialog(§4.1)', () => {
  it('打开时拉取 agent 列表填充下拉', async () => {
    stubApi(defaultRouter());
    renderDialog();
    expect(await screen.findByTestId('chat-new-session-agent')).toBeInTheDocument();
    expect(screen.getByText('Builder')).toBeInTheDocument();
  });

  it('未选 agent 时禁用创建', async () => {
    stubApi(defaultRouter());
    renderDialog();
    await screen.findByTestId('chat-new-session-agent');
    expect(screen.getByTestId('chat-new-session-create')).toBeDisabled();
  });

  it('选 agent 后创建回调携带 null issue/project 上下文', async () => {
    const user = userEvent.setup();
    stubApi(defaultRouter());
    const onCreate = vi.fn().mockResolvedValue(undefined);
    renderDialog({ onCreate });
    await user.selectOptions(await screen.findByTestId('chat-new-session-agent'), 'a-1');
    expect(screen.getByTestId('chat-new-session-create')).toBeEnabled();
    await user.click(screen.getByTestId('chat-new-session-create'));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('a-1', null, null));
  });

  it('issue 搜索 → 选择 → 创建携带 context_issue_id', async () => {
    const user = userEvent.setup();
    stubApi(
      defaultRouter({ issues: fakeResponse({ body: { data: [issue], next_cursor: null } }) }),
    );
    const onCreate = vi.fn().mockResolvedValue(undefined);
    renderDialog({ onCreate });
    await user.selectOptions(await screen.findByTestId('chat-new-session-agent'), 'a-1');
    await user.type(screen.getByTestId('chat-new-session-context'), 'Bug');
    await screen.findByTestId('chat-context-results');
    await user.click(screen.getByTestId('chat-context-option-iss-1'));
    expect(screen.getByTestId('chat-context-selected')).toBeInTheDocument();
    await user.click(screen.getByTestId('chat-new-session-create'));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('a-1', 'iss-1', null));
  });

  it('项目选择 → 创建携带 context_project_id', async () => {
    const user = userEvent.setup();
    stubApi(defaultRouter());
    const onCreate = vi.fn().mockResolvedValue(undefined);
    renderDialog({ onCreate });
    await user.selectOptions(await screen.findByTestId('chat-new-session-agent'), 'a-1');
    // 项目下拉已填充(打开时拉取)
    await user.selectOptions(await screen.findByTestId('chat-new-session-project'), 'prj-1');
    await user.click(screen.getByTestId('chat-new-session-create'));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('a-1', null, 'prj-1'));
  });

  it('项目拉取失败不阻断创建(退化为无项目)', async () => {
    const user = userEvent.setup();
    stubApi(
      defaultRouter({
        projects: fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        }),
      }),
    );
    const onCreate = vi.fn().mockResolvedValue(undefined);
    renderDialog({ onCreate });
    await user.selectOptions(await screen.findByTestId('chat-new-session-agent'), 'a-1');
    await user.click(screen.getByTestId('chat-new-session-create'));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('a-1', null, null));
  });

  it('选择上下文后可清除', async () => {
    const user = userEvent.setup();
    stubApi(
      defaultRouter({ issues: fakeResponse({ body: { data: [issue], next_cursor: null } }) }),
    );
    renderDialog();
    await user.selectOptions(await screen.findByTestId('chat-new-session-agent'), 'a-1');
    await user.type(screen.getByTestId('chat-new-session-context'), 'Bug');
    await screen.findByTestId('chat-context-results');
    await user.click(screen.getByTestId('chat-context-option-iss-1'));
    await user.click(screen.getByTestId('chat-context-clear'));
    expect(screen.queryByTestId('chat-context-selected')).toBeNull();
  });

  it('空查询不触发搜索并清空结果', async () => {
    const user = userEvent.setup();
    const recorder: Recorder = { calls: [] };
    stubApi(defaultRouter(), recorder);
    renderDialog();
    await screen.findByTestId('chat-new-session-agent');
    const input = screen.getByTestId('chat-new-session-context');
    await user.type(input, ' ');
    await user.clear(input);
    expect(screen.queryByTestId('chat-context-results')).toBeNull();
    expect(recorder.calls.some((url) => url.includes('/issues'))).toBe(false);
  });

  it('agent 加载失败呈现错误态', async () => {
    stubApi(
      defaultRouter({
        agents: fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        }),
      }),
    );
    renderDialog();
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-new-session-agent')).toBeNull();
  });

  it('issue 搜索失败时清空结果(不崩溃)', async () => {
    const user = userEvent.setup();
    stubApi(
      defaultRouter({
        issues: fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        }),
      }),
    );
    renderDialog();
    await user.selectOptions(await screen.findByTestId('chat-new-session-agent'), 'a-1');
    await user.type(screen.getByTestId('chat-new-session-context'), 'Bug');
    await waitFor(() => expect(screen.queryByTestId('chat-context-results')).toBeNull());
  });

  it('创建失败 toast 但不抛出(对话框保持)', async () => {
    const user = userEvent.setup();
    stubApi(defaultRouter());
    const onCreate = vi.fn().mockRejectedValue(new Error('nope'));
    renderDialog({ onCreate });
    await user.selectOptions(await screen.findByTestId('chat-new-session-agent'), 'a-1');
    await user.click(screen.getByTestId('chat-new-session-create'));
    await waitFor(() => expect(onCreate).toHaveBeenCalled());
    expect(screen.getByTestId('chat-new-session')).toBeInTheDocument();
  });
});
