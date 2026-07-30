/**
 * types.ts 运行时常量测试(连接器目录 / kind 枚举 / VCS 对象类型)。
 */
import { describe, expect, it } from 'vitest';
import { ICON_PATHS } from '../../../design';
import {
  CONNECTOR_CATALOG,
  INTEGRATION_HEALTH_STATES,
  INTEGRATION_KINDS,
  OAUTH_KINDS,
  VCS_OBJECT_TYPES,
} from '../types';

describe('integration kind constants', () => {
  it('enumerates the five connector kinds', () => {
    expect(INTEGRATION_KINDS).toEqual([
      'im_feishu',
      'im_slack',
      'vcs_github',
      'vcs_gitlab',
      'webhook_outbound',
    ]);
  });

  it('enumerates the four health states', () => {
    expect(INTEGRATION_HEALTH_STATES).toEqual(['unknown', 'healthy', 'auth_failed', 'unreachable']);
  });

  it('flags exactly the oauth-capable kinds', () => {
    expect(OAUTH_KINDS.has('im_feishu')).toBe(true);
    expect(OAUTH_KINDS.has('im_slack')).toBe(true);
    expect(OAUTH_KINDS.has('vcs_github')).toBe(true);
    expect(OAUTH_KINDS.has('vcs_gitlab')).toBe(true);
    expect(OAUTH_KINDS.has('webhook_outbound')).toBe(false);
  });

  it('exposes vcs object types', () => {
    expect(VCS_OBJECT_TYPES).toContain('pull_request');
    expect(VCS_OBJECT_TYPES).toContain('repository');
  });
});

describe('connector catalog', () => {
  it('has one card per kind with capabilities', () => {
    expect(CONNECTOR_CATALOG.map((meta) => meta.kind)).toEqual([...INTEGRATION_KINDS]);
    const expectedIcon = {
      im_feishu: 'message',
      im_slack: 'chat',
      vcs_github: 'git-merge',
      vcs_gitlab: 'git-merge',
      webhook_outbound: 'upload',
    } as const;
    for (const meta of CONNECTOR_CATALOG) {
      expect(Object.keys(ICON_PATHS)).toContain(meta.icon);
      expect(meta.icon).toBe(expectedIcon[meta.kind]);
      expect(meta.nameKey).toBe(`integrations.kind.${meta.kind}`);
      expect(meta.capabilityKeys.length).toBeGreaterThan(0);
    }
  });

  it('tags im connectors with approval cards and vcs connectors with linking', () => {
    const feishu = CONNECTOR_CATALOG.find((meta) => meta.kind === 'im_feishu');
    const github = CONNECTOR_CATALOG.find((meta) => meta.kind === 'vcs_github');
    expect(feishu?.capabilityKeys).toContain('integrations.capability.approval_card');
    expect(github?.capabilityKeys).toContain('integrations.capability.vcs_link');
  });
});
