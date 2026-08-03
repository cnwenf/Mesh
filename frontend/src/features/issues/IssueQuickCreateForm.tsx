/**
 * 快速创建表单(issue.md §4.3 / design-quality.md §9.3):
 * 只要求标题;优先级必选、项目/负责人可渐进展开;支持连续创建(「创建并继续」)。
 * 失败保留已输入内容(error 就近呈现,不清空草稿,§7.7/§9.1 error 行)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, Input, Select } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import type { Membership, MemberSummary } from '../members/types';
import type { ProjectSummary } from '../projects/types';
import { listProjects } from '../projects/api';
import { createIssue } from './api';
import type { IssuePriority, IssueSummary } from './types';
import { PRIORITY_ORDER } from './types';
import './issues.css';

interface IssueQuickCreateFormProps {
  readonly workspace: Membership | null;
  readonly members: readonly MemberSummary[];
  readonly onCreated: (issue: IssueSummary) => void;
  readonly onClose: () => void;
}

export function IssueQuickCreateForm(props: IssueQuickCreateFormProps): React.JSX.Element {
  const t = useT();
  const [title, setTitle] = useState('');
  const [priority, setPriority] = useState<IssuePriority>('none');
  const [projectId, setProjectId] = useState('');
  const [assigneeId, setAssigneeId] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const { workspace, members } = props;

  // 展开更多字段时按需加载项目名册(成员名册由页面下传,§4.2/§4.3)
  useEffect(() => {
    if (!expanded || workspace === null) return;
    let cancelled = false;
    void (async () => {
      const projectPage = await listProjects(client, workspace.workspace_id, { limit: 100 });
      if (cancelled) return;
      setProjects([...projectPage.data]);
    })();
    return () => {
      cancelled = true;
    };
  }, [expanded, workspace, client]);

  const submit = useCallback(
    async (keepOpen: boolean) => {
      if (workspace === null || title.trim() === '') return;
      setIsSaving(true);
      setError(null);
      try {
        const created = await createIssue(client, workspace.workspace_id, {
          title: title.trim(),
          priority,
          project_id: projectId === '' ? undefined : projectId,
          assignee_id: assigneeId === '' ? undefined : assigneeId,
        });
        props.onCreated(created);
        if (keepOpen) {
          setTitle('');
        } else {
          props.onClose();
        }
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(t(key));
      } finally {
        setIsSaving(false);
      }
    },
    [client, workspace, title, priority, projectId, assigneeId, props, t],
  );

  return (
    <form
      className="mesh-issues__create"
      data-testid="issue-create-form"
      onSubmit={(event) => {
        event.preventDefault();
        void submit(false);
      }}
    >
      <Input
        label={t('issues.create.title')}
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder={t('issues.create.titlePlaceholder')}
        data-testid="issue-create-title"
        autoFocus
      />
      <Select
        label={t('issues.priority.label')}
        value={priority}
        onChange={(event) => setPriority(event.target.value as IssuePriority)}
      >
        {PRIORITY_ORDER.map((p) => (
          <option key={p} value={p}>
            {t(`issues.priority.${p}`)}
          </option>
        ))}
      </Select>
      <Button
        type="button"
        variant="ghost"
        onClick={() => setExpanded((v) => !v)}
        data-testid="issue-create-expand"
      >
        {expanded ? t('issues.create.collapse') : t('issues.create.expand')}
      </Button>
      {expanded ? (
        <>
          <Select
            label={t('issues.create.project')}
            value={projectId}
            data-testid="issue-create-project"
            onChange={(event) => setProjectId(event.target.value)}
          >
            <option value="">{t('issues.detail.inbox')}</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}（{p.key}）
              </option>
            ))}
          </Select>
          <Select
            label={t('issues.create.assignee')}
            value={assigneeId}
            data-testid="issue-create-assignee"
            onChange={(event) => setAssigneeId(event.target.value)}
          >
            <option value="">{t('issues.unassigned')}</option>
            {members
              .filter((m) => m.status === 'active')
              .map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                  {m.member_type === 'agent' ? ` (${t('issues.agentBadge')})` : ''}
                </option>
              ))}
          </Select>
        </>
      ) : null}
      {error !== null ? (
        <p className="mesh-issues__create-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="mesh-issues__create-actions">
        <Button type="submit" isLoading={isSaving} disabled={title.trim() === ''}>
          {t('issues.create.submit')}
        </Button>
        <Button
          type="button"
          variant="secondary"
          isLoading={isSaving}
          disabled={title.trim() === ''}
          onClick={() => void submit(true)}
        >
          {t('issues.create.submitMore')}
        </Button>
        <Button type="button" variant="ghost" onClick={props.onClose}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  );
}
