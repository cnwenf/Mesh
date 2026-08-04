import { describe, expect, it } from 'vitest';
import type { Membership } from '../../members/types';
import {
  projectRoute,
  projectSettingsRoute,
  projectsRoute,
  resolveProjectWorkspace,
} from '../routing';

const MEMBERSHIPS: readonly Membership[] = [
  {
    workspace_id: 'ws-a',
    workspace_name: 'Alpha',
    workspace_slug: 'alpha',
    role: 'owner',
    status: 'active',
    joined_at: null,
  },
  {
    workspace_id: 'ws-b',
    workspace_name: 'Beta',
    workspace_slug: 'beta',
    role: 'member',
    status: 'active',
    joined_at: null,
  },
];

describe('project workspace routing', () => {
  it('规范深链按 route slug 精确选择 membership,不回退到首个工作区', () => {
    expect(resolveProjectWorkspace(MEMBERSHIPS, 'beta')?.workspace_id).toBe('ws-b');
    expect(resolveProjectWorkspace(MEMBERSHIPS, 'missing')).toBeNull();
  });

  it('旧扁平入口无 route slug 时保留首个 membership 兼容', () => {
    expect(resolveProjectWorkspace(MEMBERSHIPS, undefined)?.workspace_id).toBe('ws-a');
    expect(resolveProjectWorkspace([], undefined)).toBeNull();
  });

  it('统一构造 workspace-scoped 项目列表、详情与设置路径', () => {
    expect(projectsRoute('beta')).toBe('/w/beta/projects');
    expect(projectRoute('beta', 'prj-1')).toBe('/w/beta/projects/prj-1');
    expect(projectSettingsRoute('beta', 'prj-1')).toBe('/w/beta/projects/prj-1/settings');
  });

  it('逐段编码 workspace slug 与 project id,避免保留字符改变路由结构', () => {
    expect(projectsRoute('blue team/ops')).toBe('/w/blue%20team%2Fops/projects');
    expect(projectRoute('blue team/ops', 'prj/1?#')).toBe(
      '/w/blue%20team%2Fops/projects/prj%2F1%3F%23',
    );
    expect(projectSettingsRoute('blue team/ops', 'prj/1?#')).toBe(
      '/w/blue%20team%2Fops/projects/prj%2F1%3F%23/settings',
    );
  });
});
