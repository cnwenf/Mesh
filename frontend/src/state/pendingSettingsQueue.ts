/**
 * 偏好写失败 pending 队列(theme.md §2.3 / §4.5,评审 M5 收口)。
 *
 * 服务端同步失败**不当场回滚本地**(乐观),失败写入进入持久 pending 队列:
 * - **分区键** `mesh.settings.pending:{host}:{user_id}:{workspace_id}`
 *   (每条亦内嵌三元组);重放前校验当前活跃主体与条目三元组一致,
 *   不一致的条目**不重放**(换账号/换工作区后不得把上一主体的失败写回放到新主体);
 * - **冲突策略(服务端回填优先)**:重放前先 `GET /me` 取服务端最新
 *   `updated_at`——服务端在本次 pending 基线之后已被其他端/会话更新 →
 *   **丢弃该 pending、采用服务端值**(服务端为跨设备真源);否则重放 PATCH;
 * - 重试上限后丢弃坏条目(防无限循环);
 * - 触发:`online` 事件 / 应用前台恢复 / 下次偏好写入(经 preferencesSync)。
 *
 * 匿名写入无服务端端点(§3.1):未登录不产生队列条目。
 */
import type { MeshApiClient } from '../api/client';
import { fetchCurrentUserPreferences, updatePreferences } from '../api/userPreferences';
import type { ServerUserPreferences, UpdatePreferencesPayload } from '../api/userPreferences';

const PENDING_PREFIX = 'mesh.settings.pending:';
const MAX_RETRY_COUNT = 3;

/** 服务端快照回填事件(replay 发现服务端较新时派发,监听方 hydrate 本地) */
export const SERVER_SNAPSHOT_EVENT = 'mesh-prefs-server-snapshot';

export interface PendingEntry {
  readonly payload: UpdatePreferencesPayload;
  /** 写入尝试时已知的服务端 updated_at 基线(null = 未知,按较旧处理) */
  readonly baselineUpdatedAt: string | null;
  readonly retryCount: number;
  /** 主体三元组 [host, user_id, workspace_id] */
  readonly subject: readonly [string, string, string];
}

interface ActiveSubject {
  userId: string | null;
  workspaceId: string | null;
}

const activeSubject: ActiveSubject = { userId: null, workspaceId: null };
let lastServerUpdatedAt: string | null = null;

function currentHost(): string {
  try {
    return window.location.host;
  } catch {
    return 'unknown';
  }
}

/** 登录回填后设置当前账号主体(null = 未登录,队列不产生条目)。 */
export function setActiveUser(userId: string | null): void {
  activeSubject.userId = userId;
}

/** 进入/离开工作区上下文时设置当前工作区主体(null = 无工作区)。 */
export function setActiveWorkspace(workspaceId: string | null): void {
  activeSubject.workspaceId = workspaceId;
}

export function getActiveSubject(): Readonly<ActiveSubject> {
  return activeSubject;
}

/** 记录最近一次服务端快照的 updated_at(冲突策略基线)。 */
export function noteServerUpdatedAt(updatedAt: string | null): void {
  lastServerUpdatedAt = updatedAt;
}

function keyFor(subject: readonly [string, string, string]): string {
  return `${PENDING_PREFIX}${subject[0]}:${subject[1]}:${subject[2]}`;
}

function activeKey(): string | null {
  const { userId } = activeSubject;
  if (userId === null) return null; // 匿名无服务端端点,不入队
  return keyFor([currentHost(), userId, activeSubject.workspaceId ?? 'none']);
}

function readEntries(key: string): PendingEntry[] {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // 形状校验:毒化/残缺条目丢弃,绝不令重放崩溃(评审 LOW)。
    return parsed.filter(isWellFormedEntry) as PendingEntry[];
  } catch {
    return [];
  }
}

function isWellFormedEntry(value: unknown): boolean {
  if (typeof value !== 'object' || value === null) return false;
  const entry = value as Record<string, unknown>;
  if (typeof entry.payload !== 'object' || entry.payload === null) return false;
  if (typeof entry.retryCount !== 'number' || !Number.isFinite(entry.retryCount)) {
    return false;
  }
  if (!Array.isArray(entry.subject) || entry.subject.length !== 3) return false;
  return (
    entry.baselineUpdatedAt === null || typeof entry.baselineUpdatedAt === 'string'
  );
}

function writeEntries(key: string, entries: PendingEntry[]): void {
  try {
    if (entries.length === 0) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, JSON.stringify(entries));
    }
  } catch {
    /* 存储不可用:本地乐观值仍在 store,仅损失跨刷新重放能力 */
  }
}

/** 偏好写入失败入队(仅登录态;附当前主体三元组与基线)。 */
export function enqueueFailedWrite(payload: UpdatePreferencesPayload): void {
  const key = activeKey();
  if (key === null) return;
  const subject = [currentHost(), activeSubject.userId ?? '', activeSubject.workspaceId ?? 'none'] as const;
  const entries = readEntries(key);
  writeEntries(key, [
    ...entries,
    { payload, baselineUpdatedAt: lastServerUpdatedAt, retryCount: 0, subject },
  ]);
}

/** 当前主体队列是否有待重放条目(测试/诊断用)。 */
export function hasPendingWrites(): boolean {
  const key = activeKey();
  return key !== null && readEntries(key).length > 0;
}

function isLater(updatedAt: string | null, baseline: string | null): boolean {
  if (updatedAt === null || baseline === null) return false; // 基线未知:保守重放
  const a = new Date(updatedAt).getTime();
  const b = new Date(baseline).getTime();
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false; // 防 NaN 误判
  return a > b;
}

export interface ReplayOptions {
  /** 重放前取服务端最新快照(缺省经 GET /me) */
  fetchSnapshot?: (client: MeshApiClient) => Promise<ServerUserPreferences>;
}

/**
 * 按序重放当前主体的 pending:
 * - 主体三元组不符的条目不重放(保留于其所属分区,不迁移);
 * - 服务端较基线新 → 丢弃条目并派发 SERVER_SNAPSHOT_EVENT(采用服务端值);
 * - 否则 PATCH 重放;成功移除,失败 retryCount+1(达上限丢弃)。
 * 静默降级:网络异常中断本轮,条目保留待下次触发。
 */
export async function replayPendingWrites(
  client: MeshApiClient,
  options: ReplayOptions = {},
): Promise<void> {
  const key = activeKey();
  if (key === null) return;
  const entries = readEntries(key);
  if (entries.length === 0) return;
  const fetchSnapshot = options.fetchSnapshot ?? fetchCurrentUserPreferences;
  const expectedSubject = [currentHost(), activeSubject.userId ?? '', activeSubject.workspaceId ?? 'none'].join('|');

  let snapshot: ServerUserPreferences | null = null;
  const survivors: PendingEntry[] = [];
  for (const entry of entries) {
    if (entry.subject.join('|') !== expectedSubject) {
      survivors.push(entry); // 非当前主体:不重放(分区键正常也不会命中此分支)
      continue;
    }
    if (entry.retryCount >= MAX_RETRY_COUNT) continue; // 坏条目丢弃
    try {
      if (snapshot === null) {
        snapshot = await fetchSnapshot(client);
      }
    } catch {
      survivors.push(entry); // 快照不可达:本轮保守保留
      continue;
    }
    if (isLater(snapshot.updated_at ?? null, entry.baselineUpdatedAt)) {
      // 服务端已被其他端/会话更新 → 丢弃 pending,采用服务端值(真源优先)。
      window.dispatchEvent(
        new CustomEvent(SERVER_SNAPSHOT_EVENT, { detail: snapshot }),
      );
      continue;
    }
    try {
      await updatePreferences(client, entry.payload);
      // 重放成功:本条即最新写入,基线前移由下次快照刷新。
    } catch {
      survivors.push({ ...entry, retryCount: entry.retryCount + 1 });
    }
  }
  writeEntries(key, survivors);
}

/**
 * 注册重放触发器:`online` / 前台恢复(visibilitychange→visible)。
 * 「下次偏好写入时重放」经 preferencesSync 成功路径触发(见该模块)。
 * 返回拆卸函数。
 */
export function initPendingReplayTriggers(client: MeshApiClient): () => void {
  const flush = (): void => {
    void replayPendingWrites(client);
  };
  const onOnline = (): void => flush();
  const onVisibility = (): void => {
    if (document.visibilityState === 'visible') flush();
  };
  window.addEventListener('online', onOnline);
  document.addEventListener('visibilitychange', onVisibility);
  return () => {
    window.removeEventListener('online', onOnline);
    document.removeEventListener('visibilitychange', onVisibility);
  };
}

/** 登出清理:删除当前 host 下所有主体的 pending 分区键(防下一账号串用)。 */
export function clearPendingWritesForHost(): void {
  try {
    const hostPrefix = `${PENDING_PREFIX}${currentHost()}:`;
    const doomed: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key !== null && key.startsWith(hostPrefix)) doomed.push(key);
    }
    for (const key of doomed) localStorage.removeItem(key);
  } catch {
    /* 存储不可用即无残留可言 */
  }
}
