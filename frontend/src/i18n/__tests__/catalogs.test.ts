/**
 * 消息目录完整性与质量测试 — i18n.md §2.4/§2.5/§3.4/§5.3。
 * - en 为权威源语言,zh-CN 必须键集完全一致(发版前 key 覆盖检查);
 * - error.<code> 覆盖全部 canonical 错误码(README §6.14);
 * - 全部消息为合法 ICU MessageFormat(可渲染,不依赖后端拼好的句子);
 * - 文案守卫:当外部环境变量 MESH_COPY_DENYLIST 提供词表时,校验文案不含其中词条(§5.3)。
 */
import { createIntl } from 'react-intl';
import { describe, expect, it } from 'vitest';
import { builtinCatalogs } from '../catalogLoader';

/**
 * 文案禁用词表(§5.3 / i18n.md §5.3):由外部环境变量 MESH_COPY_DENYLIST
 * (逗号分隔)注入,不随源码入库;未注入时词表为空,守卫用例退化为空校验。
 */
const COPY_DENYLIST_ENV = 'MESH_COPY_DENYLIST';

const COPY_DENYLIST: readonly string[] = (process.env[COPY_DENYLIST_ENV] ?? '')
  .split(',')
  .map((token) => token.trim().toLowerCase())
  .filter((token) => token.length > 0);

const REQUIRED_KEYS = [
  // common.*
  'common.save',
  'common.cancel',
  'common.retry',
  'common.close',
  'common.confirm',
  'common.loading',
  'common.search',
  'common.back',
  'common.unknownError',
  // state.*
  'state.loading',
  'state.emptyTitle',
  'state.emptyDescription',
  'state.errorTitle',
  'state.errorDescription',
  'state.permissionTitle',
  'state.permissionDescription',
  'state.permissionHint',
  'state.offline',
  'state.resyncing',
  'state.partialFailure',
  // error.*(README §6.14 canonical + 模块具名码;§3.4 前端按 code 取本地文案)
  'error.unauthorized',
  'error.forbidden',
  'error.not_found',
  'error.conflict',
  'error.gone',
  'error.locked',
  'error.payload_too_large',
  'error.unsupported_media_type',
  'error.rate_limited',
  'error.internal_error',
  'error.storage_error',
  'error.network',
  'error.unknown',
  'error.validation_error',
  'error.filter_too_complex',
  'error.query_cost_exceeded',
  'error.invalid_timezone',
  'error.unsupported_locale',
  'error.move_confirmation_required',
  'error.wip_limit_exceeded',
  'error.scan_pending',
  'error.circular_dependency',
  'error.label_name_taken',
  'error.field_key_taken',
  'error.invalid_field_config',
  'error.field_inactive',
  // nav.*
  'nav.home',
  'nav.inbox',
  'nav.projects',
  'nav.issues',
  'nav.board',
  'nav.members',
  'nav.chat',
  'nav.automation',
  'nav.settings',
  // theme.*
  'theme.label',
  'theme.light',
  'theme.dark',
  'theme.system',
  // settings.*
  'settings.title',
  'settings.appearance',
  'settings.language',
  'settings.languageFollowDefault',
  'settings.timezone',
  'settings.timezoneBrowser',
  'settings.personal',
  // login.*
  'login.title',
  'login.description',
  // status.*
  'status.connecting',
  'status.connected',
  'status.reconnecting',
  'status.offline',
  'status.resyncing',
  // shortcuts.*
  'shortcuts.helpTitle',
  'shortcuts.paletteTitle',
  'shortcuts.palettePlaceholder',
  'shortcuts.paletteEmpty',
  'shortcuts.groupGlobal',
  'shortcuts.groupBoard',
  'shortcuts.groupIssue',
  'shortcuts.groupChat',
  'shortcuts.actionPalette',
  'shortcuts.actionHelp',
  'shortcuts.actionNewIssue',
  'shortcuts.actionFocusSearch',
  'shortcuts.actionGoInbox',
  'shortcuts.actionGoBoard',
  'shortcuts.actionGoMembers',
  'shortcuts.actionGoAutomation',
  // a11y.*
  'a11y.themeToggle',
  'a11y.languageToggle',
  'a11y.openPalette',
  'a11y.openHelp',
  'a11y.connectionStatus',
  'a11y.closeDialog',
  'a11y.dismiss',
  'a11y.sidebar',
  'a11y.topbar',
  // notFound.*
  'notFound.title',
  'notFound.description',
  'notFound.backHome',
  // errorPage.*
  'errorPage.title',
  'errorPage.description',
  'errorPage.retry',
  // home.*(真实首页 / 工作区仪表盘,MES-107)
  'home.subtitle',
  'home.greeting',
  'home.workspacesTitle',
  'home.noWorkspacesTitle',
  'home.noWorkspacesDescription',
  'home.createWorkspace',
  'home.dashboardTitle',
  'home.quickCreateLabel',
  'home.feedEmptyTitle',
  'home.feedEmptyDescription',
  'home.loadMore',
] as const;

describe('消息目录完整性(§2.5:en 权威源,非 en locale 键覆盖检查)', () => {
  it('en 与 zh-CN 键集完全一致', () => {
    const enKeys = Object.keys(builtinCatalogs.en.messages).sort();
    const zhKeys = Object.keys(builtinCatalogs['zh-CN'].messages).sort();
    expect(zhKeys).toEqual(enKeys);
  });

  it('覆盖基线必需键清单(导航/状态/错误/设置/快捷键/无障碍/首页)', () => {
    for (const locale of ['en', 'zh-CN']) {
      const keys = new Set(Object.keys(builtinCatalogs[locale].messages));
      for (const key of REQUIRED_KEYS) {
        expect(keys.has(key), `${locale} 缺少键 ${key}`).toBe(true);
      }
    }
  });

  it('error.<code> 覆盖全部 canonical 错误码(README §6.14 + 模块具名码)', () => {
    const canonicalCodes = [
      'unauthorized',
      'forbidden',
      'not_found',
      'conflict',
      'gone',
      'locked',
      'payload_too_large',
      'unsupported_media_type',
      'rate_limited',
      'internal_error',
      'storage_error',
      'network',
      'unknown',
      'validation_error',
      'filter_too_complex',
      'query_cost_exceeded',
      'invalid_timezone',
      'unsupported_locale',
      'move_confirmation_required',
      'wip_limit_exceeded',
      'scan_pending',
      'circular_dependency',
      'label_name_taken',
      'field_key_taken',
      'invalid_field_config',
      'field_inactive',
    ];
    for (const locale of ['en', 'zh-CN']) {
      const messages = builtinCatalogs[locale].messages;
      for (const code of canonicalCodes) {
        const text = messages[`error.${code}`];
        expect(typeof text, `${locale} 缺少 error.${code}`).toBe('string');
        expect((text ?? '').length).toBeGreaterThan(0);
      }
    }
  });

  it('文案守卫:文案不含外部注入禁用词表词条(§5.3;词表经 MESH_COPY_DENYLIST 注入,为空时空校验)', () => {
    for (const locale of ['en', 'zh-CN']) {
      for (const [key, value] of Object.entries(builtinCatalogs[locale].messages)) {
        const lower = value.toLowerCase();
        for (const token of COPY_DENYLIST) {
          expect(lower, `${locale} ${key} 命中禁用词表`).not.toContain(token);
        }
      }
    }
  });
});

describe('ICU MessageFormat 可渲染性(§2.4)', () => {
  const dummyValues = {
    count: 3,
    minutes: 3,
    name: 'Mesh',
    date: new Date('2026-07-25T08:00:00Z'),
    n: 3,
    total: 10,
    // project §4 文案占位符(MES-30)
    done: 5,
    title: 'v1.0',
    // label-property §4 文案占位符(选项序号)
    index: 1,
    // squad §4 文案占位符(issue 头部单一责任主体:小队名 + 组长名)
    squad: '支付重构小队',
    leader: 'orchestrator',
    // workspace §4 文案占位符(MES-26)
    slug: 'acme',
    role: 'member',
    locale: 'zh-CN',
    cap: 100,
    value: 500,
    email: 'jane@corp.com',
    // agent §4.9 presence 容量三元组占位符(MES-60)
    running: 1,
    queued: 2,
    awaiting: 0,
    workspace: 'Acme',
    when: '2026-08-01 10:00',
    prefix: 'invtk_Ab3Xy9',
    supported: 'zh-CN, en',
    // issue §4 文案占位符(MES-31)
    succeeded: 9,
    failed: 1,
    field: 'priority',
    // kanban 视图 §4 文案占位符(MES-43)
    limit: 5,
    enforcement: 'warn',
    groupBy: 'status',
    // kanban 投影层 §4 文案占位符(MES-33):WIP 超限 toast 的列 key
    group: 'in_progress',
    // comment-inbox §4 文案占位符(触发预览的 agent 名单)
    names: 'code-reviewer, test-runner',
    // runtime §4 文案占位符:负载 / 心跳新鲜度 / 日志偏移 / 终态原因
    load: 2,
    max: 4,
    offset: 1049012,
    status: 'completed',
    reason: 'nonzero_exit',
    branch: 'agent/8f3a1d2c/a1',
    // import-export §4 文案占位符(MES-64):映射列名 / 可导入与跳过行数
    column: 'State',
    importable: 98,
    skipped: 2,
    // onboarding §4 文案占位符(MES-69):进度条已完成数 / 百分比
    completed: 2,
    // theme.md §4.1 主题占位标注(跟随工作区默认/跟随系统的当前解析值)
    theme: 'dark',
    percent: 40,
    // analytics §4 文案占位符(MES-71):净流量 / 日历时区回显
    net: 2,
    tz: 'UTC',
  };

  it('全部键在各自 locale 下均可成功渲染(语法合法、无占位符解析错误)', () => {
    for (const locale of ['en', 'zh-CN']) {
      const messages = builtinCatalogs[locale].messages;
      const intl = createIntl({ locale, messages });
      for (const key of Object.keys(messages)) {
        const text = intl.formatMessage({ id: key }, dummyValues);
        expect(text.length, `${locale} ${key} 渲染为空`).toBeGreaterThan(0);
        expect(text, `${locale} ${key} 残留未解析占位符`).not.toContain('{');
      }
    }
  });

  it('复数分支按 CLDR 类别选择:en 区分 one/other,zh-CN 仅 other(内联 ICU 样例)', () => {
    const en = createIntl({
      locale: 'en',
      messages: {
        commentCount: '{count, plural, =0 {No comments} one {# comment} other {# comments}}',
        duration: 'Took {minutes, plural, one {# minute} other {# minutes}}',
      },
    });
    expect(en.formatMessage({ id: 'commentCount' }, { count: 0 })).toBe('No comments');
    expect(en.formatMessage({ id: 'commentCount' }, { count: 1 })).toBe('1 comment');
    expect(en.formatMessage({ id: 'commentCount' }, { count: 5 })).toBe('5 comments');
    expect(en.formatMessage({ id: 'duration' }, { minutes: 1 })).toBe('Took 1 minute');
    expect(en.formatMessage({ id: 'duration' }, { minutes: 42 })).toBe('Took 42 minutes');

    const zh = createIntl({
      locale: 'zh-CN',
      messages: {
        commentCount: '{count, plural, =0 {暂无评论} other {# 条评论}}',
        duration: '耗时 {minutes, plural, other {# 分钟}}',
      },
    });
    expect(zh.formatMessage({ id: 'commentCount' }, { count: 0 })).toBe('暂无评论');
    expect(zh.formatMessage({ id: 'commentCount' }, { count: 5 })).toBe('5 条评论');
    expect(zh.formatMessage({ id: 'duration' }, { minutes: 42 })).toBe('耗时 42 分钟');
  });

  it('date/number 占位:结构化参数渲染,不依赖后端拼接(内联 ICU 样例)', () => {
    const en = createIntl({
      locale: 'en-US',
      messages: { position: 'Item {n, number} of {total, number}' },
    });
    expect(en.formatMessage({ id: 'position' }, { n: 3, total: 10 })).toBe('Item 3 of 10');
    const zh = createIntl({
      locale: 'zh-CN',
      messages: {
        position: '第 {n, number} 项，共 {total, number} 项',
        joined: '{name} 于 {date, date, medium} 加入',
      },
    });
    expect(zh.formatMessage({ id: 'position' }, { n: 3, total: 10 })).toBe(
      '第 3 项，共 10 项',
    );
    const joined = zh.formatMessage(
      { id: 'joined' },
      { name: 'Mesh', date: new Date('2026-07-25T08:00:00Z') },
    );
    expect(joined.startsWith('Mesh 于 ')).toBe(true);
    expect(joined.endsWith(' 加入')).toBe(true);
  });

  it('en 与 zh-CN 的 ICU 结构占位符一致(同名参数;覆盖全部含参消息)', () => {
    // 仅匹配后随 `,` 或 `}` 的参数名,避开 `{No comments}` 等分支内字面量
    const placeholder = (text: string): string[] =>
      [...text.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)(?=[,}])/g)].map((m) => m[1]).sort();
    const enMessages = builtinCatalogs.en.messages;
    const zhMessages = builtinCatalogs['zh-CN'].messages;
    const parameterized = Object.keys(enMessages).filter((key) =>
      placeholder(enMessages[key]).length > 0,
    );
    expect(parameterized.length).toBeGreaterThan(0);
    for (const key of parameterized) {
      expect(placeholder(zhMessages[key]), `${key} 占位符不一致`).toEqual(
        placeholder(enMessages[key]),
      );
    }
  });
});
