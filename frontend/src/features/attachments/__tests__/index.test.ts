/**
 * 附件模块公共 API 桶导出校验:确保进度环/捏合计算/格式化工具均经桶可达。
 */
import { describe, expect, it } from 'vitest';
import * as attachments from '../index';

describe('attachments public barrel', () => {
  it('exports components, hooks, and helpers', () => {
    expect(attachments.AttachmentPanel).toBeTypeOf('function');
    expect(attachments.AttachmentComposer).toBeTypeOf('function');
    expect(attachments.Lightbox).toBeTypeOf('function');
    expect(attachments.FileIcon).toBeTypeOf('function');
    expect(attachments.ProgressRing).toBeTypeOf('function');
    expect(attachments.clampPercent).toBeTypeOf('function');
    expect(attachments.formatFileSize).toBeTypeOf('function');
    expect(attachments.pinchScale).toBeTypeOf('function');
    expect(attachments.pointerDistance).toBeTypeOf('function');
    expect(attachments.doubleTapScale).toBeTypeOf('function');
    expect(attachments.useAttachmentUploader).toBeTypeOf('function');
    expect(attachments.LIGHTBOX_MAX_SCALE).toBeTypeOf('number');
  });
});
