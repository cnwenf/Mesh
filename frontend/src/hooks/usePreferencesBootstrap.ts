/**
 * 登录态偏好回填(theme.md §4.5):服务端为跨设备真源。
 *
 * 认证 shell 挂载(或登录态变化)时:
 * - `GET /me` → `hydrateFromServer`(服务端有值覆盖本地同名镜像;absent/null →
 *   偏好置 null——匿名阶段本地值不充当账号偏好,协商链自工作区默认起解析);
 * - 记录服务端 `updated_at` 基线与账号主体(pending 队列冲突策略/三元组);
 * - 经首个所属工作区 detail 置 pending 队列工作区主体(三元组分区);
 * - 注册 pending 重放触发器(online / 前台恢复)与服务端快照回填监听
 *   (重放发现服务端较新时采用服务端值)。
 * 离线/失败静默降级:本地镜像继续可用,不阻塞渲染。
 *
 * 工作区默认主题桥接(workspaceThemeBridge)**不由本 hook 写入**:全局页无
 * 工作区上下文 → 协商链落系统级(theme.md §2.2「已登录全局页等同 case3」),
 * 工作区路由内由 WorkspaceProvider 独占桥接写入(加载/实时更新)。
 */
import { useEffect } from 'react';
import { getApiClient } from '../api/instance';
import { fetchCurrentUserPreferences } from '../api/userPreferences';
import type { ServerUserPreferences } from '../api/userPreferences';
import { fetchWorkspaces, getWorkspace } from '../api/workspace';
import {
  SERVER_SNAPSHOT_EVENT,
  initPendingReplayTriggers,
  noteServerUpdatedAt,
  setActiveUser,
  setActiveWorkspace,
} from '../state/pendingSettingsQueue';
import { useAuthStore } from '../state/authStore';
import { useSettingsStore } from '../state/settingsStore';

function hydrateFromSnapshot(snapshot: ServerUserPreferences): void {
  noteServerUpdatedAt(snapshot.updated_at ?? null);
  useSettingsStore.getState().hydrateFromServer({
    theme: snapshot.settings?.theme ?? null,
    locale: snapshot.settings?.locale,
    timezone: snapshot.timezone,
  });
}

export function usePreferencesBootstrap(): void {
  const hasToken = useAuthStore((state) => state.token !== null);

  useEffect(() => {
    if (!hasToken) return;
    const client = getApiClient();
    let cancelled = false;
    void (async () => {
      try {
        const me = await fetchCurrentUserPreferences(client);
        if (cancelled) return;
        setActiveUser(me.id ?? null);
        hydrateFromSnapshot(me);
      } catch {
        // 离线/服务端不可达:本地镜像继续可用(降级语义,§4.5)。
        useSettingsStore.getState().markSessionProbed();
        return;
      }
      useSettingsStore.getState().markSessionProbed();
      // pending 队列工作区主体:取首个所属工作区 id(三元组分区,§2.3/§4.5)。
      // 主题协商链的工作区默认级不在此写入——全局页无工作区上下文(§2.2
      // 已登录全局页等同 case3 → system),工作区路由由 WorkspaceProvider
      // 独占桥接(beginWorkspaceLoad → detail → setWorkspaceDefault)。
      try {
        const list = await fetchWorkspaces(client);
        if (cancelled || list.length === 0) return;
        const detail = await getWorkspace(client, list[0].id);
        if (cancelled) return;
        setActiveWorkspace(detail.id);
      } catch {
        /* 工作区主体不可达:队列落 none 维(降级) */
      }
    })();
    const teardownReplay = initPendingReplayTriggers(client);
    const onSnapshot = (event: Event): void => {
      const snapshot = (event as CustomEvent<ServerUserPreferences>).detail;
      hydrateFromSnapshot(snapshot);
    };
    window.addEventListener(SERVER_SNAPSHOT_EVENT, onSnapshot);
    return () => {
      cancelled = true;
      setActiveUser(null);
      teardownReplay();
      window.removeEventListener(SERVER_SNAPSHOT_EVENT, onSnapshot);
    };
  }, [hasToken]);
}
