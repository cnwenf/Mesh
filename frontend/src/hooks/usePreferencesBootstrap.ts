/**
 * 登录态偏好回填(theme.md §4.5):服务端为跨设备真源。
 *
 * 认证 shell 挂载(或登录态变化)时:
 * - `GET /me` → `hydrateFromServer`(服务端有值覆盖本地同名镜像;absent/null →
 *   偏好置 null——匿名阶段本地值不充当账号偏好,协商链自工作区默认起解析);
 * - 记录服务端 `updated_at` 基线与账号主体(pending 队列冲突策略/三元组);
 * - 注册 pending 重放触发器(online / 前台恢复)与服务端快照回填监听
 *   (重放发现服务端较新时采用服务端值)。
 * 离线/失败静默降级:本地镜像继续可用,不阻塞渲染。
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
import { useWorkspaceThemeBridge } from '../state/workspaceThemeBridge';

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
      // 全局页(设置等非工作区路由)协商链第 2 级基线:取首个所属工作区的
      // default_theme(theme.md §4.1 占位标注)。工作区路由内由 WorkspaceProvider
      // 以路由工作区值覆盖(其挂载时 beginWorkspaceLoad 置 loaded=false,
      // 此处仅在仍为基线态时写入,避免竞态覆盖路由值)。
      try {
        const list = await fetchWorkspaces(client);
        if (cancelled || list.length === 0) return;
        const detail = await getWorkspace(client, list[0].id);
        if (cancelled) return;
        setActiveWorkspace(detail.id);
        const bridge = useWorkspaceThemeBridge.getState();
        if (bridge.defaultTheme === null && bridge.loaded) {
          bridge.setWorkspaceDefault(
            typeof detail.settings?.default_theme === 'string'
              ? detail.settings.default_theme
              : null,
          );
        }
      } catch {
        /* 工作区基线不可达:协商链落系统级(降级) */
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
