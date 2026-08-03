/**
 * 工作区功能开关(workspace.md §2.2)。
 *
 * 首个产品化键 `autopilot` 由 workspace detail 的 settings.feature_flags 下发。
 * 缺失/读取失败保持向后兼容(启用)，只有显式 boolean false 才关闭；开关只控制
 * 前端入口/路由呈现，授权与数据隔离仍由后端 RBAC 独立强制。
 */
import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { getApiClient } from '../api/instance';
import { getWorkspace } from '../api/workspace';
import type { WorkspaceSettings } from '../api/workspace';
import type { NavItemKey } from '../shell/navigation';
import { EmptyState } from '../design';
import { useT } from '../i18n';

export interface WorkspaceFeatureFlags {
  readonly autopilot: boolean;
}

export const DEFAULT_WORKSPACE_FEATURE_FLAGS: WorkspaceFeatureFlags = Object.freeze({
  autopilot: true,
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function deriveWorkspaceFeatureFlags(
  settings: Pick<WorkspaceSettings, 'feature_flags'> | { readonly feature_flags?: unknown },
): WorkspaceFeatureFlags {
  const raw = settings.feature_flags;
  if (!isRecord(raw)) return DEFAULT_WORKSPACE_FEATURE_FLAGS;
  return {
    autopilot:
      typeof raw.autopilot === 'boolean'
        ? raw.autopilot
        : DEFAULT_WORKSPACE_FEATURE_FLAGS.autopilot,
  };
}

export function isNavItemEnabled(key: NavItemKey, flags: WorkspaceFeatureFlags): boolean {
  return key !== 'autopilots' || flags.autopilot;
}

/**
 * 自动值守功能拥有的直接路由族。出向 webhook subscriptions 属于集成模块，
 * 不能被相似前缀误伤；同时识别规范工作区深链 /w/:slug/automations/autopilots。
 */
export function isAutopilotFeaturePath(pathname: string): boolean {
  return (
    pathname === '/autopilots' ||
    pathname.startsWith('/autopilots/') ||
    pathname === '/webhooks' ||
    pathname === '/automation' ||
    /^\/w\/[^/]+\/automations\/autopilots(?:\/|$)/.test(pathname)
  );
}

const WorkspaceFeatureFlagsContext = createContext<WorkspaceFeatureFlags>(
  DEFAULT_WORKSPACE_FEATURE_FLAGS,
);

export function useWorkspaceFeatureFlagsContext(): WorkspaceFeatureFlags {
  return useContext(WorkspaceFeatureFlagsContext);
}

export function WorkspaceFeatureFlagsProvider(props: {
  readonly value: WorkspaceFeatureFlags;
  readonly children: ReactNode;
}): React.JSX.Element {
  return (
    <WorkspaceFeatureFlagsContext.Provider value={props.value}>
      {props.children}
    </WorkspaceFeatureFlagsContext.Provider>
  );
}

/** 直接深链的条件路由：关闭时给出明确、可读的产品态，而不是渲染后报 API 错。 */
export function WorkspaceFeatureGate(props: {
  readonly flag: keyof WorkspaceFeatureFlags;
  readonly children: ReactNode;
}): React.JSX.Element {
  const flags = useWorkspaceFeatureFlagsContext();
  const t = useT();
  if (!flags[props.flag]) {
    return (
      <div data-testid="feature-disabled">
        <EmptyState
          title={t('workspace.featureDisabledTitle')}
          description={t('workspace.featureDisabledDescription')}
        />
      </div>
    );
  }
  return <>{props.children}</>;
}

/** 当前活跃工作区 detail → 功能开关；切换工作区时先恢复兼容默认再读取新值。 */
export function useWorkspaceFeatureFlagsValue(workspaceId: string | null): WorkspaceFeatureFlags {
  const [flags, setFlags] = useState<WorkspaceFeatureFlags>(DEFAULT_WORKSPACE_FEATURE_FLAGS);

  useEffect(() => {
    let cancelled = false;
    setFlags(DEFAULT_WORKSPACE_FEATURE_FLAGS);
    if (workspaceId === null)
      return () => {
        cancelled = true;
      };
    void getWorkspace(getApiClient(), workspaceId)
      .then((workspace) => {
        if (!cancelled) setFlags(deriveWorkspaceFeatureFlags(workspace.settings));
      })
      .catch(() => {
        // 开关不是权限边界；离线/旧后端按兼容默认继续显示，业务请求仍自行报错。
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  return flags;
}
