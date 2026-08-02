/**
 * 当前收件箱视图 filter 共享(§1.2 S3 命令 ⑧:标记全部已读随当前视图 filter)。
 */
import { afterEach, describe, expect, it } from 'vitest';
import { getCurrentInboxView, setCurrentInboxView } from '../currentFilter';

afterEach(() => setCurrentInboxView(null, 'all'));

describe('currentInboxView', () => {
  it('默认 all / 无工作区', () => {
    expect(getCurrentInboxView()).toEqual({ workspaceId: null, filter: 'all' });
  });

  it('InboxPage 写入 → 命令读取同一 filter', () => {
    setCurrentInboxView('ws-1', 'mentions');
    expect(getCurrentInboxView()).toEqual({ workspaceId: 'ws-1', filter: 'mentions' });
    setCurrentInboxView('ws-1', 'unread');
    expect(getCurrentInboxView().filter).toBe('unread');
  });

  it('离开收件箱(卸载清理)→ 复位', () => {
    setCurrentInboxView('ws-1', 'assigned');
    setCurrentInboxView(null, 'all');
    expect(getCurrentInboxView()).toEqual({ workspaceId: null, filter: 'all' });
  });
});
