/**
 * stylelint 规则 `mesh/zindex-token-only` 单测(design-quality §11.2)。
 */
import stylelint from 'stylelint';
import { describe, expect, it } from 'vitest';
import plugin, { messages, ruleName } from '../zindex-token-only.mjs';

async function lintRule(code) {
  const result = await stylelint.lint({
    code,
    config: {
      plugins: [plugin],
      rules: { [ruleName]: true },
    },
  });
  return result.results[0].warnings.filter((warning) => warning.rule === ruleName);
}

describe('mesh/zindex-token-only', () => {
  describe('放行', () => {
    it('层级令牌 var(--z-*) 全档放行', async () => {
      for (const token of ['--z-base', '--z-sticky', '--z-dropdown', '--z-overlay', '--z-toast']) {
        expect(await lintRule(`a { z-index: var(${token}); }`), token).toHaveLength(0);
      }
    });

    it('calc(var(--z-*) ± 整数) 组合放行', async () => {
      expect(await lintRule('a { z-index: calc(var(--z-toast) + 1); }')).toHaveLength(0);
      expect(await lintRule('a { z-index: calc(var(--z-overlay) - 1); }')).toHaveLength(0);
    });

    it('CSS 关键字放行', async () => {
      for (const keyword of ['auto', 'inherit', 'initial', 'unset', 'revert']) {
        expect(await lintRule(`a { z-index: ${keyword}; }`), keyword).toHaveLength(0);
      }
    });

    it('局部层叠小整数 -1/0/1 放行(同一层叠上下文兄弟排序)', async () => {
      for (const value of ['-1', '0', '1']) {
        expect(await lintRule(`a { z-index: ${value}; }`), value).toHaveLength(0);
      }
    });
  });

  describe('命中:任意整数层级', () => {
    it('散落整数(10/40/50/1000)命中并指明令牌路径', async () => {
      for (const value of ['10', '40', '50', '60', '1000']) {
        const warnings = await lintRule(`a { z-index: ${value}; }`);
        expect(warnings, value).toHaveLength(1);
        expect(warnings[0].text).toContain(value);
        expect(warnings[0].text).toContain('--z-');
      }
    });

    it('局部语义外的 2 命中(兄弟排序只许 -1/0/1)', async () => {
      expect(await lintRule('a { z-index: 2; }')).toHaveLength(1);
    });

    it('非 --z- 前缀的 var() 命中(不得拿别的令牌冒充层级)', async () => {
      expect(await lintRule('a { z-index: var(--space-4); }')).toHaveLength(1);
    });

    it('消息工厂可直接调用', () => {
      expect(messages.rejected('99')).toContain('99');
    });
  });
});
