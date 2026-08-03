/**
 * Issue 状态类别的展示映射。
 *
 * 状态名称可由工作区自定义，但 category 是稳定语义；列表与详情共用同一
 * Badge tone，避免两个页面对同一状态给出不同反馈。颜色只作增强，Badge
 * 自带图标与文字仍是语义真源(design-quality.md §7.2)。
 */
import type { BadgeTone } from '../../design';
import type { StateCategory } from './types';

export function categoryTone(category: StateCategory): BadgeTone {
  switch (category) {
    case 'todo':
      return 'info';
    case 'in_progress':
      return 'accent';
    case 'in_review':
      return 'warning';
    case 'blocked':
      return 'danger';
    case 'done':
      return 'success';
    case 'backlog':
    case 'cancelled':
      return 'neutral';
  }
}
