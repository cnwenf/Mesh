/**
 * 收藏 / recents 目标批量解析单测(§4.2.1 步骤 3 / §5.1)。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiError } from '../../../api/errors';
import { getView } from '../../board/api';
import { getChatSession } from '../../chat/api';
import { getIssue } from '../../issues/api';
import { getMember } from '../../members/api';
import { getProject } from '../../projects/api';
import { collectValidRecentKeys, resolveFavoriteTargets, resolveTarget } from '../favoritesResolve';
import type { ResolveScope } from '../favoritesResolve';
import type { FavoriteEntry } from '../types';

vi.mock('../../issues/api', () => ({ getIssue: vi.fn() }));
vi.mock('../../projects/api', () => ({ getProject: vi.fn() }));
vi.mock('../../board/api', () => ({ getView: vi.fn() }));
vi.mock('../../chat/api', () => ({ getChatSession: vi.fn() }));
vi.mock('../../members/api', () => ({ getMember: vi.fn() }));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const client = {} as any;
const SCOPE: ResolveScope = { workspaceId: 'ws-1', workspaceSlug: 'acme' };
const FLAT_SCOPE: ResolveScope = { workspaceId: 'ws-1', workspaceSlug: null };

const notFound = new MeshApiError({ status: 404, code: 'not_found', message: 'x' });
const serverErr = new MeshApiError({ status: 500, code: 'internal', message: 'x' });

function favorite(targetType: FavoriteEntry['target_type'], targetId: string): FavoriteEntry {
  return {
    id: `fav-${targetId}`,
    workspace_id: 'ws-1',
    member_id: 'm-1',
    target_type: targetType,
    target_id: targetId,
    created_at: '2026-07-01T00:00:00.000Z',
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('resolveTarget(单目标解析,§4.2.1 步骤 3)', () => {
  it('issue → 标题 + 规范 by-identifier 深链(§3.4)', async () => {
    vi.mocked(getIssue).mockResolvedValue({
      id: 'i-1',
      identifier: 'WEB-7',
      title: '崩溃',
    } as never);
    const result = await resolveTarget(client, 'issue', 'i-1', SCOPE);
    expect(result).toEqual({
      status: 'ok',
      title: '崩溃',
      url: '/w/acme/issues/by-identifier/WEB-7',
    });
  });

  it('无 slug 上下文 → 扁平深链(经迁移解析)', async () => {
    vi.mocked(getIssue).mockResolvedValue({
      id: 'i-1',
      identifier: 'WEB-7',
      title: '崩溃',
    } as never);
    const result = await resolveTarget(client, 'issue', 'i-1', FLAT_SCOPE);
    expect(result).toEqual({ status: 'ok', title: '崩溃', url: '/issues/i-1' });
  });

  it('project / view 取 name 字段', async () => {
    vi.mocked(getProject).mockResolvedValue({ id: 'p-1', name: '项目' } as never);
    expect(await resolveTarget(client, 'project', 'p-1', SCOPE)).toEqual({
      status: 'ok',
      title: '项目',
      url: '/w/acme/projects/p-1',
    });
    vi.mocked(getView).mockResolvedValue({ id: 'v-1', name: '视图' } as never);
    expect(await resolveTarget(client, 'view', 'v-1', SCOPE)).toEqual({
      status: 'ok',
      title: '视图',
      url: '/w/acme/views/v-1',
    });
  });

  it('chat_session / member / agent(member row id)取各自标题字段', async () => {
    vi.mocked(getChatSession).mockResolvedValue({ id: 'c-1', title: '会话' } as never);
    expect(await resolveTarget(client, 'chat_session', 'c-1', SCOPE)).toEqual({
      status: 'ok',
      title: '会话',
      url: '/w/acme/chat/c-1',
    });
    vi.mocked(getMember)
      .mockResolvedValueOnce({ id: 'm-1', display_name: '成员' } as never)
      .mockResolvedValueOnce({ id: 'm-agent', display_name: '助手' } as never);
    expect(await resolveTarget(client, 'member', 'm-1', SCOPE)).toEqual({
      status: 'ok',
      title: '成员',
      url: '/w/acme/members/m-1',
    });
    expect(await resolveTarget(client, 'agent', 'm-agent', SCOPE)).toEqual({
      status: 'ok',
      title: '助手',
      url: '/w/acme/members/m-agent',
    });
    expect(getMember).toHaveBeenLastCalledWith(client, 'ws-1', 'm-agent');
  });

  it('404 / not_found → missing(目标已不存在,该剪枝)', async () => {
    vi.mocked(getIssue).mockRejectedValue(notFound);
    expect(await resolveTarget(client, 'issue', 'gone', SCOPE)).toEqual({ status: 'missing' });
  });

  it('403 / forbidden → missing(目标已失权,该剪枝)', async () => {
    vi.mocked(getIssue).mockRejectedValue(
      new MeshApiError({ status: 403, code: 'forbidden', message: 'forbidden' }),
    );
    expect(await resolveTarget(client, 'issue', 'private', SCOPE)).toEqual({ status: 'missing' });
  });

  it('非 404 错误(500 / 网络)→ error(保留,不剪枝)', async () => {
    vi.mocked(getProject).mockRejectedValue(serverErr);
    expect(await resolveTarget(client, 'project', 'p-x', SCOPE)).toEqual({ status: 'error' });
    vi.mocked(getView).mockRejectedValue(new Error('network down'));
    expect(await resolveTarget(client, 'view', 'v-x', SCOPE)).toEqual({ status: 'error' });
  });
});

describe('resolveFavoriteTargets(批量,仅 ok 入映射)', () => {
  it('ok 目标入映射;missing/error 不入(收藏行不渲染)', async () => {
    vi.mocked(getIssue).mockResolvedValue({ id: 'i-1', identifier: 'K-1', title: 'T1' } as never);
    vi.mocked(getProject).mockRejectedValue(notFound);
    const map = await resolveFavoriteTargets(
      client,
      [favorite('issue', 'i-1'), favorite('project', 'p-gone')],
      SCOPE,
    );
    expect(map.size).toBe(1);
    expect(map.get('issue:i-1')).toEqual({ title: 'T1', url: '/w/acme/issues/by-identifier/K-1' });
    expect(map.has('project:p-gone')).toBe(false);
  });
});

describe('collectValidRecentKeys(§5.1 打开即清理:missing 剔除,ok/error 保留)', () => {
  it('missing 目标不保留;ok 与 error 保留', async () => {
    vi.mocked(getIssue).mockResolvedValue({ id: 'i-ok', identifier: 'K-1', title: 'T' } as never);
    vi.mocked(getProject).mockRejectedValue(notFound);
    vi.mocked(getView).mockRejectedValue(serverErr);
    const valid = await collectValidRecentKeys(
      client,
      [
        { type: 'issue', id: 'i-ok' },
        { type: 'project', id: 'p-gone' },
        { type: 'view', id: 'v-flaky' },
      ],
      SCOPE,
    );
    expect(valid.has('issue:i-ok')).toBe(true);
    expect(valid.has('view:v-flaky')).toBe(true); // error → 保留
    expect(valid.has('project:p-gone')).toBe(false); // missing → 剔除
  });
});
