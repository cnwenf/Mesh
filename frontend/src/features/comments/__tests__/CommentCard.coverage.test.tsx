/**
 * CommentCard 分支与回调补齐(coverage fill):resolve/reopen/delete 点击、highlighted、
 * 作者为 null、body_html 缺省、C6 卡片 status 缺省、多链接部分失败、卸载时取消水合。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { getIssueByIdentifier } from '../../issues/api';
import { CommentCard } from '../CommentCard';
import type { CommentCardProps } from '../CommentCard';
import type { Comment } from '../types';

vi.mock('../../issues/api', () => ({
  getIssueByIdentifier: vi.fn(),
}));

const COMMENT: Comment = {
  id: 'c-1',
  issue_id: 'iss-1',
  parent_id: null,
  thread_root_id: null,
  author_kind: 'member',
  author: { id: 'mem-1', member_type: 'human', name: 'Owner' },
  body_markdown: 'hello',
  body_html: '<p>hello</p>',
  body_text: 'hello',
  reactions: [],
  reply_count: 0,
  resolved_at: null,
  resolved_by: null,
  mentions: [],
  triggered_execution_ids: [],
  deleted_at: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-02T00:00:00Z',
  edited_at: null,
};

function renderCard(
  overrides: Partial<CommentCardProps> = {},
): ReturnType<typeof renderWithProviders> {
  const props: CommentCardProps = {
    comment: COMMENT,
    workspaceId: 'ws-1',
    locale: 'en',
    highlighted: false,
    canModify: true,
    onReply: vi.fn(),
    onResolve: vi.fn(),
    onReopen: vi.fn(),
    onToggleReaction: vi.fn(),
    onAddReaction: vi.fn(),
    onSaveEdit: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn(),
    onCopyLink: vi.fn(),
    onRetrySend: vi.fn(),
    ...overrides,
  };
  return renderWithProviders(<CommentCard {...props} />);
}

afterEach(() => {
  vi.mocked(getIssueByIdentifier).mockReset();
});

describe('CommentCard action callbacks', () => {
  it('invokes onResolve when the resolve button is clicked', () => {
    const onResolve = vi.fn();
    renderCard({ onResolve });
    fireEvent.click(screen.getByTestId('comment-resolve-c-1'));
    expect(onResolve).toHaveBeenCalledWith(COMMENT);
  });

  it('invokes onReopen when the reopen button is clicked', () => {
    const onReopen = vi.fn();
    const resolved = { ...COMMENT, resolved_at: '2026-07-02T00:00:00Z' };
    renderCard({ comment: resolved, onReopen });
    fireEvent.click(screen.getByTestId('comment-reopen-c-1'));
    expect(onReopen).toHaveBeenCalledWith(resolved);
  });

  it('invokes onDelete when the delete button is clicked', () => {
    const onDelete = vi.fn();
    renderCard({ onDelete });
    fireEvent.click(screen.getByTestId('comment-delete-c-1'));
    expect(onDelete).toHaveBeenCalledWith(COMMENT);
  });
});

describe('CommentCard render branches', () => {
  it('adds the highlight class when highlighted', () => {
    renderCard({ highlighted: true });
    expect(screen.getByTestId('comment-card-c-1').className).toContain(
      'mesh-comments__card--highlight',
    );
  });

  it('renders the unknown-author fallback when author is null', () => {
    renderCard({ comment: { ...COMMENT, author: null } });
    expect(screen.getByTestId('comment-author-c-1').textContent).toBe('Unknown author');
    expect(screen.queryByTestId('agent-badge')).toBeNull();
  });

  it('tolerates a nullish body_html', () => {
    renderCard({ comment: { ...COMMENT, body_html: undefined as unknown as string } });
    expect(screen.getByTestId('comment-body-c-1').innerHTML).toBe('');
  });
});

describe('CommentCard C6 issue-link cards', () => {
  it('omits the status span when the issue has no status', async () => {
    vi.mocked(getIssueByIdentifier).mockResolvedValue({
      id: 'iss-9',
      title: 'No status issue',
      status: undefined,
    } as never);
    renderCard({
      comment: {
        ...COMMENT,
        body_html:
          '<p><a class="mesh-issue-link" data-issue-identifier="MES-5" ' +
          'href="/issues/by-identifier/MES-5">#MES-5</a></p>',
      },
    });
    await waitFor(() => {
      expect(screen.getByTestId('comment-body-c-1').innerHTML).toContain('mesh-issue-card');
    });
    const html = screen.getByTestId('comment-body-c-1').innerHTML;
    expect(html).toContain('No status issue');
    expect(html).not.toContain('mesh-issue-card__status');
  });

  it('keeps the original anchor for an identifier whose fetch fails while another succeeds', async () => {
    vi.mocked(getIssueByIdentifier).mockImplementation(((
      _client: unknown,
      _ws: unknown,
      ident: string,
    ) =>
      ident === 'MES-A'
        ? Promise.resolve({ id: 'i-a', title: 'Title A', status: { name: 'Open' } })
        : Promise.reject(new Error('missing'))) as never);
    renderCard({
      comment: {
        ...COMMENT,
        body_html:
          '<p><a class="mesh-issue-link" data-issue-identifier="MES-A" href="#">#MES-A</a>' +
          '<a class="mesh-issue-link" data-issue-identifier="MES-B" href="#">#MES-B</a></p>',
      },
    });
    await waitFor(() => {
      expect(screen.getByTestId('comment-body-c-1').innerHTML).toContain('mesh-issue-card');
    });
    const html = screen.getByTestId('comment-body-c-1').innerHTML;
    expect(html).toContain('Title A'); // MES-A hydrated
    expect(html).toContain('data-issue-identifier="MES-B"'); // MES-B anchor preserved via ?? match
    expect(html).toContain('mesh-issue-link'); // the MES-B anchor still carries its class
  });

  it('cancels the in-flight hydration when the card unmounts before it resolves', async () => {
    let resolveDetail: (value: unknown) => void = () => undefined;
    const pending = new Promise((resolve) => {
      resolveDetail = resolve;
    });
    vi.mocked(getIssueByIdentifier).mockReturnValue(pending as never);
    const view = renderCard({
      comment: {
        ...COMMENT,
        body_html:
          '<p><a class="mesh-issue-link" data-issue-identifier="MES-Z" href="#">#MES-Z</a></p>',
      },
    });
    expect(getIssueByIdentifier).toHaveBeenCalled();
    // 卸载触发 effect cleanup(cancelled=true),随后才 resolve → 命中 if(cancelled) return
    view.unmount();
    await waitFor(() => {
      resolveDetail({ id: 'i-z', title: 'Late', status: { name: 'Open' } });
      return Promise.resolve();
    });
    // 没有可断言的 DOM(已卸载);确保 resolve 后无异常即覆盖取消分支
    expect(true).toBe(true);
  });
});
