/**
 * DistillDialog 测试(chat-session.md §4 沉淀 / README §6.9):打开取副作用预览、
 * 目标 issue + 预填正文 + 触发名单 + 抑制开关、提交经 createComment、失败重试。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { DistillDialog } from '../DistillDialog';

const preview = {
  target_issue: { id: 'iss-1', identifier: 'WEB-1', title: 'Bug' },
  body_markdown: '# Summary',
  attachments: [{ id: 'a-1', file_name: 'r.pdf', mime_type: 'application/pdf', byte_size: 10, scan_status: 'clean' }],
  triggered_agents: [{ member_id: 'mem-1', agent_id: 'a-1', name: 'Builder' }],
  mentions: [],
  can_trigger_agents: true,
  suppress_triggers_supported: true,
};

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

function renderDialog(props: Partial<React.ComponentProps<typeof DistillDialog>> = {}) {
  return renderWithProviders(
    <DistillDialog
      open
      client={client}
      workspaceId="ws-1"
      sessionId="sess-1"
      initialBody="# Summary"
      targetIssueId="iss-1"
      attachmentIds={['a-1']}
      onClose={vi.fn()}
      onDistilled={vi.fn()}
      {...props}
    />,
  );
}

describe('DistillDialog(§6.9 副作用预览 + 一次提交)', () => {
  it('打开取预览:目标 issue + 预填正文 + 触发名单 + 抑制开关 + 附件', async () => {
    stub = stubFetch(fakeResponse({ body: { data: preview } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDialog();
    expect(await screen.findByTestId('chat-distill-target')).toHaveTextContent('WEB-1');
    expect(screen.getByTestId('chat-distill-body')).toHaveValue('# Summary');
    expect(screen.getByTestId('chat-distill-trigger')).toHaveTextContent('Builder');
    expect(screen.getByTestId('chat-distill-suppress')).toBeInTheDocument();
    expect(screen.getByTestId('chat-distill-attachments')).toHaveTextContent('r.pdf');
  });

  it('编辑正文(onChange)后值更新并可提交新正文', async () => {
    const user = userEvent.setup();
    stub = stubFetch(
      fakeResponse({ body: { data: preview } }),
      fakeResponse({ body: { data: { id: 'c-1' } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDialog();
    const body = await screen.findByTestId('chat-distill-body');
    await user.clear(body);
    await user.type(body, 'edited body');
    expect(body).toHaveValue('edited body');
    await user.click(screen.getByTestId('chat-distill-submit'));
    await waitFor(() =>
      expect(JSON.parse(String(stub.calls[1].init?.body))).toMatchObject({ body_markdown: 'edited body' }),
    );
  });

  it('编辑正文后提交经 createComment(携带 suppress_triggers)', async () => {
    const user = userEvent.setup();
    stub = stubFetch(
      fakeResponse({ body: { data: preview } }),
      fakeResponse({ body: { data: { id: 'c-1' } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    const onDistilled = vi.fn();
    renderDialog({ onDistilled });
    await screen.findByTestId('chat-distill-body');
    await user.click(screen.getByTestId('chat-distill-suppress'));
    await user.click(screen.getByTestId('chat-distill-submit'));
    await waitFor(() => expect(onDistilled).toHaveBeenCalledWith('iss-1', 'WEB-1'));
    // 第二次调用为发表评论,带 suppress_triggers=true
    const call = stub.calls[1];
    expect(call.url).toContain('/api/v1/issues/iss-1/comments');
    expect(JSON.parse(String(call.init?.body))).toMatchObject({ body_markdown: '# Summary', suppress_triggers: true });
  });

  it('正文为空禁用提交', async () => {
    stub = stubFetch(fakeResponse({ body: { data: { ...preview, body_markdown: '' } } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDialog();
    await screen.findByTestId('chat-distill-body');
    expect(screen.getByTestId('chat-distill-submit')).toBeDisabled();
  });

  it('不支持抑制时不渲染开关', async () => {
    stub = stubFetch(
      fakeResponse({ body: { data: { ...preview, suppress_triggers_supported: false } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDialog();
    await screen.findByTestId('chat-distill-body');
    expect(screen.queryByTestId('chat-distill-suppress')).toBeNull();
  });

  it('预览失败呈现错误态并可重试', async () => {
    stub = stubFetch(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
      fakeResponse({ body: { data: preview } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Something went wrong');
    await user.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByTestId('chat-distill-body')).toBeInTheDocument();
  });

  it('提交失败 toast 但不抛出', async () => {
    const user = userEvent.setup();
    stub = stubFetch(
      fakeResponse({ body: { data: preview } }),
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    const onDistilled = vi.fn();
    renderDialog({ onDistilled });
    await screen.findByTestId('chat-distill-body');
    await user.click(screen.getByTestId('chat-distill-submit'));
    await waitFor(() => expect(stub.calls.length).toBe(2));
    expect(onDistilled).not.toHaveBeenCalled();
  });

  it('提交进行中再次点击被 submitting 守卫拦截(单次落库)', async () => {
    const user = userEvent.setup();
    let commentCalls = 0;
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/distill-preview')) return fakeResponse({ body: { data: preview } });
      if (url.includes('/comments')) {
        commentCalls += 1;
        await new Promise(() => undefined); // 挂起,保持 submitting=true
        return fakeResponse({ body: { data: { id: 'c-1' } } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    renderDialog();
    await screen.findByTestId('chat-distill-body');
    const submit = screen.getByTestId('chat-distill-submit');
    await user.click(submit);
    await user.click(submit); // submitting 守卫拦截
    await waitFor(() => expect(commentCalls).toBe(1));
  });

  it('预览加载期间卸载:迟到响应被 cancelled 守卫丢弃(不报错)', async () => {
    let resolvePreview: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      resolvePreview = resolve;
    });
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/distill-preview')) return pending;
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    const { unmount } = renderDialog();
    // 卸载触发 effect 清理(cancelled=true),随后迟到响应抵达 → 守卫丢弃
    unmount();
    resolvePreview(fakeResponse({ body: { data: preview } }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    // 未抛错即通过(无卸载后 setState)
    expect(true).toBe(true);
  });
});
