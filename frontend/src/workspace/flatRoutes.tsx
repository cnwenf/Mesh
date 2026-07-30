/**
 * 旧扁平路由 → 规范深链迁移(search-command-palette.md §3.4 旧→新映射表)。
 *
 * 执行层(评审 R2-M1 写死):扁平路由是 SPA 客户端路由,旧→新跳转由**前端
 * 路由器的 replace navigation** 执行——`navigate(target, {replace:true})`
 * 触发路由匹配与数据加载,不新增历史栈条目;不用裸 history.replaceState,
 * 不称 302 语义(真实 HTTP 301 仅在 nginx 入口对过期 slug 发生)。
 *
 * 一律保留原 query 与 hash(`/board?view=x#card-1` → `/w/{ws}/board?view=x#card-1`)。
 * active workspace 解析序见 lastWorkspace.ts;多工作区无上下文 → 选择页,
 * 经 `?next=` 保留原意图路径。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:映射表/匹配函数与迁移组件同文件 */
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { getApiClient } from '../api/instance';
import { fetchMe } from '../features/members/api';
import { NotFoundPage } from '../shell/pages/NotFoundPage';
import { recordLastWorkspace, resolveActiveWorkspaceSlug } from './lastWorkspace';

export interface FlatRouteRule {
  /** 精确匹配旧扁平路径(不含 query/hash) */
  readonly pattern: RegExp;
  /** 由匹配组与目标 slug 构造规范路径(不含 query/hash) */
  readonly build: (match: RegExpMatchArray, slug: string) => string;
}

/**
 * 旧→新逐条映射(§3.4 表 + 运营区/执行/agent 别名闭合)。
 * 注意:`/settings` 裸路径为**账号设置**(主题/语言/时区/安全),不迁移;
 * 仅工作区设置子路径(labels/data/custom-fields/members/approvals/fields/danger)
 * 映射至 /w/{ws}/settings/*。
 */
export const FLAT_ROUTE_RULES: readonly FlatRouteRule[] = [
  { pattern: /^\/inbox$/, build: (_m, slug) => `/w/${slug}/inbox` },
  { pattern: /^\/board$/, build: (_m, slug) => `/w/${slug}/board` },
  { pattern: /^\/views\/([^/]+)$/, build: (m, slug) => `/w/${slug}/views/${m[1]}` },
  { pattern: /^\/members$/, build: (_m, slug) => `/w/${slug}/members` },
  { pattern: /^\/members\/([^/]+)$/, build: (m, slug) => `/w/${slug}/members/${m[1]}` },
  { pattern: /^\/projects$/, build: (_m, slug) => `/w/${slug}/projects` },
  { pattern: /^\/projects\/([^/]+)\/settings$/, build: (m, slug) => `/w/${slug}/projects/${m[1]}/settings` },
  { pattern: /^\/projects\/([^/]+)$/, build: (m, slug) => `/w/${slug}/projects/${m[1]}` },
  { pattern: /^\/issues$/, build: (_m, slug) => `/w/${slug}/issues` },
  {
    pattern: /^\/issues\/by-identifier\/([^/]+)$/,
    build: (m, slug) => `/w/${slug}/issues/by-identifier/${m[1]}`,
  },
  { pattern: /^\/issues\/([^/]+)$/, build: (m, slug) => `/w/${slug}/issues/${m[1]}` },
  { pattern: /^\/chat$/, build: (_m, slug) => `/w/${slug}/chat` },
  { pattern: /^\/chat\/([^/]+)$/, build: (m, slug) => `/w/${slug}/chat/${m[1]}` },
  { pattern: /^\/squads$/, build: (_m, slug) => `/w/${slug}/squads` },
  { pattern: /^\/squads\/([^/]+)\/tasks\/([^/]+)$/, build: (m, slug) => `/w/${slug}/squads/${m[1]}/tasks/${m[2]}` },
  { pattern: /^\/squads\/([^/]+)$/, build: (m, slug) => `/w/${slug}/squads/${m[1]}` },
  { pattern: /^\/cycles$/, build: (_m, slug) => `/w/${slug}/cycles` },
  { pattern: /^\/executions\/([^/]+)$/, build: (m, slug) => `/w/${slug}/executions/${m[1]}` },
  // 统计报表(analytics.md):旧扁平 /insights 迁移至规范路由。
  { pattern: /^\/insights$/, build: (_m, slug) => `/w/${slug}/insights` },
  // agent 详情 = member_type='agent' 的成员详情别名(README §6.12),保留 agent_id 路由解析。
  { pattern: /^\/agents\/([^/]+)$/, build: (m, slug) => `/w/${slug}/agents/${m[1]}` },
  // 自动化运营区:旧 /autopilots、/runtimes、/webhooks、/skills 收敛至 automations/*。
  { pattern: /^\/automation$/, build: (_m, slug) => `/w/${slug}/automations/autopilots` },
  { pattern: /^\/autopilots$/, build: (_m, slug) => `/w/${slug}/automations/autopilots` },
  { pattern: /^\/autopilots\/(.+)$/, build: (m, slug) => `/w/${slug}/automations/autopilots/${m[1]}` },
  { pattern: /^\/runtimes$/, build: (_m, slug) => `/w/${slug}/automations/runtimes` },
  { pattern: /^\/runtimes\/([^/]+)$/, build: (m, slug) => `/w/${slug}/automations/runtimes/${m[1]}` },
  { pattern: /^\/webhooks$/, build: (_m, slug) => `/w/${slug}/automations/webhooks` },
  { pattern: /^\/skills$/, build: (_m, slug) => `/w/${slug}/automations/skills` },
  { pattern: /^\/skills\/(.+)$/, build: (m, slug) => `/w/${slug}/automations/skills/${m[1]}` },
  // 集成平台(MES-68):旧扁平 /integrations、/webhook-subscriptions 收敛至运营区规范深链。
  { pattern: /^\/integrations$/, build: (_m, slug) => `/w/${slug}/automations/integrations` },
  {
    pattern: /^\/integrations\/([^/]+)$/,
    build: (m, slug) => `/w/${slug}/automations/integrations/${m[1]}`,
  },
  {
    pattern: /^\/webhook-subscriptions$/,
    build: (_m, slug) => `/w/${slug}/automations/webhook-subscriptions`,
  },
  { pattern: /^\/automations\/(.+)$/, build: (m, slug) => `/w/${slug}/automations/${m[1]}` },
  // 工作区设置子路径(admin+);裸 /settings 为账号设置,不在此表。
  {
    pattern: /^\/settings\/(labels|data|custom-fields|members|approvals|fields|danger)$/,
    build: (m, slug) => `/w/${slug}/settings/${m[1]}`,
  },
];

/** 命中旧扁平路径 → 规范路径构造器;非旧路由 → null。 */
export function matchFlatRoute(pathname: string): ((slug: string) => string) | null {
  for (const rule of FLAT_ROUTE_RULES) {
    const match = pathname.match(rule.pattern);
    if (match !== null) {
      return (slug: string) => rule.build(match, slug);
    }
  }
  return null;
}

/**
 * 扁平路由迁移组件:挂载于 AppShell 内规范路由之后的 catch-all。当前路径
 * 命中旧扁平路由时,按 active workspace 解析序求目标工作区并 replace
 * navigation 至规范路由(query/hash 保留);多工作区无上下文 → 选择页
 * (?next= 保留意图);非旧路由路径 → not-found 呈现。
 */
export function FlatRouteMigration(): React.JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const [isNotFound, setIsNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setIsNotFound(false);
    const { pathname, search, hash } = location;
    const build = matchFlatRoute(pathname);
    if (build === null) {
      setIsNotFound(true);
      return;
    }
    void (async () => {
      try {
        const me = await fetchMe(getApiClient());
        if (cancelled) return;
        const slug = resolveActiveWorkspaceSlug({
          memberships: me.memberships,
          userId: me.user.id,
          lastActiveWorkspaceId: me.user.last_active_workspace_id ?? null,
        });
        if (slug === null) {
          // 解析序 ⑤:多工作区无上下文 → 选择页,选定后回跳意图路径。
          const next = encodeURIComponent(pathname + search + hash);
          navigate(`/workspace-picker?next=${next}`, { replace: true });
          return;
        }
        recordLastWorkspace(me.user.id, slug);
        navigate(build(slug) + search + hash, { replace: true });
      } catch {
        if (!cancelled) setIsNotFound(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [location, navigate]);

  if (isNotFound) {
    return <NotFoundPage />;
  }
  // 解析中:不渲染占位(replace navigation 即完成,避免闪烁)。
  return <></>;
}
