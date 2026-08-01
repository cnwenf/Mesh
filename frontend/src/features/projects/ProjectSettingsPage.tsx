/**
 * 项目设置页(project.md §4.1 侧栏设置):基础字段表单(name/description/status/
 * visibility/start_date/target_date/lead)+ 乐观并发保存(useOptimisticMutation,
 * If-Match=updated_at,409 自动收敛并提示)+ 危险区(归档/取消归档 / 删除二次确认)+
 * 成员区(ProjectMembersSection)。归档项目写操作 422 project_archived 经 toast 呈现。
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import {
  MeshApiClient,
  MeshApiError,
  errorToI18nKey,
  getToken,
  useOptimisticMutation,
} from '../../api';
import { Button, Dialog, ErrorState, Input, Select, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { CustomFieldsPanel, LabelsPanel } from '../labels';
import { activeWorkspace, fetchMe, listMembers } from '../members/api';
import type { MemberSummary, Membership } from '../members/types';
import { archiveProject, deleteProject, getProject, unarchiveProject } from './api';
import { ProjectMembersSection } from './ProjectMembersSection';
import { LabeledTextarea } from './widgets';
import type { ProjectDetail, ProjectStatus, ProjectVisibility, UpdateProjectBody } from './types';
import { PROJECT_STATUS_ORDER } from './types';
import './projects.css';

/** UpdateProjectBody 的可写构建态(readonly 属性无法逐步赋值)。 */
type MutableChanges = { -readonly [K in keyof UpdateProjectBody]: UpdateProjectBody[K] };

interface SettingsFormState {
  readonly name: string;
  readonly description: string;
  readonly status: ProjectStatus;
  readonly visibility: ProjectVisibility;
  readonly startDate: string;
  readonly targetDate: string;
  readonly leadMemberId: string;
}

function formFromProject(project: ProjectDetail): SettingsFormState {
  return {
    name: project.name,
    description: project.description ?? '',
    status: project.status,
    visibility: project.visibility,
    startDate: project.start_date ?? '',
    targetDate: project.target_date ?? '',
    leadMemberId: project.lead_member_id ?? '',
  };
}

function diffChanges(form: SettingsFormState, project: ProjectDetail): UpdateProjectBody {
  const changes: MutableChanges = {};
  if (form.name.trim() !== project.name) changes.name = form.name.trim();
  if (form.description !== (project.description ?? '')) {
    changes.description = form.description.trim() === '' ? null : form.description.trim();
  }
  if (form.status !== project.status) changes.status = form.status;
  if (form.visibility !== project.visibility) changes.visibility = form.visibility;
  if (form.startDate !== (project.start_date ?? '')) {
    changes.start_date = form.startDate === '' ? null : form.startDate;
  }
  if (form.targetDate !== (project.target_date ?? '')) {
    changes.target_date = form.targetDate === '' ? null : form.targetDate;
  }
  if (form.leadMemberId !== (project.lead_member_id ?? '')) {
    changes.lead_member_id = form.leadMemberId === '' ? null : form.leadMemberId;
  }
  return changes;
}

export function ProjectSettingsPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const { projectId } = useParams<{ projectId: string }>();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);

  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [roster, setRoster] = useState<MemberSummary[]>([]);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [form, setForm] = useState<SettingsFormState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const mutation = useOptimisticMutation<ProjectDetail>({
    client,
    path: projectId !== undefined ? `/api/v1/projects/${projectId}` : '/api/v1/projects/unknown',
    getServerVersion: (value) => value.updated_at,
    // 409 收敛:重放前先把表单对齐到服务端最新态,避免下一次保存拿陈旧 form
    // 重新 diff 把他人编辑覆盖回去(CWE-362 表单侧收敛)。
    onConflict: async (server) => {
      setForm(formFromProject(server));
      return server;
    },
  });

  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((me) => {
        if (!cancelled) setWorkspace(activeWorkspace(me.memberships));
      })
      .catch(() => {
        if (!cancelled) setError(t('state.errorDescription'));
      });
    return () => {
      cancelled = true;
    };
  }, [client, t]);

  useEffect(() => {
    if (workspace === null || projectId === undefined) return;
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    Promise.all([
      getProject(client, projectId),
      listMembers(client, workspace.workspace_id, { limit: 100 }),
    ])
      .then(([detail, members]) => {
        if (cancelled) return;
        setProject(detail);
        setForm(formFromProject(detail));
        setRoster(members.data);
      })
      .catch((err) => {
        if (cancelled) return;
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(t(key));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspace, projectId, reloadKey, t]);

  const updateForm = (patch: Partial<SettingsFormState>): void => {
    setForm((prev) => (prev === null ? prev : { ...prev, ...patch }));
  };

  const handleSave = async (): Promise<void> => {
    if (project === null || form === null) return;
    const changes = diffChanges(form, project);
    if (Object.keys(changes).length === 0) return;
    try {
      const { result, conflicted } = await mutation.mutate(
        project,
        changes as Partial<ProjectDetail>,
      );
      setProject((prev) => (prev === null ? prev : { ...prev, ...result }));
      toast.addToast(
        t(conflicted ? 'projects.settings.conflictToast' : 'projects.settings.saved'),
        {
          tone: conflicted ? 'warn' : 'success',
          closeLabel: t('common.close'),
        },
      );
    } catch (err) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    }
  };

  const handleArchiveToggle = async (): Promise<void> => {
    if (project === null || projectId === undefined) return;
    try {
      const updated = project.archived
        ? await unarchiveProject(client, projectId)
        : await archiveProject(client, projectId);
      setProject((prev) => (prev === null ? prev : { ...prev, ...updated }));
      toast.addToast(
        t(project.archived ? 'projects.detail.unarchived' : 'projects.detail.archived'),
        {
          tone: 'success',
          closeLabel: t('common.close'),
        },
      );
    } catch (err) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    }
  };

  const handleDelete = async (): Promise<void> => {
    if (projectId === undefined) return;
    setIsDeleting(true);
    try {
      await deleteProject(client, projectId);
      toast.addToast(t('projects.detail.deleted'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      navigate('/projects');
    } catch (err) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      setIsDeleting(false);
      setDeleteOpen(false);
    }
  };

  if (error !== null) {
    return (
      <div className="mesh-projects">
        <ErrorState
          title={t('state.errorTitle')}
          description={error}
          retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      </div>
    );
  }
  if (isLoading || project === null || form === null) {
    return (
      <div className="mesh-projects">
        <Skeleton loadingLabel={t('common.loading')} />
      </div>
    );
  }

  // 改派负责人是 lead/admin 专属操作(project.md §3.4,后端为权威校验):
  // 非 lead/admin 的选择器只读,避免暴露无权限操作。
  const canManageLead =
    project.my_role === 'lead' ||
    (workspace !== null && (workspace.role === 'owner' || workspace.role === 'admin'));

  return (
    <div className="mesh-projects">
      <h1 className="mesh-projects__title">
        {t('projects.settings.title', { name: project.name })}
      </h1>

      <form
        className="mesh-projects__form mesh-projects__settings-form"
        data-testid="settings-form"
        onSubmit={(event) => {
          event.preventDefault();
          void handleSave();
        }}
      >
        <Input
          label={t('projects.settings.name')}
          value={form.name}
          data-testid="settings-name"
          onChange={(event) => updateForm({ name: event.target.value })}
        />
        <LabeledTextarea
          label={t('projects.settings.description')}
          value={form.description}
          onChange={(value) => updateForm({ description: value })}
        />
        <Select
          label={t('projects.settings.status')}
          value={form.status}
          data-testid="settings-status"
          onChange={(event) => updateForm({ status: event.target.value as ProjectStatus })}
        >
          {PROJECT_STATUS_ORDER.map((status) => (
            <option key={status} value={status}>
              {t(`projects.status.${status}`)}
            </option>
          ))}
        </Select>
        <Select
          label={t('projects.settings.visibility')}
          value={form.visibility}
          data-testid="settings-visibility"
          onChange={(event) => updateForm({ visibility: event.target.value as ProjectVisibility })}
        >
          <option value="public">{t('projects.visibility.public')}</option>
          <option value="private">{t('projects.visibility.private')}</option>
        </Select>
        <Input
          type="date"
          label={t('projects.settings.startDate')}
          value={form.startDate}
          data-testid="settings-start-date"
          onChange={(event) => updateForm({ startDate: event.target.value })}
        />
        <Input
          type="date"
          label={t('projects.settings.targetDate')}
          value={form.targetDate}
          data-testid="settings-target-date"
          onChange={(event) => updateForm({ targetDate: event.target.value })}
        />
        <Select
          label={t('projects.settings.lead')}
          value={form.leadMemberId}
          data-testid="settings-lead"
          disabled={!canManageLead}
          onChange={(event) => updateForm({ leadMemberId: event.target.value })}
        >
          <option value="">{t('projects.settings.leadNone')}</option>
          {roster.map((member) => (
            <option key={member.id} value={member.id}>
              {member.display_name}
            </option>
          ))}
        </Select>
        {canManageLead ? null : (
          <p className="mesh-field__hint" data-testid="settings-lead-hint">
            {t('projects.settings.leadReadOnly')}
          </p>
        )}
        <div className="mesh-projects__form-actions">
          <Button
            type="submit"
            variant="primary"
            disabled={mutation.isMutating || form.name.trim().length === 0}
            data-testid="settings-save"
          >
            {t('projects.settings.save')}
          </Button>
        </div>
      </form>

      <ProjectMembersSection client={client} projectId={project.id} roster={roster} />

      {/* label-property.md §4.1 项目设置:项目级标签与自定义字段定义管理 */}
      <section className="mesh-projects__settings-section" aria-label={t('labels.sectionTitle')}>
        <h2 className="mesh-projects__settings-subtitle">{t('labels.sectionTitle')}</h2>
        <LabelsPanel client={client} workspaceId={project.workspace_id} projectId={project.id} />
      </section>
      <section className="mesh-projects__settings-section" aria-label={t('fields.sectionTitle')}>
        <h2 className="mesh-projects__settings-subtitle">{t('fields.sectionTitle')}</h2>
        <CustomFieldsPanel
          client={client}
          workspaceId={project.workspace_id}
          projectId={project.id}
        />
      </section>

      <section
        className="mesh-projects__settings-section mesh-projects__danger-zone"
        aria-label={t('projects.settings.dangerTitle')}
      >
        <h2 className="mesh-projects__settings-subtitle">{t('projects.settings.dangerTitle')}</h2>
        <div className="mesh-projects__form-actions">
          <Button
            variant="secondary"
            data-testid="settings-archive-toggle"
            onClick={() => void handleArchiveToggle()}
          >
            {project.archived ? t('projects.detail.unarchive') : t('projects.detail.archive')}
          </Button>
          <Button
            variant="danger"
            data-testid="settings-delete"
            onClick={() => setDeleteOpen(true)}
          >
            {t('projects.detail.delete')}
          </Button>
        </div>
      </section>

      {deleteOpen ? (
        <Dialog
          open
          onClose={() => setDeleteOpen(false)}
          title={t('projects.detail.deleteTitle')}
          closeLabel={t('common.close')}
        >
          <p data-testid="settings-delete-confirm-text">
            {t('projects.detail.deleteConfirm', { name: project.name })}
          </p>
          <div className="mesh-projects__form-actions">
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              disabled={isDeleting}
              data-testid="settings-delete-confirm"
              onClick={() => void handleDelete()}
            >
              {t('projects.detail.deleteSubmit')}
            </Button>
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}
