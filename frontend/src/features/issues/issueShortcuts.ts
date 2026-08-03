/**
 * Issue 详情页上下文快捷键(search-command-palette.md §4.3)。
 * 命令面板与按键分发共用同一 action 表；动作只触发页面已有的等价控件，
 * 不建立第二套写路径。
 */
import { useShortcutRegistry } from '../../shortcuts';

export interface IssueShortcutLabels {
  readonly edit: string;
  readonly status: string;
  readonly assignee: string;
  readonly priority: string;
  readonly labels: string;
  readonly milestone: string;
  readonly submitComment: string;
  readonly close: string;
}

export interface RegisterIssueContextShortcutsOptions {
  readonly labels: IssueShortcutLabels;
  readonly close: () => void;
}

const focus = (testId: string): void => {
  document.querySelector<HTMLElement>(`[data-testid="${testId}"]`)?.focus();
};

export type IssueFocusTarget = 'status' | 'assignee' | 'priority' | 'labels' | 'milestone';

const FOCUS_TEST_IDS: Readonly<Record<IssueFocusTarget, string>> = {
  status: 'issue-detail-status',
  assignee: 'issue-detail-assignee',
  priority: 'issue-detail-priority',
  labels: 'issue-label-search',
  milestone: 'issue-detail-milestone',
};

/** Board → issue 属性深链在详情落地后调用；未知值安全忽略。 */
export function focusIssueProperty(target: string | null): boolean {
  if (target === null || !(target in FOCUS_TEST_IDS)) return false;
  focus(FOCUS_TEST_IDS[target as IssueFocusTarget]);
  return true;
}

export function registerIssueContextShortcuts(
  options: RegisterIssueContextShortcutsOptions,
): () => void {
  const registry = useShortcutRegistry.getState();
  const actions = [
    {
      id: 'issue.edit',
      combo: 'e',
      label: options.labels.edit,
      run: () => focus('issue-detail-title'),
    },
    {
      id: 'issue.status',
      combo: 's',
      label: options.labels.status,
      run: () => focus('issue-detail-status'),
    },
    {
      id: 'issue.assignee',
      combo: 'a',
      label: options.labels.assignee,
      run: () => focus('issue-detail-assignee'),
    },
    {
      id: 'issue.priority',
      combo: 'p',
      label: options.labels.priority,
      run: () => focus('issue-detail-priority'),
    },
    {
      id: 'issue.labels',
      combo: 'l',
      label: options.labels.labels,
      run: () => focus('issue-label-search'),
    },
    {
      id: 'issue.milestone',
      combo: 'm',
      label: options.labels.milestone,
      run: () => focus('issue-detail-milestone'),
    },
    {
      id: 'issue.submit.comment',
      combo: 'mod+enter',
      label: options.labels.submitComment,
      run: () =>
        document.querySelector<HTMLButtonElement>('[data-testid="composer-submit"]')?.click(),
    },
    { id: 'issue.close', combo: 'esc', label: options.labels.close, run: options.close },
  ] as const;
  const unregisterShortcuts = registry.registerShortcuts(
    actions.map((action) => ({ ...action, group: 'issue' as const })),
  );
  const unregisterCommands = actions.map((action) =>
    registry.registerCommand({ ...action, group: 'issue' as const }),
  );

  return () => {
    unregisterShortcuts();
    for (const unregister of unregisterCommands) unregister();
  };
}
