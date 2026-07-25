/**
 * 项目模块实时帧合并(project.md §3.5/§4.5,README §6.7)。
 * 纯函数:绝不修改入参,有变化返回新数组/对象,无变化返回原引用。
 *
 * 帧形态:{op:'event', channel, seq, event, payload};event ∈
 * project.created/updated/archived/unarchived/deleted · project_update.added ·
 * milestone.created/updated/deleted · cycle.updated。
 * project.updated 的 payload 携带变更字段(可能嵌于 `changes`)+ progress + updated_at;
 * 防回退以 updated_at 字符串比较为闸(RFC3339 UTC 可直接比较)。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import type { Milestone, ProjectDetail, ProjectSummary, ProjectUpdateEntry } from './types';

interface FramePayload {
  readonly id?: unknown;
  readonly updated_at?: unknown;
  readonly changes?: unknown;
}

/** 取 `<entity>.<action>` 的动作后缀。 */
function actionOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? event : event.slice(dot + 1);
}

function entityOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? '' : event.slice(0, dot);
}

function payloadOf(frame: RealtimeEventFrame): FramePayload {
  return frame.payload as FramePayload;
}

/** 防回退:两侧 updated_at 皆存在且帧更旧 → 丢弃。 */
function isStale(
  existingUpdatedAt: string | undefined,
  payload: FramePayload,
): boolean {
  const frameUpdatedAt = typeof payload.updated_at === 'string' ? payload.updated_at : undefined;
  if (existingUpdatedAt === undefined || frameUpdatedAt === undefined) return false;
  return frameUpdatedAt < existingUpdatedAt;
}

/** 合并 payload 顶层字段与嵌套 `changes` 字段(浅层)。 */
function mergedFields<T>(existing: T, payload: FramePayload): T {
  const { changes, ...top } = payload;
  const changeFields =
    typeof changes === 'object' && changes !== null ? (changes as Record<string, unknown>) : {};
  return { ...existing, ...top, ...changeFields } as T;
}

/**
 * 列表级帧合并(workspace:{ws}:projects 频道)。
 * `belongs` 判定项目是否属于当前筛选视图;不属于则从列表移除(可见性水位,§6.7)。
 */
export function applyProjectListFrame(
  projects: readonly ProjectSummary[],
  frame: RealtimeEventFrame,
  belongs: (project: ProjectSummary) => boolean,
): ProjectSummary[] {
  if (entityOf(frame.event) !== 'project') return projects as ProjectSummary[];
  const payload = payloadOf(frame);
  const id = typeof payload.id === 'string' ? payload.id : undefined;
  if (id === undefined) return projects as ProjectSummary[];
  const action = actionOf(frame.event);

  if (action === 'deleted') {
    const next = projects.filter((project) => project.id !== id);
    return next.length === projects.length ? (projects as ProjectSummary[]) : next;
  }

  const index = projects.findIndex((project) => project.id === id);
  const existing = index === -1 ? undefined : projects[index];

  if (action === 'archived' || action === 'unarchived') {
    if (existing === undefined) return projects as ProjectSummary[];
    if (isStale(existing.updated_at, payload)) return projects as ProjectSummary[];
    const updated: ProjectSummary = mergedFields(existing, {
      ...payload,
      archived: action === 'archived',
    });
    return replaceAt(projects, index, updated, belongs);
  }

  // created / updated:防回退后 upsert,再按 belongs 决定去留
  if (existing !== undefined && isStale(existing.updated_at, payload)) {
    return projects as ProjectSummary[];
  }
  const merged = (
    existing !== undefined ? mergedFields(existing, payload) : (payload as ProjectSummary)
  ) as ProjectSummary;
  if (!belongs(merged)) {
    if (existing === undefined) return projects as ProjectSummary[];
    const next = projects.filter((project) => project.id !== id);
    return next;
  }
  if (existing !== undefined) {
    return replaceAt(projects, index, merged, () => true);
  }
  return [...projects, merged];
}

function replaceAt(
  projects: readonly ProjectSummary[],
  index: number,
  updated: ProjectSummary,
  belongs: (project: ProjectSummary) => boolean,
): ProjectSummary[] {
  if (!belongs(updated)) {
    return projects.filter((_, i) => i !== index);
  }
  return projects.map((project, i) => (i === index ? updated : project));
}

/**
 * 里程碑帧合并(project:{id} 频道的 milestone.created/updated/deleted)。
 */
export function applyMilestoneFrame(
  milestones: readonly Milestone[],
  frame: RealtimeEventFrame,
): Milestone[] {
  if (entityOf(frame.event) !== 'milestone') return milestones as Milestone[];
  const payload = payloadOf(frame);
  const id = typeof payload.id === 'string' ? payload.id : undefined;
  if (id === undefined) return milestones as Milestone[];
  const action = actionOf(frame.event);

  if (action === 'deleted') {
    const next = milestones.filter((milestone) => milestone.id !== id);
    return next.length === milestones.length ? (milestones as Milestone[]) : next;
  }

  const index = milestones.findIndex((milestone) => milestone.id === id);
  const existing = index === -1 ? undefined : milestones[index];
  if (existing !== undefined && isStale(existing.updated_at, payload)) {
    return milestones as Milestone[];
  }
  const merged = (
    existing !== undefined ? mergedFields(existing, payload) : (payload as Milestone)
  ) as Milestone;
  if (existing !== undefined) {
    return milestones.map((milestone, i) => (i === index ? merged : milestone));
  }
  return [...milestones, merged];
}

/**
 * 更新动态帧合并(project_update.added → 头插;重复 id 不重复插入)。
 */
export function applyUpdateFrame(
  updates: readonly ProjectUpdateEntry[],
  frame: RealtimeEventFrame,
): ProjectUpdateEntry[] {
  if (frame.event !== 'project_update.added') return updates as ProjectUpdateEntry[];
  const payload = payloadOf(frame);
  const id = typeof payload.id === 'string' ? payload.id : undefined;
  if (id === undefined) return updates as ProjectUpdateEntry[];
  if (updates.some((update) => update.id === id)) return updates as ProjectUpdateEntry[];
  return [payload as ProjectUpdateEntry, ...updates];
}

/**
 * 详情页头合并(project.updated → 合并变更字段;防回退)。
 */
export function mergeProjectHeader(
  project: ProjectDetail,
  frame: RealtimeEventFrame,
): ProjectDetail {
  if (frame.event !== 'project.updated') return project;
  const payload = payloadOf(frame);
  const id = typeof payload.id === 'string' ? payload.id : undefined;
  if (id !== project.id) return project;
  if (isStale(project.updated_at, payload)) return project;
  return mergedFields(project, payload);
}
