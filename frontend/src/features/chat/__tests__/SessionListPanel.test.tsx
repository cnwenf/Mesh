/**
 * SessionListPanel 测试(chat-session.md §4.1):置顶/最近分组、选中、pin 切换、
 * agent/状态过滤、新建入口、空/错误/加载态、预览与相对时间。
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import type { AgentSummary } from '../../agents/types';
import { SessionListPanel } from '../SessionListPanel';
import type { ChatSession } from '../types';

function makeSession(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    id: 'sess-1',
    workspace_id: 'ws-1',
    owner_id: 'u-1',
    agent_id: 'a-1',
    agent: { id: 'a-1', name: 'Bot', avatar_url: null },
    title: 'First chat',
    title_is_auto: true,
    context_issue_id: null,
    context_project_id: null,
    status: 'active',
    pinned: false,
    last_message_at: '2026-07-01T00:00:00Z',
    last_message_preview: 'hi there',
    message_count: 3,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

const agents: AgentSummary[] = [
  {
    id: 'a-1',
    member: null,
    display_name: 'Builder',
    name: 'builder',
    avatar_url: null,
    role_tag: null,
    badge_kind: 'agent',
    lifecycle_status: 'active',
    visibility: 'workspace',
    trigger_on_assign: false,
    owner_user_id: 'u-1',
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  },
];

function renderPanel(props: Partial<React.ComponentProps<typeof SessionListPanel>> = {}) {
  return renderWithProviders(
    <SessionListPanel
      sessions={[makeSession()]}
      selectedId={null}
      locale="en"
      agents={agents}
      agentFilter=""
      statusFilter="active"
      isLoading={false}
      error={null}
      onAgentFilterChange={vi.fn()}
      onStatusFilterChange={vi.fn()}
      onSelect={vi.fn()}
      onTogglePin={vi.fn()}
      onNewSession={vi.fn()}
      onRetry={vi.fn()}
      {...props}
    />,
  );
}

describe('SessionListPanel(§4.1)', () => {
  it('渲染会话行 + 预览 + 相对时间', () => {
    renderPanel();
    expect(screen.getByTestId('chat-session-sess-1')).toBeInTheDocument();
    expect(screen.getByTestId('chat-session-preview-sess-1')).toHaveTextContent('hi there');
    expect(screen.getByText('First chat')).toBeInTheDocument();
  });

  it('置顶会话进入 Pinned 分组', () => {
    renderPanel({
      sessions: [makeSession({ id: 's-pin', pinned: true }), makeSession({ id: 's-rec' })],
    });
    expect(screen.getByText('Pinned')).toBeInTheDocument();
    expect(screen.getByText('Recent')).toBeInTheDocument();
  });

  it('点击行触发 onSelect', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const session = makeSession();
    renderPanel({ sessions: [session], onSelect });
    await user.click(screen.getByText('First chat'));
    expect(onSelect).toHaveBeenCalledWith(session);
  });

  it('pin 按钮触发 onTogglePin', async () => {
    const user = userEvent.setup();
    const onTogglePin = vi.fn();
    renderPanel({ onTogglePin });
    await user.click(screen.getByTestId('chat-session-pin-sess-1'));
    expect(onTogglePin).toHaveBeenCalledTimes(1);
  });

  it('新建按钮触发 onNewSession', async () => {
    const user = userEvent.setup();
    const onNewSession = vi.fn();
    renderPanel({ onNewSession });
    await user.click(screen.getByTestId('chat-new-session'));
    expect(onNewSession).toHaveBeenCalledTimes(1);
  });

  it('agent 过滤变更回调', async () => {
    const user = userEvent.setup();
    const onAgentFilterChange = vi.fn();
    renderPanel({ onAgentFilterChange });
    await user.selectOptions(screen.getByTestId('chat-filter-agent'), 'a-1');
    expect(onAgentFilterChange).toHaveBeenCalledWith('a-1');
  });

  it('状态过滤变更回调', async () => {
    const user = userEvent.setup();
    const onStatusFilterChange = vi.fn();
    renderPanel({ onStatusFilterChange });
    await user.selectOptions(screen.getByTestId('chat-filter-status'), 'archived');
    expect(onStatusFilterChange).toHaveBeenCalledWith('archived');
  });

  it('空列表呈现 onboarding 四要素空态 + 新建入口(onboarding.md §1.2.2)', async () => {
    const user = userEvent.setup();
    const onNewSession = vi.fn();
    renderPanel({ sessions: [], onNewSession });
    // 插画 + 引导文案 + 主操作深链既有发起会话入口
    expect(screen.getByTestId('illustration-chat')).toBeInTheDocument();
    expect(screen.getByText('No conversations yet')).toBeInTheDocument();
    expect(screen.getByText('Start a chat')).toBeInTheDocument();
    await user.click(screen.getByTestId('chat-empty-new'));
    expect(onNewSession).toHaveBeenCalledTimes(1);
  });

  it('错误态触发 onRetry', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    renderPanel({ error: 'error.internal_error', onRetry });
    await user.click(screen.getByRole('button', { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('加载态渲染骨架', () => {
    renderPanel({ isLoading: true });
    // 骨架 sr-only 加载文案(toast region 亦为 role=status,故按文案断言)。
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('无预览时回退占位文案', () => {
    renderPanel({ sessions: [makeSession({ last_message_preview: null })] });
    expect(screen.getByTestId('chat-session-preview-sess-1').textContent).not.toBe('');
  });

  it('渲染会话行 agent 头像(占位首字母)', () => {
    renderPanel();
    expect(screen.getByTestId('chat-session-avatar-sess-1')).toHaveTextContent('B');
  });

  it('搜索按标题大小写不敏感过滤(§4.1)', async () => {
    const user = userEvent.setup();
    renderPanel({
      sessions: [
        makeSession({ id: 's1', title: 'Alpha' }),
        makeSession({ id: 's2', title: 'Beta' }),
      ],
    });
    await user.type(screen.getByTestId('chat-session-search'), 'alph');
    expect(screen.getByTestId('chat-session-s1')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-session-s2')).toBeNull();
  });

  it('搜索按 agent 名称过滤', async () => {
    const user = userEvent.setup();
    renderPanel({
      sessions: [
        makeSession({
          id: 's1',
          title: 'X',
          agent: { id: 'a-1', name: 'Builder', avatar_url: null },
        }),
        makeSession({
          id: 's2',
          title: 'Y',
          agent: { id: 'a-2', name: 'Reviewer', avatar_url: null },
        }),
      ],
    });
    await user.type(screen.getByTestId('chat-session-search'), 'review');
    expect(screen.queryByTestId('chat-session-s1')).toBeNull();
    expect(screen.getByTestId('chat-session-s2')).toBeInTheDocument();
  });

  it('空查询不过滤(全部展示)', async () => {
    const user = userEvent.setup();
    renderPanel({
      sessions: [
        makeSession({ id: 's1', title: 'Alpha' }),
        makeSession({ id: 's2', title: 'Beta' }),
      ],
    });
    const input = screen.getByTestId('chat-session-search');
    await user.type(input, 'zzz');
    await user.clear(input);
    expect(screen.getByTestId('chat-session-s1')).toBeInTheDocument();
    expect(screen.getByTestId('chat-session-s2')).toBeInTheDocument();
  });

  it('搜索无命中呈现搜索结果空态', async () => {
    const user = userEvent.setup();
    renderPanel({ sessions: [makeSession({ id: 's1', title: 'Alpha' })] });
    await user.type(screen.getByTestId('chat-session-search'), 'no-match');
    expect(screen.getByTestId('chat-search-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-session-s1')).toBeNull();
  });
});
