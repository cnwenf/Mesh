/**
 * 上手引导 API 调用(契约层,onboarding.md §3.1/§3.2,README §6.14 包络)。
 * 成员自助端点经必填查询参数 workspace_id 指定当前工作区;管理员重置显式嵌套于
 * /workspaces/{ws}/ 路径。单对象一律走 `request`(自动解 {data})。
 */
import type { MeshApiClient } from '../../api';
import type { OnboardingState, OnboardingStep, OnboardingStepKey } from './types';

const ONBOARDING_PATH = '/api/v1/onboarding';

/** 成员私有实时频道(onboarding.md §3.7,README §6.7 逐资源授权):member:{member_id}:onboarding */
export function onboardingChannel(memberId: string): string {
  return `member:${memberId}:onboarding`;
}

/** 获取当前成员在该工作区的清单进度(主记录 + 全部步骤,无分页——步骤固定五步)。 */
export async function getOnboardingState(
  client: MeshApiClient,
  workspaceId: string,
): Promise<OnboardingState> {
  return client.request<OnboardingState>('GET', `${ONBOARDING_PATH}/state`, {
    query: { workspace_id: workspaceId },
  });
}

/** 手动完成某步(幂等;已完成/跳过为 no-op,不改写来源与时间,§3.5)。 */
export async function completeOnboardingStep(
  client: MeshApiClient,
  workspaceId: string,
  stepKey: OnboardingStepKey,
): Promise<OnboardingStep> {
  return client.request<OnboardingStep>(
    'POST',
    `${ONBOARDING_PATH}/steps/${stepKey}/complete`,
    { query: { workspace_id: workspaceId }, body: {} },
  );
}

/** 整体关闭清单(幂等;dismissed_at 保持首次值,§3.5)。 */
export async function dismissOnboarding(
  client: MeshApiClient,
  workspaceId: string,
): Promise<{ id: string; dismissed_at: string | null }> {
  return client.request<{ id: string; dismissed_at: string | null }>(
    'POST',
    `${ONBOARDING_PATH}/dismiss`,
    { query: { workspace_id: workspaceId }, body: {} },
  );
}

/** 恢复已关闭的清单(清除 dismissed_at,幂等,§3.5)。 */
export async function restoreOnboarding(
  client: MeshApiClient,
  workspaceId: string,
): Promise<{ id: string; dismissed_at: string | null }> {
  return client.request<{ id: string; dismissed_at: string | null }>(
    'POST',
    `${ONBOARDING_PATH}/restore`,
    { query: { workspace_id: workspaceId }, body: {} },
  );
}

/** 管理员重置某成员清单(admin/owner;删除主记录与步骤并重建,§3.4/§3.5)。 */
export async function resetOnboardingMember(
  client: MeshApiClient,
  workspaceId: string,
  memberId: string,
): Promise<OnboardingState> {
  return client.request<OnboardingState>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/onboarding/reset`,
    { body: { member_id: memberId, checklist: 'activation' } },
  );
}
