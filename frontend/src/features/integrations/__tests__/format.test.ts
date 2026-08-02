/**
 * format.ts 展示层纯函数测试(integrations.md §4)。
 */
import { describe, expect, it } from 'vitest';
import { ICON_PATHS } from '../../../design';
import {
  BINDING_STATUS_TONE,
  DINGTALK_STREAM_STATE_TONE,
  DELIVERY_STATE_TONE,
  HEALTH_STATE_TONE,
  INTEGRATION_STATUS_TONE,
  KIND_ICON,
  PROCESS_STATUS_TONE,
  QUEUE_STATE_TONE,
  SIGNATURE_STATUS_TONE,
  SUBSCRIPTION_STATUS_TONE,
  VCS_LINK_STATUS_TONE,
  formatExternalState,
  conversationDisplayName,
  externalIdentityTriple,
  formatQueueDuration,
  formatRelativeTime,
  formatSuccessRate,
  isHttpsUrl,
  isSafeWebUrl,
  isTripped,
  sanitizeMessageExcerpt,
  toDingTalkStreamState,
  toHealthState,
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

  it('maps every kind to a registered SVG icon name', () => {
    expect(KIND_ICON.im_feishu).toBe('message');
    expect(KIND_ICON.im_slack).toBe('chat');
    expect(KIND_ICON.vcs_github).toBe('git-merge');
    expect(KIND_ICON.vcs_gitlab).toBe('git-merge');
    expect(KIND_ICON.webhook_outbound).toBe('upload');
    for (const name of Object.values(KIND_ICON)) {
      expect(Object.keys(ICON_PATHS)).toContain(name);
    }
  });

  it('maps health states to semantic tones', () => {
    expect(HEALTH_STATE_TONE.unknown).toBe('neutral');
    expect(HEALTH_STATE_TONE.healthy).toBe('success');
    expect(HEALTH_STATE_TONE.auth_failed).toBe('danger');
    expect(HEALTH_STATE_TONE.unreachable).toBe('warn');
  });

  it('maps every DingTalk stream and queue state with text-compatible tones', () => {
    expect(DINGTALK_STREAM_STATE_TONE.connected).toBe('success');
    expect(DINGTALK_STREAM_STATE_TONE.reconnecting).toBe('warn');
    expect(DINGTALK_STREAM_STATE_TONE.down).toBe('danger');
    expect(DINGTALK_STREAM_STATE_TONE.disabled).toBe('neutral');
    expect(QUEUE_STATE_TONE.processing).toBe('info');
    expect(QUEUE_STATE_TONE.cancelling).toBe('warn');
    expect(QUEUE_STATE_TONE.failed).toBe('danger');
  });
});

describe('DingTalk queue display guards', () => {
  it('narrows unknown stream states to down', () => {
    expect(toDingTalkStreamState('connected')).toBe('connected');
    expect(toDingTalkStreamState('disabled')).toBe('disabled');
    expect(toDingTalkStreamState('future-value')).toBe('down');
  });

  it('extracts only the opaque conversation ref after provider and tenant', () => {
    expect(conversationDisplayName('dingtalk:dingCorp01:cid6EUvB2O8qVF2RYQtHTKEsg==')).toBe(
      'cid6EUvB2O8qVF2RYQtHTKEsg==',
    );
    expect(conversationDisplayName('malformed')).toBe('malformed');
  });

  it('removes control characters and caps excerpts at 120 Unicode characters', () => {
    const dirty = `hello\u0000\u0007\nworld\u0085\u200b\u202e\u2066\ufeff   ${'界'.repeat(130)}`;
    const clean = sanitizeMessageExcerpt(dirty);
    expect(clean).not.toContain('\u0085');
    expect(clean).not.toContain('\u200b');
    expect(clean).not.toContain('\u202e');
    expect(clean).not.toContain('\u2066');
    expect(clean).not.toContain('\ufeff');
    expect(Array.from(clean)).toHaveLength(120);
    expect(clean.startsWith('helloworld ')).toBe(true);
  });

  it('mirrors backend excerpt hygiene for soft-hyphen, joiner and bidi ranges', () => {
    expect(sanitizeMessageExcerpt('  a\u00adb\u180ec\u200dd\u2029e\u2064f\u206fg  h  ')).toBe(
      'abcdefg h',
    );
  });

  it('builds the full identity triple used for ownership checks', () => {
    expect(
      externalIdentityTriple({
        provider: 'dingtalk',
        provider_tenant_key: 'dingCorp01',
        external_user_key: 'staff-1',
      }),
    ).toBe('dingtalk:dingCorp01:staff-1');
  });

  it('formats active and completed queue runtimes defensively', () => {
    expect(
      formatQueueDuration('2026-08-01T10:00:00Z', null, Date.parse('2026-08-01T10:02:03Z')),
    ).toBe('2:03');
    expect(
      formatQueueDuration(
        '2026-08-01T10:00:00Z',
        '2026-08-01T11:02:03Z',
        Date.parse('2026-08-01T12:00:00Z'),
      ),
    ).toBe('1:02:03');
    expect(
      formatQueueDuration(
        '2026-08-01T10:00:02Z',
        '2026-08-01T10:00:00Z',
        Date.parse('2026-08-01T12:00:00Z'),
      ),
    ).toBe('0:00');
    expect(formatQueueDuration('not-a-date', null, Date.now())).toBeNull();
    expect(formatQueueDuration('2026-08-01T10:00:00Z', 'also-not-a-date', Date.now())).toBeNull();
  });
});

describe('toHealthState', () => {
  it('passes through known states and narrows unknown values', () => {
    expect(toHealthState('healthy')).toBe('healthy');
    expect(toHealthState('auth_failed')).toBe('auth_failed');
    expect(toHealthState('unreachable')).toBe('unreachable');
    expect(toHealthState('bogus')).toBe('unknown');
    expect(toHealthState('')).toBe('unknown');
  });
});

describe('formatSuccessRate', () => {
  it('renders an em dash for null or non-finite rates', () => {
    expect(formatSuccessRate(null)).toBe('—');
    expect(formatSuccessRate(Number.NaN)).toBe('—');
    expect(formatSuccessRate(Number.POSITIVE_INFINITY)).toBe('—');
  });

  it('renders rounded integer percentages', () => {
    expect(formatSuccessRate(0.95)).toBe('95%');
    expect(formatSuccessRate(0.951)).toBe('95%');
    expect(formatSuccessRate(1)).toBe('100%');
    expect(formatSuccessRate(0)).toBe('0%');
  });
});

describe('isSafeWebUrl', () => {
  it('accepts http(s) and rejects executable or invalid schemes', () => {
    expect(isSafeWebUrl('https://github.com/owner/repo/pull/1')).toBe(true);
    expect(isSafeWebUrl('http://gitlab.internal/owner/repo/-/commit/abc')).toBe(true);
    expect(isSafeWebUrl('javascript:alert(1)')).toBe(false);
    expect(isSafeWebUrl('data:text/html,x')).toBe(false);
    expect(isSafeWebUrl('not a url')).toBe(false);
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
