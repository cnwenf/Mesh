/**
 * 新建会话对话框(chat-session.md §4.1)。选择 agent(必填)+ 可选关联 issue 上下文;
 * 关联后 agent 将读取该 issue 上下文(§6.15 结构隔离注入),界面给出提示文案。
 * agent 列表取 active(agents 模块),issue 经搜索按需查询(issues 模块)。
 * 创建经父级 onCreate 落库(乐观/错误在父级),本组件只编排选择态。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { Button, Dialog, ErrorState, Input, Select, Skeleton, useToast } from '../../design';
import { useT } from '../../i18n';
import { listAgents } from '../agents/api';
import type { AgentSummary } from '../agents/types';
import { listIssues } from '../issues/api';
import type { IssueSummary } from '../issues/types';
import { listProjects } from '../projects/api';
import type { ProjectSummary } from '../projects/types';
import { toErrorKey } from './errors';

export interface NewSessionDialogProps {
  readonly open: boolean;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly onClose: () => void;
  readonly onCreate: (
    agentId: string,
    contextIssueId: string | null,
    contextProjectId: string | null,
  ) => Promise<void>;
}

export function NewSessionDialog(props: NewSessionDialogProps): React.JSX.Element | null {
  const t = useT();
  const toast = useToast();
  const [agents, setAgents] = useState<readonly AgentSummary[] | null>(null);
  const [projects, setProjects] = useState<readonly ProjectSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [agentId, setAgentId] = useState('');
  const [issueQuery, setIssueQuery] = useState('');
  const [issues, setIssues] = useState<readonly IssueSummary[]>([]);
  const [contextIssueId, setContextIssueId] = useState<string | null>(null);
  const [contextProjectId, setContextProjectId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // 打开时拉取可选 agent(active)。
  useEffect(() => {
    if (!props.open) return;
    let cancelled = false;
    setAgents(null);
    setLoadError(null);
    listAgents(props.client, props.workspaceId, { status: 'active', limit: 50 })
      .then((page) => {
        if (!cancelled) setAgents(page.data);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(toErrorKey(err, 'state.errorDescription'));
      });
    return () => {
      cancelled = true;
    };
  }, [props.open, props.client, props.workspaceId]);

  // 可选项目上下文(§4.2):拉取可访问项目供选择;失败不阻断创建(退化为无项目)。
  useEffect(() => {
    if (!props.open) return;
    let cancelled = false;
    setProjects([]);
    listProjects(props.client, props.workspaceId, { limit: 50 })
      .then((page) => {
        if (!cancelled) setProjects(page.data);
      })
      .catch(() => {
        if (!cancelled) setProjects([]);
      });
    return () => {
      cancelled = true;
    };
  }, [props.open, props.client, props.workspaceId]);

  // issue 搜索(去抖由用户输入驱动;空查询清空结果)。
  const searchIssues = useCallback(
    async (query: string) => {
      setIssueQuery(query);
      if (query.trim() === '') {
        setIssues([]);
        return;
      }
      try {
        const page = await listIssues(props.client, props.workspaceId, { q: query, limit: 20 });
        setIssues(page.data);
      } catch {
        setIssues([]);
      }
    },
    [props.client, props.workspaceId],
  );

  const handleCreate = useCallback(async () => {
    if (agentId === '' || creating) return;
    setCreating(true);
    try {
      await props.onCreate(agentId, contextIssueId, contextProjectId);
    } catch (err) {
      toast.addToast(t(toErrorKey(err)), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setCreating(false);
    }
  }, [agentId, creating, contextIssueId, contextProjectId, props, toast, t]);

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      title={t('chat.newSession.title')}
      closeLabel={t('a11y.closeDialog')}
    >
      <div className="mesh-chat__new-session" data-testid="chat-new-session">
        {loadError !== null ? (
          <ErrorState title={t('state.errorTitle')} description={t(loadError)} />
        ) : agents === null ? (
          <Skeleton loadingLabel={t('common.loading')} />
        ) : (
          <>
            <Select
              label={t('chat.newSession.agentLabel')}
              value={agentId}
              data-testid="chat-new-session-agent"
              onChange={(event) => setAgentId(event.target.value)}
            >
              <option value="">{t('chat.newSession.agentPlaceholder')}</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.display_name !== '' ? agent.display_name : agent.name}
                </option>
              ))}
            </Select>

            <Select
              label={t('chat.newSession.projectLabel')}
              value={contextProjectId ?? ''}
              data-testid="chat-new-session-project"
              onChange={(event) =>
                setContextProjectId(event.target.value === '' ? null : event.target.value)
              }
            >
              <option value="">{t('chat.newSession.projectPlaceholder')}</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </Select>

            <Input
              label={t('chat.newSession.contextLabel')}
              value={issueQuery}
              data-testid="chat-new-session-context"
              placeholder={t('chat.newSession.contextSearchPlaceholder')}
              hint={t('chat.newSession.contextHint')}
              onChange={(event) => void searchIssues(event.target.value)}
            />

            {issues.length > 0 ? (
              <ul className="mesh-chat__context-results" data-testid="chat-context-results">
                {issues.map((issue) => (
                  <li key={issue.id}>
                    <button
                      type="button"
                      className="mesh-chat__context-result"
                      data-testid={`chat-context-option-${issue.id}`}
                      onClick={() => {
                        setContextIssueId(issue.id);
                        setIssues([]);
                        setIssueQuery(`${issue.identifier} ${issue.title}`);
                      }}
                    >
                      <span className="mesh-chat__context-id">{issue.identifier}</span>
                      <span className="mesh-chat__context-title">{issue.title}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}

            {contextIssueId !== null ? (
              <p className="mesh-chat__context-selected" data-testid="chat-context-selected">
                {t('chat.newSession.contextSelected')}
                <Button
                  variant="ghost"
                  size="sm"
                  data-testid="chat-context-clear"
                  onClick={() => {
                    setContextIssueId(null);
                    setIssueQuery('');
                  }}
                >
                  {t('common.cancel')}
                </Button>
              </p>
            ) : null}

            <div className="mesh-chat__new-session-actions">
              <Button
                variant="secondary"
                data-testid="chat-new-session-cancel"
                onClick={props.onClose}
              >
                {t('common.cancel')}
              </Button>
              <Button
                data-testid="chat-new-session-create"
                disabled={agentId === ''}
                isLoading={creating}
                onClick={() => void handleCreate()}
              >
                {t('chat.newSession.create')}
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
