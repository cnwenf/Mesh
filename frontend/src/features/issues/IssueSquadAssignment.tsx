/**
 * Issue → 小队分派(issue.md §4.3-2 / squad.md §1.2 S4):
 * 挂载即查 getIssueAssignment;活跃分派 → 头部单一责任主体徽章(组长头像 +
 * 「{squad_name} · led by {leader}」,深链小队详情)。「分派给小队」入口弹出活跃
 * 小队列表(含 member_preview),选定即 assignTask(202)→ 刷新徽章与 issue;
 * 422 squad_no_leader 呈现服务端错误。不触碰既有人类负责人下拉。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, Dialog, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { MemberAvatarWall } from '../squads/MemberAvatarWall';
import { assignTask, getIssueAssignment, listSquads } from '../squads/api';
import type { IssueAssignment, Squad } from '../squads/types';
import './issues.css';

const SQUAD_LIST_LIMIT = 50;

function initialsOf(name: string): string {
  const trimmed = name.trim();
  if (trimmed === '') return '?';
  return Array.from(trimmed)[0].toUpperCase();
}

interface AssignToSquadDialogProps {
  readonly workspaceId: string;
  readonly issueId: string;
  readonly onAssigned: () => void;
  readonly onClose: () => void;
}

function AssignToSquadDialog(props: AssignToSquadDialogProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [squads, setSquads] = useState<Squad[] | null>(null);
  const [busySquadId, setBusySquadId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const page = await listSquads(client, props.workspaceId, {
          status: 'active',
          limit: SQUAD_LIST_LIMIT,
        });
        if (!cancelled) setSquads([...page.data]);
      } catch (err: unknown) {
        if (cancelled) return;
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(t(key));
        setSquads([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, props.workspaceId, t]);

  const assign = useCallback(
    async (squadId: string) => {
      setBusySquadId(squadId);
      setError(null);
      try {
        await assignTask(client, props.workspaceId, squadId, { issue_id: props.issueId });
        toast.addToast(t('issues.squad.assigned'), { tone: 'success', closeLabel: t('common.close') });
        props.onAssigned();
        props.onClose();
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(t(key));
      } finally {
        setBusySquadId(null);
      }
    },
    [client, props, toast, t],
  );

  return (
    <Dialog open onClose={props.onClose} title={t('issues.squad.assignTitle')} closeLabel={t('common.close')}>
      <div className="mesh-issues__squad-picker" data-testid="issue-squad-picker">
        {squads === null ? (
          <Skeleton loadingLabel={t('common.loading')} />
        ) : squads.length === 0 ? (
          <p className="mesh-issues__squad-empty">{t('issues.squad.noActiveSquads')}</p>
        ) : (
          <ul className="mesh-issues__squad-list">
            {squads.map((squad) => (
              <li key={squad.id} className="mesh-issues__squad-item" data-testid={`issue-squad-option-${squad.id}`}>
                <div className="mesh-issues__squad-item-info">
                  <span className="mesh-issues__squad-item-name">{squad.name}</span>
                  {squad.description !== null && squad.description !== '' ? (
                    <span className="mesh-issues__squad-item-desc">{squad.description}</span>
                  ) : null}
                  <MemberAvatarWall members={squad.member_preview} />
                </div>
                <Button
                  size="sm"
                  disabled={busySquadId !== null}
                  onClick={() => void assign(squad.id)}
                  data-testid={`issue-squad-assign-${squad.id}`}
                >
                  {t('issues.squad.assign')}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {error !== null ? (
          <p className="mesh-issues__squad-error" role="alert" data-testid="issue-squad-error">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

export interface IssueSquadAssignmentProps {
  readonly workspaceId: string;
  readonly issueId: string;
  /** 分派成功后通知父级重取 issue(负责人已被服务端改写为组长)。 */
  readonly onChanged: () => void;
}

export function IssueSquadAssignment(props: IssueSquadAssignmentProps): React.JSX.Element {
  const t = useT();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [assignment, setAssignment] = useState<IssueAssignment | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const current = await getIssueAssignment(client, props.workspaceId, props.issueId);
        if (!cancelled) setAssignment(current);
      } catch {
        // 查询失败:徽章缺省隐藏,不阻断 issue 详情(分派入口仍可用)。
        if (!cancelled) setAssignment(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, props.workspaceId, props.issueId, reloadKey]);

  const refresh = useCallback(() => {
    setReloadKey((k) => k + 1);
    props.onChanged();
  }, [props]);

  return (
    <div className="mesh-issues__squad" data-testid="issue-squad-assignment">
      {assignment !== null ? (
        <Link
          to={`/squads/${assignment.squad_id}`}
          className="mesh-issues__squad-badge"
          data-testid="issue-squad-badge"
        >
          <span className="mesh-issues__squad-badge-avatar" aria-hidden="true">
            {initialsOf(assignment.leader?.name ?? assignment.squad_name)}
          </span>
          <span className="mesh-issues__squad-badge-text">
            {assignment.leader !== null
              ? t('issues.squad.ledBy', { squad: assignment.squad_name, leader: assignment.leader.name })
              : t('issues.squad.squadOnly', { squad: assignment.squad_name })}
          </span>
        </Link>
      ) : null}
      <Button
        size="sm"
        variant="secondary"
        onClick={() => setDialogOpen(true)}
        data-testid="issue-assign-squad"
      >
        {t('issues.squad.assignToSquad')}
      </Button>
      {dialogOpen ? (
        <AssignToSquadDialog
          workspaceId={props.workspaceId}
          issueId={props.issueId}
          onAssigned={refresh}
          onClose={() => setDialogOpen(false)}
        />
      ) : null}
    </div>
  );
}
