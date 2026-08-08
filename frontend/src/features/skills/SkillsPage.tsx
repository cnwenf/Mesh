/**
 * 技能库页面(skill.md §4.1):搜索 / 来源 / 状态过滤 + 卡片网格 + 新建 + 导入 + 市场入口。
 * 实时:订阅 workspace:{ws}:skills,skill.changed / update_available / approval_required
 * 触发重拉(README §6.7);无连接时 useCursorPagination 的常规拉取即退化轮询面。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { MeshApiClient, getToken, useCursorPagination } from '../../api';
import {
  Button,
  DataView,
  Dialog,
  EmptyState,
  ErrorState,
  Icon,
  Input,
  Select,
  Skeleton,
  useToast,
} from '../../design';
import type { IconName } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import {
  DirtyNavigationGuardDialog,
  useDirtyNavigationGuard,
} from '../../workspace/useDirtyNavigationGuard';
import { useWorkspaceMembership, workspaceRoute } from '../members/useWorkspaceMembership';
import { createSkill, listSkills, workspaceSkillsChannel } from './api';
import { ImportWizard } from './ImportWizard';
import type { SkillSourceType } from './types';
import './skills.css';

/** 来源信任徽标(§4.2):builtin 盾形 / user / marketplace / url 警示;一律走统一 Icon(§7.1)。 */
const TRUST_BADGES: Record<string, IconName> = {
  builtin: 'shield',
  user: 'user',
  marketplace: 'store',
  url: 'alert-triangle',
};

export function SkillsPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const membershipState = useWorkspaceMembership(client);
  const membership = membershipState.kind === 'ready' ? membershipState.membership : null;

  const [q, setQ] = useState('');
  const [status, setStatus] = useState('all');
  const [sourceType, setSourceType] = useState('all');
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  // L242 脏态保护:新建技能对话框有输入即视为脏(技能编辑面唯一表单)。
  const [createDirty, setCreateDirty] = useState(false);
  const guard = useDirtyNavigationGuard(createOpen && createDirty);

  const workspaceId = membership?.workspace_id ?? null;
  const canManage = membership?.role === 'admin' || membership?.role === 'owner';

  const skills = useCursorPagination((cursor) =>
    workspaceId === null
      ? Promise.resolve({ data: [], next_cursor: null })
      : listSkills(client, workspaceId, {
          q: q.trim() === '' ? undefined : q.trim(),
          status,
          source_type: sourceType,
          limit: 20,
          cursor: cursor ?? undefined,
        }).then((page) => ({ data: page.data, next_cursor: page.nextCursor })),
  );

  useEffect(() => {
    skills.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, q, status, sourceType, reloadKey]);

  // 实时刷新(§3.5 / §4.6):任意技能域事件 → 重拉当前页。
  useEffect(() => {
    if (realtime === null || workspaceId === null) return;
    const channel = workspaceSkillsChannel(workspaceId);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (String(frame.channel) !== channel) return;
      if (frame.event === 'skill.approval_required') {
        toast.addToast(t('skills.approvalRequiredToast'), {
          tone: 'info',
          closeLabel: t('a11y.closeDialog'),
        });
      }
      if (frame.event === 'skill.update_available') {
        toast.addToast(t('skills.updateAvailableToast'), {
          tone: 'info',
          closeLabel: t('a11y.closeDialog'),
        });
      }
      setReloadKey((k) => k + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspaceId, t, toast]);

  const onCreate = useCallback(
    async (name: string, slug: string, summary: string, tags: string[]) => {
      if (workspaceId === null) return;
      try {
        const created = await createSkill(client, workspaceId, {
          name,
          slug: slug.trim() === '' ? undefined : slug.trim(),
          summary,
          tags: tags.length > 0 ? tags : undefined,
        });
        setCreateOpen(false);
        setCreateDirty(false);
        setReloadKey((k) => k + 1);
        navigate(
          membership === null
            ? `/skills/${created.id}`
            : workspaceRoute(membership.workspace_slug, `/automations/skills/${created.id}`),
        );
      } catch (error) {
        toast.addToast(t('error.conflict'), { tone: 'danger', closeLabel: t('a11y.closeDialog') });
        throw error;
      }
    },
    [client, workspaceId, membership, navigate, t, toast],
  );

  const marketplacePath =
    membership === null
      ? '/skills/marketplace'
      : workspaceRoute(membership.workspace_slug, '/automations/skills/marketplace');
  const skillPath = (skillId: string): string =>
    membership === null
      ? `/skills/${skillId}`
      : workspaceRoute(membership.workspace_slug, `/automations/skills/${skillId}`);

  return (
    <DataView
      className="mesh-skills"
      title={t('skills.pageTitle')}
      actions={
        canManage ? (
          <>
            <Button
              variant="secondary"
              onClick={() => setImportOpen(true)}
              data-testid="skills-import-open"
            >
              {t('skills.importButton')}
            </Button>
            <Button onClick={() => setCreateOpen(true)} data-testid="skills-create-open">
              {t('skills.createButton')}
            </Button>
          </>
        ) : undefined
      }
      toolbar={
        <div className="mesh-skills__toolbar">
          <Input
            label={t('skills.searchPlaceholder')}
            placeholder={t('skills.searchPlaceholder')}
            value={q}
            onChange={(event) => setQ(event.target.value)}
            data-testid="skills-search"
          />
          <Select
            label={t('skills.sourceFilter')}
            value={sourceType}
            onChange={(event) => setSourceType(event.target.value)}
            data-testid="skills-source-filter"
          >
            <option value="all">{t('skills.sourceAll')}</option>
            <option value="builtin">{t('skills.source.builtin')}</option>
            <option value="user">{t('skills.source.user')}</option>
            <option value="marketplace">{t('skills.source.marketplace')}</option>
            <option value="url">{t('skills.source.url')}</option>
          </Select>
          <Select
            label={t('skills.statusFilter')}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            data-testid="skills-status-filter"
          >
            <option value="all">{t('skills.statusAll')}</option>
            <option value="draft">{t('skills.status.draft')}</option>
            <option value="published">{t('skills.status.published')}</option>
            <option value="deprecated">{t('skills.status.deprecated')}</option>
            <option value="disabled">{t('skills.status.disabled')}</option>
          </Select>
          <Link
            className="mesh-skills__market-link"
            to={marketplacePath}
            data-testid="skills-market-link"
          >
            {t('skills.marketplaceLink')}
          </Link>
        </div>
      }
      footer={
        skills.hasMore ? (
          <div className="mesh-skills__more">
            <Button
              variant="secondary"
              onClick={() => void skills.fetchNext()}
              disabled={skills.isFetchingNext}
              data-testid="skills-load-more"
            >
              {t('skills.loadMore')}
            </Button>
          </div>
        ) : undefined
      }
    >
      <span className="sr-only" data-testid="skills-page-title">
        {t('skills.pageTitle')}
      </span>

      {membershipState.kind === 'no_workspace' ? (
        <EmptyState title={t('state.emptyTitle')} description={t('members.noWorkspace')} />
      ) : membershipState.kind === 'loading' || skills.isLoading ? (
        <Skeleton loadingLabel={t('state.loading')} />
      ) : membershipState.kind === 'error' ? (
        <ErrorState title={t('state.errorTitle')} description={t('skills.loadError')} />
      ) : skills.error !== null ? (
        <ErrorState title={t('state.errorTitle')} description={skills.error.message} />
      ) : skills.items.length === 0 ? (
        <EmptyState title={t('skills.emptyTitle')} description={t('skills.emptyDescription')} />
      ) : (
        <ul className="mesh-skills__grid" data-testid="skills-grid">
          {skills.items.map((skill) => (
            <li key={skill.id} className="mesh-skills__card" data-testid={`skill-card-${skill.id}`}>
              <Link className="mesh-skills__card-link" to={skillPath(skill.id)}>
                <span className="mesh-skills__card-name">
                  <span className="mesh-skills__card-badge" title={t('skills.trustBadge')}>
                    <Icon name={TRUST_BADGES[skill.source_type ?? 'user'] ?? 'user'} size={16} />
                  </span>
                  {skill.name}
                  {skill.has_scripts ? (
                    <span className="mesh-skills__script-flag" title={t('skills.hasScripts')}>
                      <Icon name="warning" size={16} />
                    </span>
                  ) : null}
                  {skill.install_status === 'updated_available' ? (
                    <span className="mesh-skills__update-flag" title={t('skills.updateAvailable')}>
                      <Icon name="cycle" size={16} />
                    </span>
                  ) : null}
                </span>
                <span className="mesh-skills__card-summary">{skill.summary}</span>
                <span className="mesh-skills__card-meta">
                  <span className={`mesh-skills__status mesh-skills__status--${skill.status}`}>
                    {t(`skills.status.${skill.status}`)}
                  </span>
                  {skill.current_version ? (
                    <span className="mesh-skills__card-version">v{skill.current_version}</span>
                  ) : null}
                  {skill.install_status ? (
                    <span className="mesh-skills__card-install">
                      {t(`skills.installStatus.${skill.install_status}`)}
                    </span>
                  ) : null}
                  <span className="mesh-skills__card-source">
                    {skill.source_type === null
                      ? ''
                      : t(`skills.source.${skill.source_type as SkillSourceType}`)}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {createOpen && workspaceId !== null ? (
        <CreateSkillDialog
          onClose={() => {
            setCreateOpen(false);
            setCreateDirty(false);
          }}
          onCreate={onCreate}
          onDirtyChange={setCreateDirty}
        />
      ) : null}
      {importOpen && workspaceId !== null ? (
        <ImportWizard
          workspaceId={workspaceId}
          onClose={() => setImportOpen(false)}
          onDone={() => {
            setImportOpen(false);
            setReloadKey((k) => k + 1);
          }}
        />
      ) : null}
      <DirtyNavigationGuardDialog
        isConfirming={guard.isConfirming}
        title={t('common.unsavedTitle')}
        description={t('common.unsavedDescription')}
        stayLabel={t('common.unsavedStay')}
        discardLabel={t('common.unsavedDiscard')}
        closeLabel={t('a11y.closeDialog')}
        onStay={guard.stay}
        onDiscard={guard.discard}
      />
    </DataView>
  );
}

function CreateSkillDialog({
  onClose,
  onCreate,
  onDirtyChange,
}: {
  onClose: () => void;
  onCreate: (name: string, slug: string, summary: string, tags: string[]) => Promise<void>;
  /** L242:任一字段非空即上报脏态,供页面级离开确认。 */
  onDirtyChange?: (dirty: boolean) => void;
}): React.JSX.Element {
  const t = useT();
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [summary, setSummary] = useState('');
  const [tags, setTags] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dialogDirty = name !== '' || slug !== '' || summary !== '' || tags !== '';
  useEffect(() => {
    onDirtyChange?.(dialogDirty);
  }, [dialogDirty, onDirtyChange]);

  const submit = async (): Promise<void> => {
    if (name.trim() === '' || summary.trim() === '') {
      setError(t('skills.createRequired'));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onCreate(
        name.trim(),
        slug,
        summary.trim(),
        tags
          .split(',')
          .map((tag) => tag.trim())
          .filter((tag) => tag !== ''),
      );
    } catch {
      setError(t('skills.createFailed'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open
      title={t('skills.createTitle')}
      onClose={onClose}
      closeLabel={t('a11y.closeDialog')}
    >
      <div className="mesh-skills__form">
        <Input
          label={t('skills.fieldName')}
          value={name}
          onChange={(e) => setName(e.target.value)}
          data-testid="skill-create-name"
        />
        <Input
          label={t('skills.fieldSlug')}
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          data-testid="skill-create-slug"
        />
        <Input
          label={t('skills.fieldSummary')}
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          data-testid="skill-create-summary"
        />
        <Input
          label={t('skills.fieldTags')}
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          data-testid="skill-create-tags"
        />
        {error !== null ? <p className="mesh-skills__form-error">{error}</p> : null}
        <div className="mesh-skills__form-actions">
          <Button variant="secondary" onClick={onClose}>
            {t('skills.cancel')}
          </Button>
          <Button
            onClick={() => void submit()}
            disabled={submitting}
            data-testid="skill-create-submit"
          >
            {t('skills.createSubmit')}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
