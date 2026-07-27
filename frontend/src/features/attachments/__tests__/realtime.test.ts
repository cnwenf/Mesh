/**
 * 附件实时帧合并测试(attachment.md §3.7):processed 放行并入既有行 / deleted 按 id 移除。
 * 纯函数契约:无关帧与缺失 id 返回原引用(不整页刷新)。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { applyAttachmentDeleted, applyAttachmentProcessed } from '../realtime';
import type { Attachment } from '../types';

function frame(event: string, payload: Record<string, unknown>): RealtimeEventFrame {
  return { op: 'event', channel: 'issue:iss-1', seq: 1, event, payload };
}

/** 畸形帧构造器(M4):payload 在真实链路上可能不是对象。 */
function malformedFrame(event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel: 'issue:iss-1', seq: 1, event, payload } as RealtimeEventFrame;
}

function attachment(overrides: Partial<Attachment> = {}): Attachment {
  return {
    id: 'att-1',
    blob_id: 'blob-1',
    file_name: 'a.png',
    file_size: 10,
    mime_type: 'image/png',
    extension: 'png',
    is_image: true,
    image_width: 100,
    image_height: 100,
    scan_status: 'pending',
    upload_status: 'completed',
    uploader: null,
    links: [],
    thumbnail_url: null,
    download_url: '/api/v1/attachments/att-1/download',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('applyAttachmentProcessed', () => {
  it('merges scan result into the matching row (pending -> clean + thumbnail)', () => {
    const list = [attachment()];
    const next = applyAttachmentProcessed(
      list,
      frame('attachment.processed', {
        id: 'att-1',
        scan_status: 'clean',
        thumbnail_url: '/api/v1/attachments/att-1/thumbnail?size=md',
      }),
    );
    expect(next).not.toBe(list);
    expect(next[0].scan_status).toBe('clean');
    expect(next[0].thumbnail_url).toContain('thumbnail');
    // 未携带的字段保持不变
    expect(next[0].file_name).toBe('a.png');
  });

  it('returns the original reference when the id is absent from the list', () => {
    const list = [attachment()];
    const next = applyAttachmentProcessed(list, frame('attachment.processed', { id: 'other' }));
    expect(next).toBe(list);
  });

  it('ignores non-attachment events and non-processed actions', () => {
    const list = [attachment()];
    expect(applyAttachmentProcessed(list, frame('issue.updated', { id: 'att-1' }))).toBe(list);
    expect(applyAttachmentProcessed(list, frame('attachment.deleted', { id: 'att-1' }))).toBe(list);
  });

  it('skips prototype-pollution keys and event meta fields (visibility)', () => {
    const list = [attachment()];
    // JSON.parse 产生自有 __proto__ 属性(对象字面量则会改写原型,枚举不到),贴合真实帧来源。
    const payload = JSON.parse(
      '{"id":"att-1","__proto__":{"polluted":true},"visibility":"public","scan_status":"clean"}',
    ) as Record<string, unknown>;
    const next = applyAttachmentProcessed(list, frame('attachment.processed', payload));
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
    expect(next[0].scan_status).toBe('clean');
    expect('visibility' in next[0]).toBe(false);
  });

  it('returns the original reference when id is missing', () => {
    const list = [attachment()];
    expect(applyAttachmentProcessed(list, frame('attachment.processed', {}))).toBe(list);
  });

  it('treats a non-string id as absent', () => {
    const list = [attachment()];
    expect(applyAttachmentProcessed(list, frame('attachment.processed', { id: 123 }))).toBe(list);
  });

  it('ignores events without an entity/action dot', () => {
    const list = [attachment()];
    expect(applyAttachmentProcessed(list, frame('attachment', { id: 'att-1' }))).toBe(list);
    expect(applyAttachmentDeleted(list, frame('attachment', { id: 'att-1' }))).toBe(list);
  });

  it('returns the original reference when the frame carries no mergeable fields', () => {
    const list = [attachment()];
    const next = applyAttachmentProcessed(
      list,
      frame('attachment.processed', { id: 'att-1', visibility: 'public' }),
    );
    expect(next).toBe(list);
  });

  it('only rewrites the matching row in a multi-item list', () => {
    const list = [attachment({ id: 'att-1' }), attachment({ id: 'att-2' })];
    const next = applyAttachmentProcessed(
      list,
      frame('attachment.processed', { id: 'att-2', scan_status: 'clean' }),
    );
    expect(next[0].scan_status).toBe('pending');
    expect(next[1].scan_status).toBe('clean');
  });

  it('returns the list unchanged for null/undefined/non-object payloads (M4, no crash in updater)', () => {
    const list = [attachment()];
    expect(applyAttachmentProcessed(list, malformedFrame('attachment.processed', null))).toBe(list);
    expect(applyAttachmentProcessed(list, malformedFrame('attachment.processed', undefined))).toBe(list);
    expect(applyAttachmentProcessed(list, malformedFrame('attachment.processed', 'att-1'))).toBe(list);
    expect(applyAttachmentProcessed(list, malformedFrame('attachment.processed', ['att-1']))).toBe(list);
  });
});

describe('applyAttachmentDeleted', () => {
  it('removes the row by id', () => {
    const list = [attachment({ id: 'att-1' }), attachment({ id: 'att-2' })];
    const next = applyAttachmentDeleted(list, frame('attachment.deleted', { id: 'att-1' }));
    expect(next.map((item) => item.id)).toEqual(['att-2']);
  });

  it('returns the original reference when the id is not present', () => {
    const list = [attachment()];
    expect(applyAttachmentDeleted(list, frame('attachment.deleted', { id: 'nope' }))).toBe(list);
  });

  it('ignores non-deleted events and missing ids', () => {
    const list = [attachment()];
    expect(applyAttachmentDeleted(list, frame('attachment.processed', { id: 'att-1' }))).toBe(list);
    expect(applyAttachmentDeleted(list, frame('attachment.deleted', {}))).toBe(list);
    expect(applyAttachmentDeleted(list, frame('comment.deleted', { id: 'att-1' }))).toBe(list);
  });

  it('returns the list unchanged for null/undefined/non-object payloads (M4, no crash in updater)', () => {
    const list = [attachment()];
    expect(applyAttachmentDeleted(list, malformedFrame('attachment.deleted', null))).toBe(list);
    expect(applyAttachmentDeleted(list, malformedFrame('attachment.deleted', undefined))).toBe(list);
    expect(applyAttachmentDeleted(list, malformedFrame('attachment.deleted', 42))).toBe(list);
  });
});
