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

function writeDraft(key: string, value: string): boolean {
  try {
    if (value === '') window.localStorage.removeItem(draftStorageKey(key));
    else window.localStorage.setItem(draftStorageKey(key), value);
    return true;
  } catch {
    // 存储不可用(隐私模式等):草稿仅驻留内存,降级而不报错。
    return false;
  }
}

export interface CommentDraft {
  readonly value: string;
  readonly setValue: (value: string) => void;
  readonly clear: () => void;
  /**
   * 当前值是否来自本地草稿恢复(而非用户本次输入)。
   * 用户一旦编辑(setValue)或清除即转 false——供「已恢复草稿」一次性弱提示判定。
   */
  readonly restored: boolean;
  /**
   * 最近一次写入是否已持久化到 localStorage(L242)。
   * 存储不可用(隐私模式等)时为 false——草稿仅驻留内存,导航离开会丢,
   * 供脏态离开确认判定;清空后无内容可丢,恒回 true。
   */
  readonly persisted: boolean;
}

/** 每个 key 一份草稿;value 变更即写穿 localStorage。 */
export function useCommentDraft(key: string): CommentDraft {
  const [value, setValueState] = useState<string>(() => readDraft(key));
  const [restored, setRestored] = useState<boolean>(() => readDraft(key) !== '');
  const [persisted, setPersisted] = useState(true);

  // key 切换时重新装载对应草稿(同一 composer 复用切回复目标/issue 的场景)。
  useEffect(() => {
    const loaded = readDraft(key);
    setValueState(loaded);
    setRestored(loaded !== '');
    setPersisted(true);
  }, [key]);

  const setValue = useCallback(
    (next: string) => {
      setValueState(next);
      setRestored(false);
      setPersisted(writeDraft(key, next));
    },
    [key],
  );

  const clear = useCallback(() => {
    setValueState('');
    setRestored(false);
    writeDraft(key, '');
    // 清空后无内容可丢:即便 removeItem 失败也不构成脏态。
    setPersisted(true);
  }, [key]);

  return { value, setValue, clear, restored, persisted };
}
