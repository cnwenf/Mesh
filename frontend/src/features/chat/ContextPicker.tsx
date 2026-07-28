/**
 * 上下文 issue 选择器(chat-session.md §4.2)。复用 NewSessionDialog 的 issue 搜索 UI:
 * 输入按需查询(issues 模块 listIssues,后端按可见性过滤,仅返回可访问 issue),
 * 点选 → onPick(issue.id) 设定/更换上下文;「清除」→ onPick(null) 清空。
 * patch 落库与错误 toast 在父级(ContextBar),本组件只编排选择。
 */
import { useCallback, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { Button, Dialog, Input } from '../../design';
import { useT } from '../../i18n';
import { listIssues } from '../issues/api';
import type { IssueSummary } from '../issues/types';

export interface ContextPickerProps {
  readonly open: boolean;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly onClose: () => void;
  /** 选定 issue(或 null 清除);父级据此 patch 并关闭/报错。 */
  readonly onPick: (issueId: string | null) => void;
}

export function ContextPicker(props: ContextPickerProps): React.JSX.Element {
  const t = useT();
  const [query, setQuery] = useState('');
  const [issues, setIssues] = useState<readonly IssueSummary[]>([]);

  // issue 搜索:空查询清空结果;失败静默清空(后端按可见性过滤,404/403 不暴露细节)。
  const search = useCallback(
    async (value: string) => {
      setQuery(value);
      if (value.trim() === '') {
        setIssues([]);
        return;
      }
      try {
        const page = await listIssues(props.client, props.workspaceId, { q: value, limit: 20 });
        setIssues(page.data);
      } catch {
        setIssues([]);
      }
    },
    [props.client, props.workspaceId],
  );

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      title={t('chat.context.pickerTitle')}
      closeLabel={t('a11y.closeDialog')}
    >
      <div className="mesh-chat__new-session" data-testid="chat-context-picker">
        <Input
          label={t('chat.context.pickerTitle')}
          value={query}
          data-testid="chat-context-picker-search"
          placeholder={t('chat.context.pickerPlaceholder')}
          onChange={(event) => void search(event.target.value)}
        />

        {issues.length > 0 ? (
          <ul className="mesh-chat__context-results" data-testid="chat-context-picker-results">
            {issues.map((issue) => (
              <li key={issue.id}>
                <button
                  type="button"
                  className="mesh-chat__context-result"
                  data-testid={`chat-context-picker-option-${issue.id}`}
                  onClick={() => props.onPick(issue.id)}
                >
                  <span className="mesh-chat__context-id">{issue.identifier}</span>
                  <span className="mesh-chat__context-title">{issue.title}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        <div className="mesh-chat__new-session-actions">
          <Button
            variant="secondary"
            data-testid="chat-context-picker-clear"
            onClick={() => props.onPick(null)}
          >
            {t('chat.context.pickerClear')}
          </Button>
          <Button
            variant="secondary"
            data-testid="chat-context-picker-cancel"
            onClick={props.onClose}
          >
            {t('common.cancel')}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
