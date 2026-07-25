/**
 * 骨架实体类型 — 供契约层/实时层/演示区使用。
 * 业务实体的完整模型归各模块 Issue;此处仅定义增量合并机制所需的最小契约。
 */

/** 带服务端版本标记的实体(乐观并发以 updated_at 作 If-Match 值,§6.14) */
export interface Versioned {
  id: string;
  /** RFC3339 UTC;乐观并发版本标记 */
  updated_at: string;
}

/** 实时事件 payload 的可见性水位(§6.7:供客户端判定归属) */
export interface Visibility {
  workspace_id?: string;
  project_id?: string | null;
  /** 其他模块可见性维度由后续 Issue 扩展 */
  [key: string]: unknown;
}

/** 演示/骨架用 issue 摘要(真实字段归 issue.md) */
export interface IssueSummary extends Versioned {
  identifier: string;
  title: string;
  status_category: 'backlog' | 'todo' | 'in_progress' | 'in_review' | 'done' | 'cancelled';
  assignee_id?: string | null;
  visibility?: Visibility;
}
