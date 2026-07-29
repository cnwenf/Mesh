/**
 * pending 偏好队列(theme.md §2.3 / §4.5):分区键 + 三元组校验重放 +
 * 服务端优先冲突策略 + 重试上限 + 登出清理。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../api/client';
import type { ServerUserPreferences } from '../../api/userPreferences';
import {
  SERVER_SNAPSHOT_EVENT,
  clearPendingWritesForHost,
  enqueueFailedWrite,
  getActiveSubject,
  hasPendingWrites,
  initPendingReplayTriggers,
  noteServerUpdatedAt,
  replayPendingWrites,
  setActiveUser,
  setActiveWorkspace,
} from '../pendingSettingsQueue';

function mockClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({ baseUrl: 'http://localhost:8901', getToken: () => 't', fetchImpl });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const HOST = window.location.host;

function snapshot(updatedAt: string | null): ServerUserPreferences {
  return { id: 'u-1', updated_at: updatedAt ?? undefined, timezone: 'UTC', settings: {} };
}

beforeEach(() => {
  localStorage.clear();
  setActiveUser(null);
  setActiveWorkspace(null);
  noteServerUpdatedAt(null);
});

afterEach(() => {
  localStorage.clear();
});

describe('enqueueFailedWrite — 分区入队', () => {
  it('未登录(匿名)不入队(匿名写入无服务端端点)', () => {
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    expect(localStorage.length).toBe(0);
  });

  it('登录态入队:分区键含三元组,条目内嵌三元组与基线', () => {
    setActiveUser('u-1');
    setActiveWorkspace('w-1');
    noteServerUpdatedAt('2026-07-29T00:00:00Z');
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    const key = `mesh.settings.pending:${HOST}:u-1:w-1`;
    const entries = JSON.parse(localStorage.getItem(key) ?? '[]') as Array<{
      subject: string[];
      baselineUpdatedAt: string | null;
      retryCount: number;
    }>;
    expect(entries).toHaveLength(1);
    expect(entries[0].subject).toEqual([HOST, 'u-1', 'w-1']);
    expect(entries[0].baselineUpdatedAt).toBe('2026-07-29T00:00:00Z');
    expect(entries[0].retryCount).toBe(0);
    expect(hasPendingWrites()).toBe(true);
  });

  it('无工作区上下文 → workspace 维为 none', () => {
    setActiveUser('u-1');
    enqueueFailedWrite({ timezone: 'UTC' });
    expect(localStorage.getItem(`mesh.settings.pending:${HOST}:u-1:none`)).not.toBeNull();
  });
});

describe('replayPendingWrites — 重放与冲突策略', () => {
  it('主体匹配 + 服务端未变更 → PATCH 重放并清键', async () => {
    setActiveUser('u-1');
    setActiveWorkspace('w-1');
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ data: {} })) as unknown as typeof fetch;
    const client = mockClient(fetchImpl);

    await replayPendingWrites(client, {
      fetchSnapshot: async () => snapshot('2026-07-29T00:00:00Z'),
    });

    expect(fetchImpl).toHaveBeenCalledTimes(1); // PATCH(快照经注入,无 GET)
    expect(localStorage.getItem(`mesh.settings.pending:${HOST}:u-1:w-1`)).toBeNull();
  });

  it('服务端较基线新 → 丢弃 pending、派发服务端快照事件(真源优先)', async () => {
    setActiveUser('u-1');
    noteServerUpdatedAt('2026-07-29T00:00:00Z');
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ data: {} })) as unknown as typeof fetch;
    const client = mockClient(fetchImpl);
    const listener = vi.fn();
    window.addEventListener(SERVER_SNAPSHOT_EVENT, listener);

    await replayPendingWrites(client, {
      fetchSnapshot: async () => snapshot('2026-07-29T08:00:00Z'), // 其他端已更新
    });

    expect(fetchImpl).not.toHaveBeenCalled(); // 不重放旧值
    expect(listener).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(`mesh.settings.pending:${HOST}:u-1:none`)).toBeNull();
    window.removeEventListener(SERVER_SNAPSHOT_EVENT, listener);
  });

  it('PATCH 重放失败 → retryCount+1 保留;达上限丢弃', async () => {
    setActiveUser('u-2');
    enqueueFailedWrite({ settings: { locale: 'zh-CN' } });
    const failFetch = vi.fn().mockRejectedValue(new Error('net')) as unknown as typeof fetch;
    const client = mockClient(failFetch);
    const fetchSnapshot = async (): Promise<ServerUserPreferences> => snapshot(null);

    for (let i = 0; i < 3; i += 1) {
      await replayPendingWrites(client, { fetchSnapshot });
    }
    const key = `mesh.settings.pending:${HOST}:u-2:none`;
    let entries = JSON.parse(localStorage.getItem(key) ?? '[]') as Array<{ retryCount: number }>;
    expect(entries[0]?.retryCount).toBe(3);

    await replayPendingWrites(client, { fetchSnapshot }); // 第 4 轮:达上限丢弃
    entries = JSON.parse(localStorage.getItem(key) ?? '[]') as Array<{ retryCount: number }>;
    expect(entries).toHaveLength(0);
  });

  it('快照不可达 → 保守保留条目(不误丢)', async () => {
    setActiveUser('u-3');
    enqueueFailedWrite({ settings: { theme: 'light' } });
    const client = mockClient(vi.fn() as unknown as typeof fetch);
    await replayPendingWrites(client, {
      fetchSnapshot: async () => {
        throw new Error('snapshot down');
      },
    });
    expect(hasPendingWrites()).toBe(true);
  });

  it('换账号后新主体不重放上一主体的失败写(三元组隔离)', async () => {
    setActiveUser('u-old');
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    // 登出/换账号:主体切换
    setActiveUser('u-new');
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ data: {} })) as unknown as typeof fetch;
    const client = mockClient(fetchImpl);
    await replayPendingWrites(client, { fetchSnapshot: async () => snapshot(null) });
    expect(fetchImpl).not.toHaveBeenCalled();
    // 旧主体分区仍在(不被新主体重放),新主体无队列
    expect(localStorage.getItem(`mesh.settings.pending:${HOST}:u-old:none`)).not.toBeNull();
    expect(hasPendingWrites()).toBe(false);
  });
});

describe('触发器与清理', () => {
  it('online 事件触发重放', async () => {
    setActiveUser('u-4');
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ data: {} })) as unknown as typeof fetch;
    const client = mockClient(fetchImpl);
    const teardown = initPendingReplayTriggers(client);
    window.dispatchEvent(new Event('online'));
    await vi.waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    teardown();
  });

  it('getActiveSubject 反映当前主体', () => {
    setActiveUser('u-5');
    setActiveWorkspace('w-9');
    expect(getActiveSubject()).toEqual({ userId: 'u-5', workspaceId: 'w-9' });
  });

  it('clearPendingWritesForHost 删除当前 host 全部分区键', () => {
    localStorage.setItem(`mesh.settings.pending:${HOST}:u-a:none`, '[]');
    localStorage.setItem(`mesh.settings.pending:${HOST}:u-b:w-1`, '[]');
    localStorage.setItem('mesh.settings.pending:other.host:u-c:none', '[]');
    localStorage.setItem('mesh.settings.v1', '{}');
    clearPendingWritesForHost();
    expect(localStorage.getItem(`mesh.settings.pending:${HOST}:u-a:none`)).toBeNull();
    expect(localStorage.getItem(`mesh.settings.pending:${HOST}:u-b:w-1`)).toBeNull();
    expect(localStorage.getItem('mesh.settings.pending:other.host:u-c:none')).not.toBeNull();
    expect(localStorage.getItem('mesh.settings.v1')).not.toBeNull();
  });
});
