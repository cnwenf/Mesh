/**
 * active workspace 解析序(§3.4 写死):② 本地记忆(经成员资格校验)→
 * ③ 服务端 last_active_workspace_id → ④ 单一归属 → ⑤ null(选择页)。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import {
  lastWorkspaceStorageKey,
  readLastWorkspaceSlug,
  recordLastWorkspace,
  resolveActiveWorkspaceSlug,
} from '../lastWorkspace';

const HOST = 'mesh.example.com';

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
    removeItem: (key: string) => {
      map.delete(key);
    },
    clear: () => map.clear(),
    key: (index: number) => [...map.keys()][index] ?? null,
    get length() {
      return map.size;
    },
  };
}

const TWO_MEMBERSHIPS = [
  { workspace_id: 'ws-a', workspace_slug: 'alpha' },
  { workspace_id: 'ws-b', workspace_slug: 'beta' },
];

describe('lastWorkspace(§3.4 active workspace 解析序)', () => {
  let storage: Storage;
  beforeEach(() => {
    storage = memoryStorage();
  });

  it('存储键按 host + user 隔离(防跨部署/跨账号串用)', () => {
    expect(lastWorkspaceStorageKey(HOST, 'u1')).toBe('mesh.last_workspace:' + HOST + ':u1');
    expect(lastWorkspaceStorageKey(HOST, 'u2')).not.toBe(lastWorkspaceStorageKey(HOST, 'u1'));
  });

  it('record/read 往返', () => {
    recordLastWorkspace('u1', 'alpha', storage, HOST);
    expect(readLastWorkspaceSlug('u1', storage, HOST)).toBe('alpha');
    expect(readLastWorkspaceSlug('u2', storage, HOST)).toBeNull();
  });

  it('解析序 ②:本地记忆 slug 经成员资格校验命中', () => {
    recordLastWorkspace('u1', 'beta', storage, HOST);
    const slug = resolveActiveWorkspaceSlug({
      memberships: TWO_MEMBERSHIPS,
      userId: 'u1',
      lastActiveWorkspaceId: 'ws-a',
      storage,
      host: HOST,
    });
    // 本地记忆优先于服务端提示。
    expect(slug).toBe('beta');
  });

  it('解析序 ②:记忆 slug 已退区/改名 → 校验失败落后续级', () => {
    recordLastWorkspace('u1', 'retired', storage, HOST);
    const slug = resolveActiveWorkspaceSlug({
      memberships: TWO_MEMBERSHIPS,
      userId: 'u1',
      lastActiveWorkspaceId: 'ws-a',
      storage,
      host: HOST,
    });
    expect(slug).toBe('alpha');
  });

  it('解析序 ③:无本地记忆 → 服务端 last_active_workspace_id 匹配成员资格', () => {
    const slug = resolveActiveWorkspaceSlug({
      memberships: TWO_MEMBERSHIPS,
      userId: 'u1',
      lastActiveWorkspaceId: 'ws-b',
      storage,
      host: HOST,
    });
    expect(slug).toBe('beta');
  });

  it('解析序 ③:服务端提示 id 不在成员资格内 → 落 ④', () => {
    const slug = resolveActiveWorkspaceSlug({
      memberships: TWO_MEMBERSHIPS,
      userId: 'u1',
      lastActiveWorkspaceId: 'ws-foreign',
      storage,
      host: HOST,
    });
    // 多工作区且服务端提示无效 → 选择页。
    expect(slug).toBeNull();
  });

  it('解析序 ④:所属恰一个工作区 → 直接采用', () => {
    const slug = resolveActiveWorkspaceSlug({
      memberships: [TWO_MEMBERSHIPS[0]],
      userId: 'u1',
      lastActiveWorkspaceId: null,
      storage,
      host: HOST,
    });
    expect(slug).toBe('alpha');
  });

  it('解析序 ⑤:多工作区无线索 → null(工作区选择页)', () => {
    const slug = resolveActiveWorkspaceSlug({
      memberships: TWO_MEMBERSHIPS,
      userId: 'u1',
      lastActiveWorkspaceId: null,
      storage,
      host: HOST,
    });
    expect(slug).toBeNull();
  });

  it('零工作区 → null', () => {
    const slug = resolveActiveWorkspaceSlug({
      memberships: [],
      userId: 'u1',
      storage,
      host: HOST,
    });
    expect(slug).toBeNull();
  });
});
