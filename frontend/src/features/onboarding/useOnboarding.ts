/**
 * 上手清单状态钩子(onboarding.md §3/§4.5)。
 *
 * - 工作区/成员派生与收件箱同口径(useInboxContext:fetchMe → activeWorkspace(首个成员身份)
 *   → 名册匹配 members.id),额外回传 workspace_slug 供步骤 1 CTA 深链 /w/{slug}/settings;
 * - 实时:经 useRealtimeContext() 订阅 member:{member_id}:onboarding,本频道任何
 *   onboarding.progress/completed 帧 → 整拉 GET state(进度真源在数据库,§3.7);
 * - 降级:实时上下文为 null(shell 外)时 30s 轮询 GET state(§3.7 功能等价);
 * - 写操作(dismiss/restore/completeStep)后一律重拉——数据库是唯一真源;
 * - 空状态主操作完成的乐观推进(§1.2.2):本地即时置位 + POST 手动完成 + 失败回滚,
 *   领域事件经服务端完成守卫复核收敛。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, errorToI18nKey, getToken } from '../../api';
import { MeshApiError } from '../../api/errors';
import { env } from '../../env';
import { useRealtimeContext } from '../../shell/AppShell';
import { useAuthStore } from '../../state/authStore';
import { activeWorkspace, fetchMe, listMembers } from '../members/api';
import { listIssues } from '../issues/api';
import type { HumanProfile } from '../members/types';
import { onOnboardingExternalChange, onStepOptimisticRequest } from './notify';
import type { StepOptimisticRequest } from './notify';
import {
  completeOnboardingStep,
  dismissOnboarding,
  getOnboardingState,
  onboardingChannel,
  restoreOnboarding,
} from './api';
import { isOnboardingFrame } from './realtime';
import type { OnboardingState, OnboardingStepKey } from './types';

/** §3.7 降级轮询间隔:WS 不可用时 30s 拉一次 GET state。 */
export const ONBOARDING_POLL_INTERVAL_MS = 30_000;

function matchMemberId(
  members: readonly { id: string; member_type: string; profile: unknown }[],
  userId: string,
  userEmail: string,
): string | null {
  for (const member of members) {
    if (member.member_type !== 'human') continue;
    const profile = member.profile as HumanProfile | null;
    if (profile === null) continue;
    if (profile.id === userId || profile.email === userEmail) return member.id;
  }
  return null;
}

export interface UseOnboardingResult {
  readonly state: OnboardingState | null;
  readonly loading: boolean;
  /** i18n 错误键(无错误为 null) */
  readonly errorKey: string | null;
  readonly workspaceId: string | null;
  readonly memberId: string | null;
  readonly workspaceSlug: string | null;
  /** 工作区最新 issue id(步骤 4 共享深链用;无 issue 为 null)。 */
  readonly latestIssueId: string | null;
  readonly dismiss: () => Promise<void>;
  readonly restore: () => Promise<void>;
  readonly completeStep: (stepKey: OnboardingStepKey) => Promise<void>;
  readonly refetch: () => void;
}

export function useOnboarding(): UseOnboardingResult {
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();
  // MES-106(验收 M1):工作区/成员派生为鉴权请求——未登录(匿名 shell 挂载)
  // 不发起,保持 loading(清单加载中自隐藏);token 写入后随依赖补取。
  const hasToken = useAuthStore((state) => state.token !== null);

  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [workspaceSlug, setWorkspaceSlug] = useState<string | null>(null);
  const [memberId, setMemberId] = useState<string | null>(null);
  const [state, setState] = useState<OnboardingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [latestIssueId, setLatestIssueId] = useState<string | null>(null);

  // 派生活跃工作区与当前成员(与 useInboxContext 同口径,多取 slug 供 CTA 深链)。
  useEffect(() => {
    if (!hasToken) return;
    let cancelled = false;
    void (async () => {
      try {
        const me = await fetchMe(client);
        const active = activeWorkspace(me.memberships);
        if (active === null) {
          if (!cancelled) {
            setLoading(false);
            setErrorKey('state.errorDescription');
          }
          return;
        }
        const roster = await listMembers(client, active.workspace_id, { limit: 100 });
        if (cancelled) return;
        setWorkspaceId(active.workspace_id);
        setWorkspaceSlug(active.workspace_slug);
        setMemberId(matchMemberId(roster.data, me.user.id, me.user.email));
      } catch {
        if (!cancelled) {
          setLoading(false);
          setErrorKey('state.errorDescription');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, hasToken]);

  // 清单进度加载:工作区就绪后拉取;reloadKey 变化(写操作后)重拉。
  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    setLoading(true);
    void getOnboardingState(client, workspaceId)
      .then((loaded) => {
        if (cancelled) return;
        setState(loaded);
        setErrorKey(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, reloadKey]);

  const refetch = useCallback(() => setReloadKey((key) => key + 1), []);

  // 工作区最新 issue(§1.2.1 步骤 4 共享深链 → issue 详情的分派/@ composer)。
  // 随 reloadKey 刷新:建 issue 后步骤 4 CTA 即指向真实 issue。
  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    void listIssues(client, workspaceId, { limit: 1 })
      .then((page) => {
        if (!cancelled) setLatestIssueId(page.data[0]?.id ?? null);
      })
      .catch(() => {
        if (!cancelled) setLatestIssueId(null);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, reloadKey]);

  // 帮助菜单 / 命令面板的恢复不经本 hook 的写路径且无实时帧 → 订阅模块内
  // 变更广播,恢复成功后即时重拉(onboarding.md §4.2 流程 3)。
  useEffect(() => onOnboardingExternalChange(refetch), [refetch]);

  // 空状态主操作完成 → 乐观推进对应步骤(§1.2.2 末注 / §5.1「乐观 UI + 服务端
  // 领域事件复核」):本地即时置位 + POST 手动完成,失败回滚本地置位,成功后重拉
  // 以服务端为真;领域事件到达经完成守卫收敛(手动/自动至多一次)。
  const handleOptimisticRequest = useCallback(
    (request: StepOptimisticRequest) => {
      // 决策基于当前已渲染状态(同步可读);setState updater 只做并发安全改写。
      if (workspaceId === null || state === null) return;
      const target = state.steps.find((s) => s.step_key === request.stepKey);
      if (target === undefined || target.status !== 'pending') return; // 已完成 → 交由服务端领域事件
      const rollback = state;
      setState((prev) => {
        if (prev === null) return prev;
        const current = prev.steps.find((s) => s.step_key === request.stepKey);
        if (current === undefined || current.status !== 'pending') return prev;
        const steps = prev.steps.map((s) =>
          s.step_key === request.stepKey
            ? {
                ...s,
                status: 'completed' as const,
                completed_via: 'manual' as const,
                completed_at: new Date().toISOString(),
              }
            : s,
        );
        return {
          ...prev,
          steps,
          progress: { ...prev.progress, completed: prev.progress.completed + 1 },
        };
      });
      void completeOnboardingStep(client, workspaceId, request.stepKey)
        .then(() => refetch())
        .catch((err: unknown) => {
          setState(rollback); // 回滚乐观置位
          setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription');
        });
    },
    [client, workspaceId, refetch, state],
  );
  useEffect(() => onStepOptimisticRequest(handleOptimisticRequest), [handleOptimisticRequest]);

  // 实时订阅:本频道任何 onboarding.* 帧 → 重拉(DB 是真源,最简正确合并)。
  useEffect(() => {
    if (realtime === null || memberId === null) return;
    const channel = onboardingChannel(memberId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (isOnboardingFrame(frame, channel)) refetch();
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, memberId, refetch]);

  // §3.7 降级:实时上下文不可用(shell 外)时 30s 轮询 GET state。
  useEffect(() => {
    if (realtime !== null || workspaceId === null) return;
    const timer = setInterval(refetch, ONBOARDING_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [realtime, workspaceId, refetch]);

  const runMutation = useCallback(
    async (mutation: (client: MeshApiClient, workspaceId: string) => Promise<unknown>) => {
      if (workspaceId === null) return;
      try {
        await mutation(client, workspaceId);
        refetch();
      } catch (err: unknown) {
        setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription');
      }
    },
    [client, workspaceId, refetch],
  );

  const dismiss = useCallback(
    () => runMutation((c, ws) => dismissOnboarding(c, ws)),
    [runMutation],
  );
  const restore = useCallback(
    () => runMutation((c, ws) => restoreOnboarding(c, ws)),
    [runMutation],
  );
  const completeStep = useCallback(
    (stepKey: OnboardingStepKey) => runMutation((c, ws) => completeOnboardingStep(c, ws, stepKey)),
    [runMutation],
  );

  return {
    state,
    loading,
    errorKey,
    workspaceId,
    memberId,
    workspaceSlug,
    latestIssueId,
    dismiss,
    restore,
    completeStep,
    refetch,
  };
}
