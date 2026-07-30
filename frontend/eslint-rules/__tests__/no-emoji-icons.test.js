/**
 * ESLint 规则 `mesh/no-emoji-icons` 单测(design-quality §7.1)。
 * 经 RuleTester 直驱:命中/放行/例外三组语义逐分支覆盖。
 */
import { RuleTester } from 'eslint';
import { describe, it } from 'vitest';
import { noEmojiIcons } from '../no-emoji-icons.js';

const ruleTester = new RuleTester({
  languageOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    parserOptions: { ecmaFeatures: { jsx: true } },
  },
});

describe('mesh/no-emoji-icons', () => {
  it('命中/放行/例外矩阵', () => {
    ruleTester.run('no-emoji-icons', noEmojiIcons, {
      valid: [
        // 纯文本与常规符号放行
        { code: 'const label = <span>运行环境</span>;' },
        { code: 'const arrow = <span>{count > 0 ? "up" : "down"}</span>;' },
        // 统一图标系统放行
        { code: 'const icon = <Icon name="bell" />;' },
        // 例外注释放行(上一行 mesh-emoji-ok + 原因)
        {
          code: [
            'function devMarker(key) {',
            '  // mesh-emoji-ok: 开发期缺失文案诊断前缀,仅 dev 可见,非产品图标',
            '  return `⚠[${key}]`;',
            '}',
          ].join('\n'),
        },
        // 对象属性值:注释放属性键前(锚点上溯 Property)
        {
          code: [
            'const KEY_LABELS = {',
            '  // mesh-emoji-ok: 键帽符号为键盘排版记号,非 UI 图标',
            '  backspace: "⌫",',
            '};',
          ].join('\n'),
        },
        // 有插值的模板串不参与静态门禁
        { code: 'const mixed = `${prefix} 文本 ${suffix}`;' },
      ],
      invalid: [
        // JSX 文本 emoji
        {
          code: 'const bell = <span aria-hidden="true">🔔</span>;',
          errors: [{ messageId: 'emoji' }],
        },
        // 字符串字面量字符图标(✓/✕/★ 丁字符区)
        {
          code: 'const done = <b>{"✓ 已选用"}</b>;',
          errors: [{ messageId: 'emoji' }],
        },
        // 注册表式字符图标映射(自动值守触发器场景)
        {
          code: 'const TRIGGER_ICONS = { issue_created: "➕", comment_created: "💬" };',
          errors: [{ messageId: 'emoji' }, { messageId: 'emoji' }],
        },
        // 技术符号区(⏳ 执行占位)
        {
          code: 'const executing = <span>⏳ 正在执行</span>;',
          errors: [{ messageId: 'emoji' }],
        },
        // 无插值模板串
        {
          code: 'const pin = `★ 置顶`;',
          errors: [{ messageId: 'emoji' }],
        },
        // 例外注释隔了一行不生效
        {
          code: ['// mesh-emoji-ok: 原因', 'const blank = null;', 'const gear = <i>⚙</i>;'].join('\n'),
          errors: [{ messageId: 'emoji' }],
        },
      ],
    });
  });
});
