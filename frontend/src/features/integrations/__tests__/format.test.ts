/**
 * format.ts 展示层纯函数测试(integrations.md §4)。
 */
import { describe, expect, it } from 'vitest';
import {
  BINDING_STATUS_TONE,
  DELIVERY_STATE_TONE,
  INTEGRATION_STATUS_TONE,
  KIND_ICON,
  PROCESS_STATUS_TONE,
  SIGNATURE_STATUS_TONE,
  SUBSCRIPTION_STATUS_TONE,
  VCS_LINK_STATUS_TONE,
  formatExternalState,
  formatRelativeTime,
  isHttpsUrl,
  isTripped,
} from '../format';

describe('tone maps', () => {
  it('maps integration + signature + process statuses', () => {
    expect(INTEGRATION_STATUS_TONE.active).toBe('success');
    expect(INTEGRATION_STATUS_TONE.disabled).toBe('neutral');
    expect(SIGNATURE_STATUS_TONE.valid).toBe('success');
    expect(SIGNATURE_STATUS_TONE.invalid).toBe('danger');
    expect(SIGNATURE_STATUS_TONE.missing).toBe('warn');
    expect(PROCESS_STATUS_TONE.dispatched).toBe('success');
    expect(PROCESS_STATUS_TONE.rejected).toBe('danger');
    expect(PROCESS_STATUS_TONE.deduped).toBe('warn');
    expect(PROCESS_STATUS_TONE.received).toBe('info');
  });

  it('maps subscription, delivery, binding and vcs statuses', () => {
    expect(SUBSCRIPTION_STATUS_TONE.disabled).toBe('danger');
    expect(SUBSCRIPTION_STATUS_TONE.paused).toBe('warn');
    expect(DELIVERY_STATE_TONE.sent).toBe('success');
    expect(DELIVERY_STATE_TONE.failed).toBe('danger');
    expect(DELIVERY_STATE_TONE.pending).toBe('info');
    expect(BINDING_STATUS_TONE.active).toBe('success');
    expect(VCS_LINK_STATUS_TONE.stale).toBe('warn');
    expect(VCS_LINK_STATUS_TONE.deleted).toBe('neutral');
  });

  it('maps every kind to an icon', () => {
    expect(KIND_ICON.im_feishu).toBeTruthy();
    expect(KIND_ICON.vcs_github).toBeTruthy();
    expect(KIND_ICON.webhook_outbound).toBeTruthy();
  });
});

describe('formatRelativeTime', () => {
  const now = Date.parse('2026-07-29T12:00:00Z');

  it('returns null for null or invalid input', () => {
    expect(formatRelativeTime(null, now, 'en')).toBeNull();
    expect(formatRelativeTime('not-a-date', now, 'en')).toBeNull();
  });

  it('formats seconds / minutes / hours / days', () => {
    expect(formatRelativeTime('2026-07-29T11:59:30Z', now, 'en')).toContain('second');
    expect(formatRelativeTime('2026-07-29T11:30:00Z', now, 'en')).toContain('minute');
    expect(formatRelativeTime('2026-07-29T06:00:00Z', now, 'en')).toContain('hour');
    expect(formatRelativeTime('2026-07-25T12:00:00Z', now, 'en')).toContain('day');
  });
});

describe('isHttpsUrl', () => {
  it('accepts https only', () => {
    expect(isHttpsUrl('https://example.com/hook')).toBe(true);
    expect(isHttpsUrl('http://example.com/hook')).toBe(false);
    expect(isHttpsUrl('ftp://example.com')).toBe(false);
    expect(isHttpsUrl('not a url')).toBe(false);
    expect(isHttpsUrl('   ')).toBe(false);
  });
});

describe('isTripped', () => {
  it('is tripped only when disabled with failures', () => {
    expect(isTripped({ status: 'disabled', fail_count: 3 })).toBe(true);
    expect(isTripped({ status: 'disabled', fail_count: 0 })).toBe(false);
    expect(isTripped({ status: 'paused', fail_count: 3 })).toBe(false);
    expect(isTripped({ status: 'active', fail_count: 0 })).toBe(false);
  });
});

describe('formatExternalState', () => {
  it('returns null for null or empty', () => {
    expect(formatExternalState(null)).toBeNull();
    expect(formatExternalState({})).toBeNull();
  });

  it('joins string/number entries and drops others', () => {
    expect(formatExternalState({ pr_state: 'merged', count: 2, nested: { a: 1 } })).toBe(
      'pr_state=merged · count=2',
    );
  });
});
