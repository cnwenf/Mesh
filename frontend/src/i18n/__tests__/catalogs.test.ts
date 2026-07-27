/**
 * 消息目录完整性与质量测试 — i18n.md §2.4/§2.5/§3.4/§5.3。
 * - en 为权威源语言,zh-CN 必须键集完全一致(发版前 key 覆盖检查);
 * - error.<code> 覆盖全部 canonical 错误码(README §6.14);
 * - 全部消息为合法 ICU MessageFormat(可渲染,不依赖后端拼好的句子);
 * - 匿名化:文案不含任何被禁字样(§5.3;禁用词经编码存放,见 FORBIDDEN_TOKENS)。
 */
import { createIntl } from 'react-intl';
import { describe, expect, it } from 'vitest';
import { builtinCatalogs } from '../catalogLoader';

/**
 * 禁用词集合(匿名化校验目标,§5.3 / i18n.md §5.3)。
 * 以 base64 编码存放,运行期解码——避免在已提交源码出现被禁字面量本身,
 * 否则源码读者会从守卫反推出匿名化目标,与本仓匿名化策略自相矛盾。
 */
const FORBIDDEN_TOKENS: readonly string[] = ['bXVsdGljYQ=='].map((encoded) =>
  atob(encoded).toLowerCase(),
);

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
  'state.retryHint',
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
  'login.tokenLabel',
  'login.tokenPlaceholder',
  'login.submit',
  'login.phaseNote',
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
  // home.*
  'home.title',
  'home.subtitle',
  'home.demoTheme',
  'home.demoLocale',
  'home.demoShortcuts',
  'home.demoStates',
  'home.demoRealtime',
  // demo.*(ICU plural/date/number 示例,§2.4)
  'demo.commentCount',
  'demo.duration',
  'demo.joined',
  'demo.position',
] as const;

describe('消息目录完整性(§2.5:en 权威源,非 en locale 键覆盖检查)', () => {
  it('en 与 zh-CN 键集完全一致', () => {
    const enKeys = Object.keys(builtinCatalogs.en.messages).sort();
    const zhKeys = Object.keys(builtinCatalogs['zh-CN'].messages).sort();
    expect(zhKeys).toEqual(enKeys);
  });

  it('覆盖基线必需键清单(导航/状态/错误/设置/快捷键/无障碍/占位页/演示)', () => {
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

  it('匿名化:文案不含被禁字样(§5.3)', () => {
    for (const locale of ['en', 'zh-CN']) {
      for (const [key, value] of Object.entries(builtinCatalogs[locale].messages)) {
        const lower = value.toLowerCase();
        for (const token of FORBIDDEN_TOKENS) {
          expect(lower, `${locale} ${key} 含禁用字样`).not.toContain(token);
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
    // workspace §4 文案占位符(MES-26)
    slug: 'acme',
    role: 'member',
    locale: 'zh-CN',
    cap: 100,
    value: 500,
    email: 'jane@corp.com',
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

  it('复数分支按 CLDR 类别选择:en 区分 one/other,zh-CN 仅 other', () => {
    const en = createIntl({ locale: 'en', messages: builtinCatalogs.en.messages });
    expect(en.formatMessage({ id: 'demo.commentCount' }, { count: 0 })).toBe('No comments');
    expect(en.formatMessage({ id: 'demo.commentCount' }, { count: 1 })).toBe('1 comment');
    expect(en.formatMessage({ id: 'demo.commentCount' }, { count: 5 })).toBe('5 comments');
    expect(en.formatMessage({ id: 'demo.duration' }, { minutes: 1 })).toBe('Took 1 minute');
    expect(en.formatMessage({ id: 'demo.duration' }, { minutes: 42 })).toBe('Took 42 minutes');

    const zh = createIntl({ locale: 'zh-CN', messages: builtinCatalogs['zh-CN'].messages });
    expect(zh.formatMessage({ id: 'demo.commentCount' }, { count: 0 })).toBe('暂无评论');
    expect(zh.formatMessage({ id: 'demo.commentCount' }, { count: 5 })).toBe('5 条评论');
    expect(zh.formatMessage({ id: 'demo.duration' }, { minutes: 42 })).toBe('耗时 42 分钟');
  });

  it('date/number 占位:结构化参数渲染,不依赖后端拼接', () => {
    const en = createIntl({ locale: 'en-US', messages: builtinCatalogs.en.messages });
    expect(en.formatMessage({ id: 'demo.position' }, { n: 3, total: 10 })).toBe('Item 3 of 10');
    const zh = createIntl({ locale: 'zh-CN', messages: builtinCatalogs['zh-CN'].messages });
    expect(zh.formatMessage({ id: 'demo.position' }, { n: 3, total: 10 })).toBe(
      '第 3 项，共 10 项',
    );
    const joined = zh.formatMessage(
      { id: 'demo.joined' },
      { name: 'Mesh', date: new Date('2026-07-25T08:00:00Z') },
    );
    expect(joined.startsWith('Mesh 于 ')).toBe(true);
    expect(joined.endsWith(' 加入')).toBe(true);
  });

  it('en 与 zh-CN 的 ICU 结构占位符一致(同名参数)', () => {
    // 仅匹配后随 `,` 或 `}` 的参数名,避开 `{No comments}` 等分支内字面量
    const placeholder = (text: string): string[] =>
      [...text.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)(?=[,}])/g)].map((m) => m[1]).sort();
    for (const key of ['demo.commentCount', 'demo.duration', 'demo.joined', 'demo.position']) {
      expect(placeholder(builtinCatalogs['zh-CN'].messages[key])).toEqual(
        placeholder(builtinCatalogs.en.messages[key]),
      );
    }
  });
});
