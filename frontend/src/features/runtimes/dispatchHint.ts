/**
 * 无可用 runtime 的分派恢复提示(L186,README §6.12 专项恢复入口):
 * agent 执行需有 runtime 认领(§6.4 标签/能力/亲和匹配)。分派给 agent 时
 * 若工作区没有任何在线 runtime,则无人可认领 → 按 onboarding.md §4 次级 CTA
 * 明确提示「无匹配 runtime」并深链 Runtimes 页。
 *
 * 语义:仅提示、不阻断 —— 分派本身正常写入;runtime 上线后触发器照常认领。
 * 仅在确定「无在线 runtime」时提示;探测失败(网络/权限)按可用处理,不误报。
 */
import type { MeshApiClient } from '../../api';
import { listRuntimes } from './api';

/** 工作区是否存在至少一个在线 runtime(limit 1 轻探测)。 */
export async function workspaceHasOnlineRuntime(
  client: MeshApiClient,
  workspaceId: string,
): Promise<boolean> {
  try {
    const { data } = await listRuntimes(client, workspaceId, { status: 'online', limit: 1 });
    return data.length > 0;
  } catch {
    // 探测失败不等于「无 runtime」:不确定时不提示,避免误导用户。
    return true;
  }
}
