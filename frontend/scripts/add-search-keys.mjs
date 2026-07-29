// One-shot key seeding for the search/deep-link/shortcut increment (MES-79).
// Merge-if-missing keeps existing translations untouched; version hash is
// recomputed with the same djb2 algorithm as catalogLoader.ts.
import { readFileSync, writeFileSync } from 'node:fs';

const KEYS = {
  'search.group.issue': ['Issues', '工作项'],
  'search.group.member': ['Members', '成员'],
  'search.group.agent': ['Agents', '智能体'],
  'search.group.project': ['Projects', '项目'],
  'search.group.view': ['Views', '视图'],
  'search.group.chat_session': ['Chats', '会话'],
  'search.group.recent': ['Recent', '最近访问'],
  'search.group.favorite': ['Favorites', '收藏'],
  'search.group.command': ['Commands', '命令'],
  'search.subtitle.issue': [
    '{identifier} · {project} · {status}',
    '{identifier} · {project} · {status}',
  ],
  'search.subtitle.member': ['Member · {role}', '成员 · {role}'],
  'search.subtitle.agent': ['Agent · {role}', '智能体 · {role}'],
  'search.subtitle.project': ['{key} · {visibility}', '{key} · {visibility}'],
  'search.subtitle.view': ['{scope}', '{scope}'],
  'search.subtitle.chat': ['{agent}', '{agent}'],
  'search.noResults': ['No results for “{q}”', '未找到与 “{q}” 匹配的结果'],
  'search.noResultsHint': [
    'Check the spelling or try fewer keywords.',
    '检查拼写或减少关键词。',
  ],
  'search.createIssue': ['Create issue “{q}”', '新建 issue “{q}”'],
  'search.error': ['Search failed', '搜索失败'],
  'search.retry': ['Retry', '重试'],
  'search.offline': [
    'Network offline — showing local commands',
    '网络已断开,显示本地命令',
  ],
  'search.loading': ['Searching…', '搜索中…'],
  'search.openNewTab': ['Open in new tab', '在新标签页打开'],
  'search.resultCount': ['{count} results', '{count} 条结果'],
  'search.capacity': [
    'Running {running} · Queued {queued} · Awaiting approval {awaiting}',
    '运行中 {running} · 排队 {queued} · 需审批 {awaiting}',
  ],
  'search.recentsCleared': ['Removed outdated entries', '已清理失效的最近访问'],
  'issue.status.name': ['{name}', '{name}'],
  'member.type.human': ['Human', '人类'],
  'member.type.agent': ['Agent', '智能体'],
  'project.visibility.public': ['Public', '公开'],
  'project.visibility.private': ['Private', '私有'],
  'view.scope.project': ['Project view', '项目视图'],
  'view.scope.workspace': ['Workspace view', '工作区视图'],
  'workspacePicker.title': ['Choose a workspace', '选择工作区'],
  'workspacePicker.hint': ['Pick the workspace to open this page in.', '选择要进入的工作区。'],
  'workspacePicker.open': ['Open', '进入'],
  'nav.approvals': ['Approvals', '审批'],
  'shortcuts.actionCopyDeepLink': ['Copy link to current page', '复制当前页面深链'],
  'shortcuts.actionToggleFavorite': [
    'Favorite / unfavorite current resource',
    '收藏 / 取消收藏当前资源',
  ],
  'shortcuts.actionMarkAllRead': ['Mark all as read', '标记全部已读'],
  'shortcuts.actionOpenApprovals': ['Pending approvals', '待我审批'],
  'shortcuts.actionOpenSettings': ['Workspace settings', '工作区设置'],
  'shortcuts.actionOpenSettingsMembers': ['Settings · Members & roles', '设置 · 成员与角色'],
  'shortcuts.actionOpenSettingsApprovals': [
    'Settings · Approval policies',
    '设置 · 审批策略',
  ],
  'shortcuts.actionOpenSettingsFields': ['Settings · Statuses & fields', '设置 · 状态与字段'],
  'shortcuts.actionOpenSettingsDanger': ['Settings · Danger zone', '设置 · 危险操作'],
  'shortcuts.boardMove': ['Move selected card', '移动选中卡片'],
  'shortcuts.boardNewCard': ['New card in selected column', '当前列新建卡片'],
  'shortcuts.boardChangeStatus': ['Change card status', '改选中卡片状态'],
  'shortcuts.boardChangeAssignee': ['Change card assignee', '改选中卡片负责人'],
  'shortcuts.boardOpenCard': ['Open selected card', '打开选中卡片'],
  'shortcuts.boardFilter': ['Open filters', '打开筛选'],
  'shortcuts.issueEdit': ['Edit issue', '编辑 issue'],
  'shortcuts.issueStatus': ['Change status', '改状态'],
  'shortcuts.issueAssignee': ['Change assignee', '改负责人'],
  'shortcuts.issuePriority': ['Change priority', '改优先级'],
  'shortcuts.issueLabels': ['Open label picker', '打开标签选择器'],
  'shortcuts.issueMilestone': ['Set milestone', '设里程碑'],
  'shortcuts.issueSubmitComment': ['Submit comment', '提交评论'],
  'shortcuts.issueClose': ['Close issue detail', '关闭 issue 详情'],
  'shortcuts.chatSend': ['Send message', '发送消息'],
  'shortcuts.chatNewline': ['Insert new line', '换行'],
  'shortcuts.chatEditLast': ['Edit last message', '编辑上一条消息'],
  'shortcuts.chatBlur': ['Exit input focus', '退出输入焦点'],
  'shortcuts.sequencePending': ['G —', 'G —'],
};

function djb2(messages) {
  const separator = String.fromCharCode(0);
  let hash = 5381;
  for (const key of Object.keys(messages).sort()) {
    const entry = key + '=' + messages[key] + separator;
    for (let i = 0; i < entry.length; i += 1) {
      hash = (Math.imul(hash, 33) + entry.charCodeAt(i)) >>> 0;
    }
  }
  return hash.toString(16).padStart(8, '0');
}

for (const [locale, idx] of [
  ['en', 0],
  ['zh-CN', 1],
]) {
  const path = `src/i18n/catalogs/${locale}.json`;
  const catalog = JSON.parse(readFileSync(path, 'utf8'));
  let added = 0;
  for (const [key, pair] of Object.entries(KEYS)) {
    if (!(key in catalog.messages)) {
      catalog.messages[key] = pair[idx];
      added += 1;
    }
  }
  catalog.version = djb2(catalog.messages);
  writeFileSync(path, `${JSON.stringify(catalog, null, 2)}\n`);
  console.log(locale, 'added', added, 'version', catalog.version);
}
