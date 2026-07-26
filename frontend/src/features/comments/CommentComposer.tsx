/**
 * 评论输入框 composer(comment-inbox.md §4.1 第 5 点 / §4.3):
 * Markdown 编辑 + 预览切换(marked + DOMPurify 本地净化)、@ 自动补全(人/agent 混排)、
 * agent 副作用提示条 + trigger preview + 显式抑制开关「仅通知,不触发运行」、
 * Cmd/Ctrl+Enter 提交、按 issue 草稿本地暂存、乐观提交(失败保留草稿供重试)。
 * 数据获取/乐观落在父级(onSubmit 返回 Promise);本组件只编排输入态。
 */
import { useCallback, useMemo, useState } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { Button } from '../../design';
import { useT } from '../../i18n';
import { MentionAutocomplete } from './MentionAutocomplete';
import { renderMarkdownPreview } from './markdown';
import {
  filterCandidates,
  insertMention,
  parseMentionQuery,
  triggeredAgents,
} from './mentions';
import type { MentionCandidate } from './mentions';
import { useCommentDraft } from './useCommentDraft';

export interface CommentComposerProps {
  readonly draftKey: string;
  readonly candidates: readonly MentionCandidate[];
  readonly replyToName?: string | null;
  /** 父级执行乐观插入 + API 调用;reject 时本组件保留草稿并呈现重试。 */
  readonly onSubmit: (body: string, opts: { suppressTriggers: boolean }) => Promise<void>;
  readonly autoFocus?: boolean;
}

type SubmitState = 'idle' | 'sending' | 'error';

export function CommentComposer(props: CommentComposerProps): React.JSX.Element {
  const t = useT();
  const draft = useCommentDraft(props.draftKey);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionStart, setMentionStart] = useState(0);
  const [mentionCursor, setMentionCursor] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [suppress, setSuppress] = useState(false);
  const [previewMode, setPreviewMode] = useState(false);
  const [submitState, setSubmitState] = useState<SubmitState>('idle');

  const filtered = useMemo(
    () => filterCandidates(props.candidates, mentionQuery),
    [props.candidates, mentionQuery],
  );
  const agents = useMemo(
    () => triggeredAgents(draft.value, props.candidates),
    [draft.value, props.candidates],
  );

  const handleChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      const value = event.target.value;
      const cursor = event.target.selectionStart ?? value.length;
      draft.setValue(value);
      const query = parseMentionQuery(value, cursor);
      if (query === null) {
        setMentionOpen(false);
        return;
      }
      setMentionOpen(true);
      setMentionQuery(query.query);
      setMentionStart(query.start);
      setMentionCursor(cursor);
      setActiveIndex(0);
    },
    [draft],
  );

  const selectMention = useCallback(
    (member: MentionCandidate) => {
      const next = insertMention(draft.value, mentionStart, mentionCursor, member);
      draft.setValue(next);
      setMentionOpen(false);
    },
    [draft, mentionStart, mentionCursor],
  );

  const submit = useCallback(async () => {
    const body = draft.value.trim();
    if (body === '' || submitState === 'sending') return;
    setSubmitState('sending');
    try {
      await props.onSubmit(body, { suppressTriggers: suppress });
      draft.clear();
      setSubmitState('idle');
    } catch {
      setSubmitState('error');
    }
  }, [draft, props, submitState, suppress]);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (mentionOpen && filtered.length > 0) {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          setActiveIndex((index) => (index + 1) % filtered.length);
          return;
        }
        if (event.key === 'ArrowUp') {
          event.preventDefault();
          setActiveIndex((index) => (index - 1 + filtered.length) % filtered.length);
          return;
        }
        if (event.key === 'Enter') {
          event.preventDefault();
          const member = filtered[activeIndex];
          if (member !== undefined) selectMention(member);
          return;
        }
        if (event.key === 'Escape') {
          event.preventDefault();
          setMentionOpen(false);
          return;
        }
      }
      if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        void submit();
      }
    },
    [mentionOpen, filtered, activeIndex, selectMention, submit],
  );

  const previewHtml = previewMode ? renderMarkdownPreview(draft.value) : '';

  return (
    <div className="mesh-comments__composer" data-testid="comment-composer">
      {props.replyToName !== null && props.replyToName !== undefined ? (
        <p className="mesh-comments__reply-hint" data-testid="reply-hint">
          {t('comments.composer.replyTo', { name: props.replyToName })}
        </p>
      ) : null}

      <div className="mesh-comments__composer-toolbar">
        <button
          type="button"
          data-testid="composer-preview-toggle"
          aria-pressed={previewMode}
          onClick={() => setPreviewMode((mode) => !mode)}
        >
          {previewMode ? t('comments.composer.edit') : t('comments.composer.preview')}
        </button>
      </div>

      {previewMode ? (
        <div
          className="mesh-comments__composer-preview"
          data-testid="composer-preview"
          // 本地预览:marked 解析后经 DOMPurify 白名单净化(markdown.ts),非原始用户文本。
          dangerouslySetInnerHTML={{ __html: previewHtml }}
        />
      ) : (
        <textarea
          className="mesh-comments__composer-input"
          data-testid="composer-input"
          value={draft.value}
          placeholder={t('comments.composer.placeholder')}
          aria-label={t('comments.composer.placeholder')}
          autoFocus={props.autoFocus}
          rows={3}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
        />
      )}

      {mentionOpen && filtered.length > 0 ? (
        <MentionAutocomplete
          candidates={filtered}
          activeIndex={activeIndex}
          onSelect={selectMention}
          onHover={setActiveIndex}
        />
      ) : null}

      {agents.length > 0 ? (
        <p className="mesh-comments__trigger-hint" data-testid="trigger-hint">
          {t('comments.composer.agentMentionHint', {
            names: agents.map((agent) => agent.name).join(', '),
          })}
        </p>
      ) : null}

      <div className="mesh-comments__composer-foot">
        <label className="mesh-comments__suppress">
          <input
            type="checkbox"
            data-testid="composer-suppress"
            checked={suppress}
            onChange={(event) => setSuppress(event.target.checked)}
          />
          {t('comments.composer.suppress')}
        </label>

        {!suppress && agents.length > 0 ? (
          <span className="mesh-comments__trigger-preview" data-testid="trigger-preview">
            {t('comments.composer.triggerPreview', {
              names: agents.map((agent) => agent.name).join(', '),
            })}
          </span>
        ) : null}

        <Button
          size="sm"
          data-testid="composer-submit"
          disabled={draft.value.trim() === '' || submitState === 'sending'}
          isLoading={submitState === 'sending'}
          onClick={() => void submit()}
        >
          {t('comments.composer.submit')}
        </Button>
      </div>

      {submitState === 'error' ? (
        <p className="mesh-comments__composer-error" role="alert" data-testid="composer-error">
          {t('comments.composer.failed')}
          <button type="button" data-testid="composer-retry" onClick={() => void submit()}>
            {t('common.retry')}
          </button>
        </p>
      ) : null}
    </div>
  );
}
