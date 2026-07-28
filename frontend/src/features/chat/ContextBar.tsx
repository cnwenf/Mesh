/**
 * 会话上下文条(chat-session.md §4.2)。呈现已关联的 issue 与 project(各带移除),
 * 并提供「添加/更换上下文」入口(ContextPicker 设定/更换/清除 issue 上下文)。
 * 二者皆无时呈现弱化的「关联上下文」提示。上下文变更经 patchChatSession 三态语义:
 * 设定/更换传 id,清除传 null(省略键则保持);后端按可见性鉴权,404/403 经 toErrorKey toast。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { Button, useToast } from '../../design';
import { useT } from '../../i18n';
import { getIssue } from '../issues/api';
import type { IssueDetail } from '../issues/types';
import { getProject } from '../projects/api';
import type { ProjectDetail } from '../projects/types';
import { patchChatSession } from './api';
import { ContextPicker } from './ContextPicker';
import { toErrorKey } from './errors';
import type { ChatSession } from './types';

export interface ContextBarProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly session: ChatSession;
  /** 上下文字段变更(设定/更换/清除)后回写父级列表与选中态。 */
  readonly onSessionUpdated: (session: ChatSession) => void;
}

export function ContextBar(props: ContextBarProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const { client, workspaceId, session } = props;
  const [contextIssue, setContextIssue] = useState<IssueDetail | null>(null);
  const [contextProject, setContextProject] = useState<ProjectDetail | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  // 上下文 issue 解析(展示 identifier + 标题;失败回退展示 id)。
  useEffect(() => {
    if (session.context_issue_id === null) {
      setContextIssue(null);
      return;
    }
    let cancelled = false;
    getIssue(client, session.context_issue_id)
      .then((detail) => {
        if (!cancelled) setContextIssue(detail);
      })
      .catch(() => {
        if (!cancelled) setContextIssue(null);
      });
    return () => {
      cancelled = true;
    };
  }, [client, session.context_issue_id]);

  // 上下文 project 解析(展示名称;失败回退展示 id)。
  useEffect(() => {
    if (session.context_project_id === null) {
      setContextProject(null);
      return;
    }
    let cancelled = false;
    getProject(client, session.context_project_id)
      .then((detail) => {
        if (!cancelled) setContextProject(detail);
      })
      .catch(() => {
        if (!cancelled) setContextProject(null);
      });
    return () => {
      cancelled = true;
    };
  }, [client, session.context_project_id]);

  // 上下文 patch 统一收口:成功回写父级,失败 toast 具体错误码(404/403 可见性亦走此路径)。
  const patchContext = useCallback(
    async (body: { context_issue_id?: string | null; context_project_id?: string | null }) => {
      try {
        const updated = await patchChatSession(
          client,
          workspaceId,
          session.id,
          body,
          session.updated_at,
        );
        props.onSessionUpdated(updated);
      } catch (err) {
        toast.addToast(t(toErrorKey(err)), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      }
    },
    [client, workspaceId, session, props, toast, t],
  );

  const handlePickIssue = useCallback(
    (issueId: string | null) => {
      setPickerOpen(false);
      void patchContext({ context_issue_id: issueId });
    },
    [patchContext],
  );

  const hasIssue = session.context_issue_id !== null;
  const hasProject = session.context_project_id !== null;

  return (
    <div className="mesh-chat__context-bar" data-testid="chat-context-bar">
      {hasIssue ? (
        <span className="mesh-chat__context-chip">
          <span>{t('chat.context.linkedIssue')}</span>
          <strong>
            {contextIssue !== null
              ? `${contextIssue.identifier} ${contextIssue.title}`
              : session.context_issue_id}
          </strong>
          <Button
            variant="ghost"
            size="sm"
            aria-label={t('chat.context.remove')}
            data-testid="chat-context-remove"
            onClick={() => void patchContext({ context_issue_id: null })}
          >
            ×
          </Button>
        </span>
      ) : null}

      {hasProject ? (
        <span className="mesh-chat__context-chip" data-testid="chat-context-project">
          <span>{t('chat.context.linkedProject')}</span>
          <strong>
            {contextProject !== null ? contextProject.name : session.context_project_id}
          </strong>
          <Button
            variant="ghost"
            size="sm"
            aria-label={t('chat.context.removeProject')}
            data-testid="chat-context-project-remove"
            onClick={() => void patchContext({ context_project_id: null })}
          >
            ×
          </Button>
        </span>
      ) : null}

      {!hasIssue && !hasProject ? (
        <span className="mesh-chat__context-prompt" data-testid="chat-context-prompt">
          {t('chat.context.prompt')}
        </span>
      ) : null}

      <Button
        variant="secondary"
        size="sm"
        className="mesh-chat__context-add"
        data-testid="chat-context-add"
        onClick={() => setPickerOpen(true)}
      >
        {t('chat.context.add')}
      </Button>

      <ContextPicker
        open={pickerOpen}
        client={client}
        workspaceId={workspaceId}
        onClose={() => setPickerOpen(false)}
        onPick={handlePickIssue}
      />
    </div>
  );
}
