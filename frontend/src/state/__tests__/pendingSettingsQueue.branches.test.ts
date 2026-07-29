/**
 * pendingSettingsQueue 防御分支补测(配合主测试文件):形状校验/存储异常/
 * NaN 基线/内嵌主体错配/host 不可读等降级路径,逐分支收敛。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../api/client';
import {
  clearPendingWritesForHost,
  enqueueFailedWrite,
  getActiveSubject,
  hasPendingWrites,
  replayPendingWrites,
  setActiveUser,
  setActiveWorkspace,
} from '../pendingSettingsQueue';

const dummyClient = {} as MeshApiClient;

function activeKey(): string {
  const { userId, workspaceId } = getActiveSubject();
  return `mesh.settings.pending:${window.location.host}:${userId ?? ''}:${workspaceId ?? 'none'}`;
}

beforeEach(() => {
  localStorage.clear();
  setActiveUser(null);
  setActiveWorkspace(null);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('readEntries 形状防御', () => {
  it('分区载荷非数组 → 视为空队列', () => {
    setActiveUser('u1');
    localStorage.setItem(activeKey(), JSON.stringify({ not: 'an array' }));
    expect(hasPendingWrites()).toBe(false);
  });

  it('分区载荷非法 JSON → 视为空队列', () => {
    setActiveUser('u1');
    localStorage.setItem(activeKey(), '{broken');
    expect(hasPendingWrites()).toBe(false);
  });

  it('残缺条目逐项过滤:payload/retryCount/subject/baseline 任一非法即丢弃', () => {
    setActiveUser('u1');
    const host = window.location.host;
    const subject = [host, 'u1', 'none'];
    const valid = { payload: { settings: { theme: 'dark' } }, baselineUpdatedAt: null, retryCount: 0, subject };
    const malformed = [
      valid,
      null, // 非对象
      { payload: null, baselineUpdatedAt: null, retryCount: 0, subject }, // payload 非对象
      { payload: {}, baselineUpdatedAt: null, retryCount: 'x', subject }, // retryCount 非数字
      { payload: {}, baselineUpdatedAt: null, retryCount: NaN, subject }, // retryCount 非有限数
      { payload: {}, baselineUpdatedAt: null, retryCount: 0, subject: ['a'] }, // subject 长度不足
      { payload: {}, baselineUpdatedAt: null, retryCount: 0, subject: 'a|b|c' }, // subject 非数组
      { payload: {}, baselineUpdatedAt: 42, retryCount: 0, subject }, // baseline 类型非法
    ];
    localStorage.setItem(activeKey(), JSON.stringify(malformed));
    // 仅 valid 存活:重放消费它(快照不较新 → PATCH 尝试;无网络桩 → 失败保留 retry+1)。
    const fetchSnapshot = vi.fn().mockResolvedValue({ updated_at: null, timezone: null, settings: {} });
    return replayPendingWrites(dummyClient, { fetchSnapshot }).then(() => {
      const remaining = JSON.parse(localStorage.getItem(activeKey()) ?? '[]') as unknown[];
      expect(remaining).toHaveLength(1);
      expect((remaining[0] as { retryCount: number }).retryCount).toBe(1);
    });
  });
});

describe('存储/host 异常降级', () => {
  it('localStorage.setItem 抛错 → 入队静默降级(不抛)', () => {
    setActiveUser('u1');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    expect(() => enqueueFailedWrite({ settings: { theme: 'dark' } })).not.toThrow();
  });

  it('clearPendingWritesForHost 迭代异常 → 静默降级(不抛)', () => {
    vi.spyOn(Storage.prototype, 'length', 'get').mockImplementation(() => {
      throw new Error('storage disabled');
    });
    expect(() => clearPendingWritesForHost()).not.toThrow();
  });

  // currentHost 的 try/catch(window.location.host 抛错 → 'unknown')在 jsdom 下
  // 不可触发(location.host 不可重定义),为纯环境防御分支,不经测试覆盖。
});

describe('冲突策略边界', () => {
  it('updated_at 非法日期串 → 保守重放(不误判为较新而丢弃)', async () => {
    setActiveUser('u1');
    enqueueFailedWrite({ settings: { theme: 'dark' } });
    const fetchSnapshot = vi.fn().mockResolvedValue({
      updated_at: 'not-a-date',
      timezone: null,
      settings: {},
    });
    await replayPendingWrites(dummyClient, { fetchSnapshot });
    // 条目未被「服务端较新」分支丢弃:PATCH 尝试失败后以 retryCount=1 保留。
    const remaining = JSON.parse(localStorage.getItem(activeKey()) ?? '[]') as Array<{
      retryCount: number;
    }>;
    expect(remaining).toHaveLength(1);
    expect(remaining[0].retryCount).toBe(1);
  });

  it('内嵌主体与分区键错配 → 不重放、原样保留(不迁移)', async () => {
    setActiveUser('u1');
    const foreign = {
      payload: { settings: { theme: 'dark' } },
      baselineUpdatedAt: null,
      retryCount: 0,
      subject: ['other-host', 'u9', 'none'],
    };
    localStorage.setItem(activeKey(), JSON.stringify([foreign]));
    const fetchSnapshot = vi.fn().mockResolvedValue({ updated_at: null, timezone: null, settings: {} });
    await replayPendingWrites(dummyClient, { fetchSnapshot });
    // 快照都无需取(错配条目直接跳过):条目原样保留。
    const remaining = JSON.parse(localStorage.getItem(activeKey()) ?? '[]') as unknown[];
    expect(remaining).toEqual([foreign]);
    expect(fetchSnapshot).not.toHaveBeenCalled();
  });

  it('重放期望主体含工作区维(无工作区 → none)', async () => {
    setActiveUser('u1');
    setActiveWorkspace(null);
    enqueueFailedWrite({ settings: { locale: 'zh-CN' } });
    const fetchSnapshot = vi.fn().mockResolvedValue({ updated_at: null, timezone: null, settings: {} });
    await replayPendingWrites(dummyClient, { fetchSnapshot });
    // 主体 [host,u1,none] 与期望一致 → 进入快照/PATCH 路径(PATCH 失败保留 retry+1)。
    const remaining = JSON.parse(localStorage.getItem(activeKey()) ?? '[]') as Array<{
      retryCount: number;
    }>;
    expect(fetchSnapshot).toHaveBeenCalledTimes(1);
    expect(remaining[0].retryCount).toBe(1);
  });
});
