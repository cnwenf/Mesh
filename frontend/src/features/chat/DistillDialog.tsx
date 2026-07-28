/**
 * 沉淀为 issue 评论对话框(chat-session.md §4 沉淀 / README §6.9 触发矩阵)。
 * 打开即调 distill-preview 取副作用预览:目标 issue + 最终正文(可编辑预填)+
 * 附件清单 + 将被触发的 agent 名单(提示「发布后将触发一次运行」)+ 「仅通知不触发」
 * 开关(suppress_triggers,仅在支持且可触发时呈现)。一次提交经 comments.createComment
 * (body_markdown + suppress_triggers)落库,成功 toast + 跳 issue。预览失败可重试。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { Button, Dialog, ErrorState, Skeleton, useToast } from '../../design';
import { useT } from '../../i18n';
import { createComment } from '../comments/api';
import { distillPreview } from './api';
import { toErrorKey } from './errors';
import type { DistillPreview } from './types';

export interface DistillDialogProps {
  readonly open: boolean;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly sessionId: string;
  /** 待沉淀正文(父级由会话消息汇编的 Markdown)。 */
  readonly initialBody: string;
  /** 目标 issue(会话上下文 issue;父级保证非空才开启本对话框)。 */
  readonly targetIssueId: string;
  /** 随沉淀附上的附件 id(会话内附件)。 */
  readonly attachmentIds: readonly string[];
  readonly onClose: () => void;
  /** 提交成功回调(父级关闭/跳转;identifier 供链接展示)。 */
  readonly onDistilled: (issueId: string, identifier: string) => void;
}

export function DistillDialog(props: DistillDialogProps): React.JSX.Element | null {
  const t = useT();
  const toast = useToast();
  const [preview, setPreview] = useState<DistillPreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [body, setBody] = useState('');
  const [suppress, setSuppress] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // 打开时拉取副作用预览(§6.9);reloadKey 支持失败重试。
  useEffect(() => {
    if (!props.open) return;
    let cancelled = false;
    setPreview(null);
    setLoadError(null);
    distillPreview(props.client, props.workspaceId, props.sessionId, {
      body_markdown: props.initialBody,
      target_issue_id: props.targetIssueId,
      attachment_ids: props.attachmentIds,
    })
      .then((result) => {
        if (cancelled) return;
        setPreview(result);
        setBody(result.body_markdown);
      })
      .catch((err) => {
        if (!cancelled)
          setLoadError(toErrorKey(err, 'state.errorDescription'));
      });
    return () => {
      cancelled = true;
    };
  }, [props.open, props.client, props.workspaceId, props.sessionId, props.initialBody, props.targetIssueId, props.attachmentIds, reloadKey]);

  const handleSubmit = useCallback(async () => {
    if (preview === null || submitting) return;
    setSubmitting(true);
    try {
      await createComment(props.client, preview.target_issue.id, {
        body_markdown: body,
        suppress_triggers: suppress,
      });
      toast.addToast(t('chat.distill.success', { title: preview.target_issue.identifier }), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      props.onDistilled(preview.target_issue.id, preview.target_issue.identifier);
    } catch (err) {
      toast.addToast(t(toErrorKey(err)), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setSubmitting(false);
    }
  }, [preview, submitting, props, body, suppress, toast, t]);

  const showSuppress =
    preview !== null && preview.suppress_triggers_supported && preview.can_trigger_agents;

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      title={t('chat.distill.title')}
      closeLabel={t('a11y.closeDialog')}
    >
      <div className="mesh-chat__distill" data-testid="chat-distill">
        {loadError !== null ? (
          <ErrorState
            title={t('state.errorTitle')}
            description={t(loadError)}
            retryLabel={t('common.retry')}
            onRetry={() => setReloadKey((key) => key + 1)}
          />
        ) : preview === null ? (
          <Skeleton loadingLabel={t('common.loading')} />
        ) : (
          <>
            <p className="mesh-chat__distill-target" data-testid="chat-distill-target">
              {t('chat.distill.targetLabel')}
              <strong>
                {preview.target_issue.identifier} · {preview.target_issue.title}
              </strong>
            </p>

            <label className="mesh-chat__distill-body-label" htmlFor="chat-distill-body">
              {t('chat.distill.bodyLabel')}
            </label>
            <textarea
              id="chat-distill-body"
              className="mesh-chat__distill-body"
              data-testid="chat-distill-body"
              rows={8}
              value={body}
              onChange={(event) => setBody(event.target.value)}
            />

            {preview.attachments.length > 0 ? (
              <ul className="mesh-chat__distill-attachments" data-testid="chat-distill-attachments">
                {preview.attachments.map((attachment) => (
                  <li key={attachment.id}>{attachment.file_name}</li>
                ))}
              </ul>
            ) : null}

            {preview.triggered_agents.length > 0 ? (
              <p className="mesh-chat__distill-trigger" data-testid="chat-distill-trigger">
                {t('chat.distill.triggerHint', {
                  names: preview.triggered_agents.map((agent) => agent.name).join(', '),
                })}
              </p>
            ) : null}

            {showSuppress ? (
              <label className="mesh-chat__distill-suppress">
                <input
                  type="checkbox"
                  data-testid="chat-distill-suppress"
                  checked={suppress}
                  onChange={(event) => setSuppress(event.target.checked)}
                />
                {t('chat.distill.suppress')}
              </label>
            ) : null}

            <div className="mesh-chat__distill-actions">
              <Button variant="secondary" data-testid="chat-distill-cancel" onClick={props.onClose}>
                {t('common.cancel')}
              </Button>
              <Button
                data-testid="chat-distill-submit"
                disabled={body.trim() === ''}
                isLoading={submitting}
                onClick={() => void handleSubmit()}
              >
                {t('chat.distill.submit')}
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
