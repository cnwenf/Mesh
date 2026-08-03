/**
 * competitor-parity-checklist §3 的固定 28×4 存证合同。
 *
 * 路径在此显式登记；生成器与门禁都不根据文件名推断页面语义。
 */

export const CHECKLIST_VARIANTS = Object.freeze({
  desktop_light: Object.freeze({
    mode: 'desktop',
    theme: 'light',
    viewport: { width: 1440, height: 900 },
  }),
  desktop_dark: Object.freeze({
    mode: 'desktop',
    theme: 'dark',
    viewport: { width: 1440, height: 900 },
  }),
  mobile_light: Object.freeze({
    mode: 'mobile',
    theme: 'light',
    viewport: { width: 390, height: 844 },
  }),
  mobile_dark: Object.freeze({
    mode: 'mobile',
    theme: 'dark',
    viewport: { width: 390, height: 844 },
  }),
});

const b1 = (desktopLight, desktopDark, mobileLight, mobileDark) => ({
  desktop_light: `e2e/evidence/mes111-b1/${desktopLight}`,
  desktop_dark: `e2e/evidence/mes111-b1/${desktopDark}`,
  mobile_light: `e2e/evidence/mes111-b1/${mobileLight}`,
  mobile_dark: `e2e/evidence/mes111-b1/${mobileDark}`,
});

const b3 = (suffix) => ({
  desktop_light: `e2e/evidence/mes111-b3/desktop-light-${suffix}.png`,
  desktop_dark: `e2e/evidence/mes111-b3/desktop-dark-${suffix}.png`,
  mobile_light: `e2e/evidence/mes111-b3/phone-light-${suffix}.png`,
  mobile_dark: `e2e/evidence/mes111-b3/phone-dark-${suffix}.png`,
});

const b4 = (key) => ({
  desktop_light: `e2e/evidence/mes111-b4/desktop-${key}-light.png`,
  desktop_dark: `e2e/evidence/mes111-b4/desktop-${key}-dark.png`,
  mobile_light: `e2e/evidence/mes111-b4/mobile-${key}-light.png`,
  mobile_dark: `e2e/evidence/mes111-b4/mobile-${key}-dark.png`,
});

const b5 = (key) => ({
  desktop_light: `e2e/evidence/mes111-b5/desktop-${key}-light.png`,
  desktop_dark: `e2e/evidence/mes111-b5/desktop-${key}-dark.png`,
  mobile_light: `e2e/evidence/mes111-b5/mobile-${key}-light.png`,
  mobile_dark: `e2e/evidence/mes111-b5/mobile-${key}-dark.png`,
});

const supplement = (row, key) => {
  const number = String(row).padStart(2, '0');
  return {
    desktop_light: `e2e/evidence/mes128-checklist/desktop-${number}-${key}-light.png`,
    desktop_dark: `e2e/evidence/mes128-checklist/desktop-${number}-${key}-dark.png`,
    mobile_light: `e2e/evidence/mes128-checklist/mobile-${number}-${key}-light.png`,
    mobile_dark: `e2e/evidence/mes128-checklist/mobile-${number}-${key}-dark.png`,
  };
};

const MOCK_B1 = Object.freeze({
  backend_kind: 'mock_contract',
  database_provenance: false,
  generator: 'e2e/mes111-b1-evidence.spec.ts',
  source_readme: 'e2e/evidence/mes111-b1/README.md',
});

const REAL_B3 = Object.freeze({
  backend_kind: 'real_stack',
  database_provenance: false,
  generator: 'e2e/real-mes111-b3-evidence.spec.ts',
  source_readme: 'e2e/evidence/mes111-b3/README.md',
});

const REAL_B4 = Object.freeze({
  backend_kind: 'real_stack',
  database_provenance: false,
  generator: 'e2e/real-mes111-b4.spec.ts',
  source_readme: 'e2e/evidence/mes111-b4/README.md',
});

const MOCK_B5 = Object.freeze({
  backend_kind: 'mock_contract',
  database_provenance: false,
  generator: 'e2e/a11y/mes128-axe.spec.ts',
  source_readme: 'e2e/evidence/mes111-b5/README.md',
});

const MOCK_SUPPLEMENT = Object.freeze({
  backend_kind: 'mock_contract',
  database_provenance: false,
  generator: 'e2e/visual/checklist-evidence.spec.ts',
  source_readme: 'e2e/visual/checklist-evidence.spec.ts',
});

function row(id, key, label, route, shown_scope, ready_assertions, provenance, evidence) {
  return Object.freeze({
    id,
    key,
    label,
    route,
    shown_scope: Object.freeze(shown_scope),
    ready_assertions: Object.freeze(ready_assertions),
    provenance,
    evidence: Object.freeze(evidence),
  });
}

export const CHECKLIST_ROWS = Object.freeze([
  row(
    1,
    'auth',
    '登录/注册/忘记',
    '/login',
    ['登录 PublicFlowShell 、账号字段、主提交动作与恢复入口'],
    ['login-account-submit visible'],
    MOCK_B1,
    b1(
      'desktop-login-light.png',
      'desktop-login-dark.png',
      'phone-login-light.png',
      'phone-login-dark.png',
    ),
  ),
  row(
    2,
    'device',
    'OAuth/设备码/邀请',
    '/device?user_code=MESH-2026',
    ['设备码授权：手工码、客户端、scope、工作区与安全提示'],
    ['#device-code visible', 'Mesh CLI confirmation visible'],
    MOCK_SUPPLEMENT,
    supplement(2, 'device'),
  ),
  row(
    3,
    'home',
    '首页',
    '/',
    ['工作台的我的工作、快速创建、等待确认、AI 运行与最近项目'],
    ['home-dashboard visible'],
    MOCK_B1,
    b1(
      'desktop-home-light.png',
      'desktop-home-dark.png',
      'phone-home-light.png',
      'phone-home-dark.png',
    ),
  ),
  row(
    4,
    'app-shell',
    'AppShell',
    '/',
    ['桌面工作区切换层或手机更多导航抽屉，含顶栏/导航/主内容关系'],
    ['home-dashboard visible', 'workspace switcher or mobile navigation dialog visible'],
    MOCK_SUPPLEMENT,
    supplement(4, 'app-shell'),
  ),
  row(
    5,
    'command-palette',
    '命令面板/快捷键帮助',
    '/',
    ['命令面板搜索结果与键盘导航界面'],
    ['command palette dialog visible', 'search results visible'],
    REAL_B4,
    b4('palette'),
  ),
  row(
    6,
    'inbox',
    '收件箱/铃铛/通知偏好',
    '/inbox',
    ['收件箱分组列表、选中通知预览与优先级/来源'],
    ['inbox preview visible'],
    REAL_B3,
    b3('05-inbox-preview'),
  ),
  row(
    7,
    'projects',
    '项目列表/详情 tabs',
    '/projects',
    ['项目列表卡片、负责人、健康度、进度与状态'],
    ['project-card-project-1 visible'],
    MOCK_SUPPLEMENT,
    supplement(7, 'projects'),
  ),
  row(
    8,
    'cycles',
    '周期',
    '/cycles',
    ['周期列表、日期范围、状态与自动顺延信息'],
    ['cycle-row-cycle-1 visible'],
    MOCK_SUPPLEMENT,
    supplement(8, 'cycles'),
  ),
  row(
    9,
    'issues',
    'issue 列表',
    '/issues',
    ['issue 表格、过滤/排序控件与列表字段'],
    ['issue-row-MESH-1 visible'],
    MOCK_B5,
    b5('issues'),
  ),
  row(
    10,
    'issue-detail',
    'issue 详情',
    '/issues/issue-1',
    ['issue 标题、属性、评论面板与活动区'],
    ['issue-detail visible', 'comments-panel visible'],
    MOCK_B5,
    b5('issue-detail'),
  ),
  row(
    11,
    'board',
    '看板',
    '/board',
    ['宽屏分栏或手机紧凑看板及可见 issue 卡片'],
    ['board-card-issue-1 visible'],
    MOCK_B5,
    b5('board'),
  ),
  row(
    12,
    'members',
    '成员',
    '/members',
    ['人类与 agent 同一名册、角色、状态、头像与 AI 标识'],
    ['member roster visible'],
    REAL_B3,
    b3('01-members-roster'),
  ),
  row(
    13,
    'agents',
    'agent 详情/向导',
    '/agents/agent-1',
    ['agent 详情头部、运行态、容量与能力信息'],
    ['agent detail visible'],
    REAL_B3,
    b3('03-agent-detail'),
  ),
  row(
    14,
    'skills',
    '技能',
    '/skills',
    ['技能卡片、信任/来源、版本、能力与标签'],
    ['skill-card-skill-1 visible'],
    MOCK_SUPPLEMENT,
    supplement(14, 'skills'),
  ),
  row(
    15,
    'chat',
    '聊天',
    '/chat',
    ['会话列表、消息气泡、agent 流式回复与输入区'],
    ['chat streamed response visible'],
    REAL_B3,
    b3('10-chat-streamed'),
  ),
  row(
    16,
    'squads',
    '小队',
    '/squads',
    ['小队卡片、领导者、成员墙、类型、状态与活跃任务'],
    ['squad-card-squad-1 visible'],
    MOCK_SUPPLEMENT,
    supplement(16, 'squads'),
  ),
  row(
    17,
    'runtimes',
    'runtime',
    '/runtimes',
    ['runtime 状态、类型、负载、心跳与操作列'],
    ['runtime-row-runtime-1 visible'],
    MOCK_SUPPLEMENT,
    supplement(17, 'runtimes'),
  ),
  row(
    18,
    'execution',
    'execution',
    '/executions/exec-1',
    ['执行详情、状态、进度、日志与时间信息'],
    ['execution-detail-page visible', 'execution-panel-logs visible'],
    MOCK_B5,
    b5('execution'),
  ),
  row(
    19,
    'autopilots',
    'autopilot',
    '/autopilots',
    ['autopilot 列表、状态、触发类型与最近运行'],
    ['autopilot-row-autopilot-1 visible'],
    MOCK_B5,
    b5('autopilots'),
  ),
  row(
    20,
    'integrations',
    'integrations/webhooks',
    '/integrations',
    ['集成目录、provider、连接状态与操作'],
    ['integration-row-integration-1 visible'],
    MOCK_B5,
    b5('integrations'),
  ),
  row(
    21,
    'insights',
    'insights',
    '/insights',
    ['洞察指标、时间范围、趋势图与成员维度'],
    ['insights data view visible'],
    REAL_B4,
    b4('insights-data'),
  ),
  row(
    22,
    'data-management',
    'import/export',
    '/w/acme/settings/data',
    ['数据作业列表、导入/导出入口、行计数与作业状态'],
    ['job-row-job-1 visible', 'open-import-wizard visible'],
    MOCK_SUPPLEMENT,
    supplement(22, 'data-management'),
  ),
  row(
    23,
    'workspace-settings',
    'workspace settings',
    '/w/acme/settings/general',
    ['工作区设置导航与基本信息表单'],
    ['workspace settings general visible'],
    REAL_B4,
    b4('ws-settings'),
  ),
  row(
    24,
    'personal-settings',
    'personal settings',
    '/settings/appearance',
    ['个人外观、语言与时区偏好'],
    ['theme-select visible'],
    REAL_B4,
    b4('settings'),
  ),
  row(
    25,
    'approvals',
    'approvals',
    '/approvals',
    ['待确认列表页、筛选与空/内容状态'],
    ['approvals page visible'],
    REAL_B4,
    b4('approvals'),
  ),
  row(
    26,
    'onboarding',
    'onboarding',
    '/',
    ['五步上手清单、进度、完成来源与首个待办 CTA'],
    ['onboarding-card visible', 'create_first_issue step visible'],
    MOCK_SUPPLEMENT,
    supplement(26, 'onboarding'),
  ),
  row(
    27,
    'attachment-lightbox',
    'attachment lightbox',
    '/issues/issue-1',
    ['图片附件灯箱、原图、缩放、旋转、定位与下载控件'],
    ['attachment-thumb-img-1 visible', 'lightbox-image and controls visible'],
    MOCK_SUPPLEMENT,
    supplement(27, 'attachment-lightbox'),
  ),
  row(
    28,
    'not-found',
    '404/error/permission/offline',
    '/checklist-route-not-found',
    ['404 未找到页与返回首页恢复动作'],
    ['notfound-home visible'],
    MOCK_SUPPLEMENT,
    supplement(28, 'not-found'),
  ),
]);

export const EXPECTED_CHECKLIST_CELLS =
  CHECKLIST_ROWS.length * Object.keys(CHECKLIST_VARIANTS).length;
