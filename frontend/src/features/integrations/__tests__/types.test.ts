/**
 * types.ts 运行时常量测试(连接器目录 / kind 枚举 / VCS 对象类型)。
 */
import { describe, expect, it } from 'vitest';
import { CONNECTOR_CATALOG, INTEGRATION_KINDS, VCS_OBJECT_TYPES } from '../types';

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

  it('exposes vcs object types', () => {
    expect(VCS_OBJECT_TYPES).toContain('pull_request');
    expect(VCS_OBJECT_TYPES).toContain('repository');
  });
});

describe('connector catalog', () => {
  it('has one card per kind with capabilities', () => {
    expect(CONNECTOR_CATALOG.map((meta) => meta.kind)).toEqual([...INTEGRATION_KINDS]);
    for (const meta of CONNECTOR_CATALOG) {
      expect(meta.icon.length).toBeGreaterThan(0);
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
