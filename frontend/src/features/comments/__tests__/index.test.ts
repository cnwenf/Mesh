/**
 * 评论模块公共 API 桶导出校验:确保新增的运行五态/滚动高亮/延迟删除/草稿提示均经桶可达。
 */
import { describe, expect, it } from 'vitest';
import * as comments from '../index';

describe('comments public barrel', () => {
  it('exports components, hooks, and helpers', () => {
    expect(comments.CommentsPanel).toBeTypeOf('function');
    expect(comments.CommentComposer).toBeTypeOf('function');
    expect(comments.CommentCard).toBeTypeOf('function');
    expect(comments.RunStatus).toBeTypeOf('function');
    expect(comments.scrollToAndHighlight).toBeTypeOf('function');
    expect(comments.useDeferredDelete).toBeTypeOf('function');
    expect(comments.useDraftSaveIndicator).toBeTypeOf('function');
    expect(comments.UNDO_WINDOW_MS).toBeTypeOf('number');
    expect(comments.HIGHLIGHT_CLASS).toBeTypeOf('string');
    expect(comments.RUN_STATUS_CONFIG).toBeTypeOf('object');
    expect(comments.DRAFT_SAVE_DEBOUNCE_MS).toBeTypeOf('number');
    expect(comments.listComments).toBeTypeOf('function');
  });
});
