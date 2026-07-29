/**
 * 帮助菜单 / 命令面板共用的「恢复上手清单」编排(onboarding.md §4.2 流程 3)。
 * 派生活跃工作区与 useOnboarding 同口径(fetchMe → activeWorkspace 首个成员身份),
 * 再 POST /onboarding/restore;shell 外(帮助层位于 App 根部)无实时上下文亦可调用。
 */
import type { MeshApiClient } from '../../api';
import { activeWorkspace, fetchMe } from '../members/api';
import { restoreOnboarding } from './api';
import { notifyOnboardingExternalChange } from './notify';

/** 当前活跃工作区的清单恢复;无工作区时为 no-op。返回是否发起恢复。 */
export async function restoreActiveOnboarding(client: MeshApiClient): Promise<boolean> {
  const me = await fetchMe(client);
  const active = activeWorkspace(me.memberships);
  if (active === null) return false;
  await restoreOnboarding(client, active.workspace_id);
  // dismiss/restore 不发实时帧:广播一次,令 useOnboarding 即时重拉,
  // 清单按库内进度重现(§4.2 流程 3)。
  notifyOnboardingExternalChange();
  return true;
}
