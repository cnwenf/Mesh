/**
 * `/w/{slug}/issues/{issueId}` 规范化解析(search-command-palette.md §3.4 「解析后」)。
 *
 * 旧扁平书签 `/issues/{id}` 经 FlatRouteMigration 映射至 `/w/{slug}/issues/{id}`
 * 后落本路由。`{id}` 可能是:
 * - **identifier 形态**(`KEY-N`,如 `WEB-124`,大小写不敏感)→ 非 issue 主键 UUID,
 *   直连 IssueDetailPage 会以 id 查无此 issue 而落错误态;故 replace 至规范
 *   `by-identifier/{KEY-N}`(大写归一)经既有解析渲染;
 * - **UUID 形态**(issue 主键)→ 应用现行用法,直接渲染 IssueDetailPage(其内按 id
 *   拉取),保持既有深链行为不变(与 by-identifier 解析互成回路即环,故 UUID 不再
 *   反向跳 by-identifier)。
 *
 * identifier 正则要求「单连字符 + 末段纯数字」,UUID 含多连字符且末段为十六进制,
 * 不会被误判为 identifier。
 */
import { Navigate, useParams } from 'react-router';
import { IssueDetailPage } from './IssueDetailPage';
import { workspaceIssueByIdentifierPath } from './issueRoutes';

/** issue identifier 形态:`KEY-N`(§3.4;大小写不敏感,归一大写) */
const IDENTIFIER_PATTERN = /^[A-Z0-9]+-\d+$/i;

export function IssueByIdRedirect(): React.JSX.Element {
  const { workspaceSlug, issueId } = useParams<{ workspaceSlug: string; issueId: string }>();
  if (issueId !== undefined && IDENTIFIER_PATTERN.test(issueId)) {
    return <Navigate to={workspaceIssueByIdentifierPath(workspaceSlug, issueId)} replace />;
  }
  return <IssueDetailPage />;
}
