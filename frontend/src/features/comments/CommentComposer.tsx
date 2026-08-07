/**
 * 评论输入框 composer(comment-inbox.md §4.1 第 5 点 / §4.3):
 * Markdown 编辑 + 预览切换(marked + DOMPurify 本地净化)、@ 自动补全(人/agent 混排)、
 * agent 副作用提示条 + trigger preview + 显式抑制开关「仅通知,不触发运行」、
 * Cmd/Ctrl+Enter 提交、按 issue 草稿本地暂存、乐观提交(失败保留草稿供重试)。
 * 数据获取/乐观落在父级(onSubmit 返回 Promise);本组件只编排输入态。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { Button, Checkbox, Textarea } from '../../design';
import { useT } from '../../i18n';
import { MentionAutocomplete } from './MentionAutocomplete';
import { renderMarkdownPreview } from './markdown';
import { filterCandidates, insertMention, parseMentionQuery, triggeredAgents } from './mentions';
import type { MentionCandidate } from './mentions';
import { useCommentDraft } from './useCommentDraft';
import { useDraftSaveIndicator } from './useDraftSaveIndicator';

export interface CommentComposerProps {
  readonly draftKey: string;
  readonly candidates: readonly MentionCandidate[];
  readonly replyToName?: string | null;
  /** 父级执行乐观插入 + API 调用;reject 时本组件保留草稿并呈现重试。 */
  readonly onSubmit: (body: string, opts: { suppressTriggers: boolean }) => Promise<void>;
  readonly autoFocus?: boolean;
  /**
   * L242 脏态上报:草稿非空且未能持久化(localStorage 不可用)时为 true,
   * 供父级挂离开确认;草稿已写穿本地存储时不打扰导航。
   */
  readonly onDirtyChange?: (dirty: boolean) => void;
}

type SubmitState = 'idle' | 'sending' | 'error';

export function CommentComposer(props: CommentComposerProps): React.JSX.Element {
  const t = useT();
  const draft = useCommentDraft(props.draftKey);
  // 草稿自动保存弱提示(§9.5.1):写穿已在 useCommentDraft 完成,此处仅映射可视状态。
  const draftIndicator = useDraftSaveIndicator(draft.value);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionStart, setMentionStart] = useState(0);
  const [mentionCursor, setMentionCursor] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [suppress, setSuppress] = useState(false);
  const [previewMode, setPreviewMode] = useState(false);
  const [submitState, setSubmitState] = useState<SubmitState>('idle');

  // L242:仅当草稿因存储不可用而只驻留内存时才视为脏(已持久化的草稿导航不丢)。
  const draftDirty = draft.value.trim() !== '' && !draft.persisted;
  const { onDirtyChange } = props;
  useEffect(() => {
    onDirtyChange?.(draftDirty);
    // 卸载/键切换时摘除本 composer 的脏标记,避免残留。
    return () => onDirtyChange?.(false);
  }, [draftDirty, onDirtyChange]);

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

  // 草稿弱提示文案(§9.5.1):dirty/saving → 「保存中」;saved → 「已保存 · 绝对时间」。
  // 绝对时间保证读屏/悬浮可得知确切时刻(§10.3),视觉上弱化为 caption/muted。
  const draftStatusText = useMemo(() => {
    if (draftIndicator.status === 'dirty' || draftIndicator.status === 'saving') {
      return t('comments.draftSaving');
    }
    if (draftIndicator.status === 'saved' && draftIndicator.savedAt !== null) {
      return t('comments.draftSaved', {
        time: new Date(draftIndicator.savedAt).toLocaleTimeString(),
      });
    }
    return null;
  }, [draftIndicator.status, draftIndicator.savedAt, t]);

  return (
    <div className="mesh-comments__composer" data-testid="comment-composer">
      {props.replyToName !== null && props.replyToName !== undefined ? (
        <p className="mesh-comments__reply-hint" data-testid="reply-hint">
          {t('comments.composer.replyTo', { name: props.replyToName })}
        </p>
      ) : null}

      {/* 草稿恢复弱提示(§9.5.1):本地有草稿被载入空编辑器时一次性提示,用户编辑后即消失。 */}
      {draft.restored ? (
        <p className="mesh-comments__draft-restored" data-testid="draft-restored">
          {t('comments.draftRestored')}
        </p>
      ) : null}

      <div className="mesh-comments__composer-toolbar">
        <Button
          variant="secondary"
          size="sm"
          data-testid="composer-preview-toggle"
          aria-pressed={previewMode}
          onClick={() => setPreviewMode((mode) => !mode)}
        >
          {previewMode ? t('comments.composer.edit') : t('comments.composer.preview')}
        </Button>
      </div>

      {previewMode ? (
        <div
          className="mesh-comments__composer-preview"
          data-testid="composer-preview"
          // 本地预览:marked 解析后经 DOMPurify 白名单净化(markdown.ts),非原始用户文本。
          dangerouslySetInnerHTML={{ __html: previewHtml }}
        />
      ) : (
        <Textarea
          className="mesh-comments__composer-input"
          data-testid="composer-input"
          value={draft.value}
          label={t('comments.composer.placeholder')}
          placeholder={t('comments.composer.placeholder')}
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
        <Checkbox
          className="mesh-comments__suppress"
          data-testid="composer-suppress"
          label={t('comments.composer.suppress')}
          checked={suppress}
          onChange={(event) => setSuppress(event.target.checked)}
        />

        {!suppress && agents.length > 0 ? (
          <span className="mesh-comments__trigger-preview" data-testid="trigger-preview">
            {t('comments.composer.triggerPreview', {
              names: agents.map((agent) => agent.name).join(', '),
            })}
          </span>
        ) : null}

        {/* 草稿自动保存弱提示(§9.5.1):aria-live=polite 供读屏,视觉上弱化为 muted caption。 */}
        {draftStatusText !== null ? (
          <span
            className="mesh-comments__draft-status"
            aria-live="polite"
            data-testid="draft-status"
          >
            {draftStatusText}
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
        /* 失败四部分(§7.7):发生了什么(发送失败)+ 哪部分受影响/已保留(正文/提及/附件保留)+
           可执行恢复动作(重试)。role=alert 原位提示,不清空任何输入(§9.5.4)。 */
        <div className="mesh-comments__composer-error" role="alert" data-testid="composer-error">
          <p className="mesh-comments__composer-error-text">{t('comments.composer.failedKeep')}</p>
          <Button
            variant="secondary"
            size="sm"
            className="mesh-comments__composer-retry"
            data-testid="composer-retry"
            onClick={() => void submit()}
          >
            {t('comments.retry')}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
