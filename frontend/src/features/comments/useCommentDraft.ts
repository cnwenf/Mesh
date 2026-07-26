/**
 * 评论草稿本地暂存(comment-inbox.md C14 / §4.3):按 issue 维度持久化到 localStorage,
 * 刷新/切走不丢,提交成功后清除。读写在边界处兜底(JSON 损坏 / 存储不可用一律降级为空串)。
 */
import { useCallback, useEffect, useState } from 'react';

const DRAFT_PREFIX = 'mesh.comments.draft.';

function draftStorageKey(key: string): string {
  return DRAFT_PREFIX + key;
}

function readDraft(key: string): string {
  try {
    const raw = window.localStorage.getItem(draftStorageKey(key));
    return typeof raw === 'string' ? raw : '';
  } catch {
    return '';
  }
}

function writeDraft(key: string, value: string): void {
  try {
    if (value === '') window.localStorage.removeItem(draftStorageKey(key));
    else window.localStorage.setItem(draftStorageKey(key), value);
  } catch {
    // 存储不可用(隐私模式等):草稿仅驻留内存,降级而不报错。
  }
}

export interface CommentDraft {
  readonly value: string;
  readonly setValue: (value: string) => void;
  readonly clear: () => void;
}

/** 每个 key 一份草稿;value 变更即写穿 localStorage。 */
export function useCommentDraft(key: string): CommentDraft {
  const [value, setValueState] = useState<string>(() => readDraft(key));

  // key 切换时重新装载对应草稿(同一 composer 复用切回复目标/issue 的场景)。
  useEffect(() => {
    setValueState(readDraft(key));
  }, [key]);

  const setValue = useCallback(
    (next: string) => {
      setValueState(next);
      writeDraft(key, next);
    },
    [key],
  );

  const clear = useCallback(() => {
    setValueState('');
    writeDraft(key, '');
  }, [key]);

  return { value, setValue, clear };
}
