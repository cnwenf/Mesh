/**
 * 邮箱 chip 输入(workspace.md §4.2 邀请面板:回车成 chip,支持粘贴批量)。
 *
 * 受控组件:emails + onChange;回车/逗号/分号提交,粘贴按分隔符批量拆分;
 * 小写归一 + 去重;非法格式不入 chip 而以提示文案呈现(权威校验在后端)。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:组件与同域纯函数/常量同文件共存 */
import { useId, useState } from 'react';
import type { ClipboardEvent, KeyboardEvent } from 'react';
import { isValidEmail } from './permissions';

/** 单次批量上限(后端 ≤50,workspace.md §3.4) */
export const MAX_BATCH_EMAILS = 50;

const SEPARATOR_PATTERN = /[,;\s]+/;

export interface EmailChipsInputProps {
  label: string;
  emails: readonly string[];
  onChange(emails: string[]): void;
  placeholder?: string;
  invalidFormatHint?: string;
  maxCountHint?: string;
  removeLabel?: string;
}

/** 将自由文本拆分为候选邮箱(去空、小写归一) */
export function splitEmailInput(text: string): string[] {
  return text
    .split(SEPARATOR_PATTERN)
    .map((part) => part.trim().toLowerCase())
    .filter((part) => part.length > 0);
}

export function EmailChipsInput(props: EmailChipsInputProps): React.JSX.Element {
  const { label, emails, onChange } = props;
  const inputId = useId();
  const [draft, setDraft] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  const commit = (candidates: string[]): void => {
    let invalid = false;
    let overflow = false;
    const next = [...emails];
    for (const candidate of candidates) {
      if (!isValidEmail(candidate)) {
        invalid = true;
        continue;
      }
      if (next.includes(candidate)) continue;
      if (next.length >= MAX_BATCH_EMAILS) {
        overflow = true;
        continue;
      }
      next.push(candidate);
    }
    if (overflow && props.maxCountHint !== undefined) {
      setNotice(props.maxCountHint);
    } else if (invalid && props.invalidFormatHint !== undefined) {
      setNotice(props.invalidFormatHint);
    } else {
      setNotice(null);
    }
    if (next.length !== emails.length || next.some((email, index) => email !== emails[index])) {
      onChange(next);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key !== 'Enter' && event.key !== ',' && event.key !== ';') return;
    event.preventDefault();
    const candidates = splitEmailInput(draft);
    if (candidates.length === 0) return;
    commit(candidates);
    setDraft('');
  };

  const handlePaste = (event: ClipboardEvent<HTMLInputElement>): void => {
    const text = event.clipboardData.getData('text');
    const candidates = splitEmailInput(text);
    if (candidates.length === 0) return;
    event.preventDefault();
    commit(candidates);
    setDraft('');
  };

  const removeEmail = (email: string): void => {
    onChange(emails.filter((existing) => existing !== email));
  };

  return (
    <div className="mesh-chips">
      <label htmlFor={inputId}>{label}</label>
      {emails.length > 0 ? (
        <ul className="mesh-chips__list" aria-label={label}>
          {emails.map((email) => (
            <li key={email} className="mesh-chips__chip" data-testid="email-chip">
              <span>{email}</span>
              <button
                type="button"
                className="mesh-chips__remove"
                aria-label={`${props.removeLabel ?? 'remove'} ${email}`}
                onClick={() => removeEmail(email)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <input
        id={inputId}
        data-testid="email-chips-input"
        type="text"
        value={draft}
        placeholder={props.placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />
      {notice !== null ? (
        <p className="mesh-chips__notice" role="alert" data-testid="email-chips-notice">
          {notice}
        </p>
      ) : null}
    </div>
  );
}
