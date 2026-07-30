/**
 * Stylelint 自定义规则 `mesh/zindex-token-only`(design-quality §11.2 门禁)。
 *
 * z-index 一律经层级令牌 var(--z-base|sticky|dropdown|overlay|toast) 表达,
 * 杜绝业务 CSS 各造近似层叠值导致浮层互相穿透。
 *
 * 放行:
 * - var(--z-*) 取值(含 calc(var(--z-*) ± 整数) 的组合运算);
 * - CSS 关键字 auto / inherit / initial / unset / revert;
 * - 局部层叠小整数 -1 / 0 / 1(同一层叠上下文内的兄弟排序,如粘住首列、
 *   伪元素衬底——不参与全局层级竞争)。
 * 其余整数(10/40/50/1000…)一律 error。
 */
import stylelint from 'stylelint';

const {
  createPlugin,
  utils: { report, ruleMessages, validateOptions },
} = stylelint;

export const ruleName = 'mesh/zindex-token-only';

export const messages = ruleMessages(ruleName, {
  rejected: (value) =>
    `${ruleName}: z-index: ${value} 须经层级令牌表达(var(--z-base|sticky|dropdown|overlay|toast))。` +
    '局部兄弟排序仅允许 -1/0/1;全局浮层层级一律走令牌,禁任意整数。',
});

export const meta = {
  url: 'https://github.com/cnwenf/Mesh/blob/main/docs/specs/features/design-quality.md',
};

const CSS_KEYWORDS = new Set(['auto', 'inherit', 'initial', 'unset', 'revert']);
/** 局部层叠(同一层叠上下文内兄弟排序):不参与全局层级竞争的小整数 */
const LOCAL_STACKING = new Set(['-1', '0', '1']);
/** 令牌取值:var(--z-*) 或以其为基础的 calc 组合 */
const TOKEN_VALUE = /^var\(\s*--z-[\w-]+\s*\)$/;
const TOKEN_CALC = /^calc\(\s*var\(\s*--z-[\w-]+\s*\)(?:\s*[+-]\s*\d+)?\s*\)$/;

function isAllowedValue(rawValue) {
  const value = rawValue.trim().toLowerCase();
  if (CSS_KEYWORDS.has(value)) return true;
  if (LOCAL_STACKING.has(value)) return true;
  return TOKEN_VALUE.test(value) || TOKEN_CALC.test(value);
}

function rule(primary) {
  return (root, result) => {
    if (!validateOptions(result, ruleName, { actual: primary, possible: [true] })) return;
    root.walkDecls(/^z-index$/i, (decl) => {
      if (isAllowedValue(decl.value)) return;
      report({
        message: messages.rejected(decl.value),
        node: decl,
        result,
        ruleName,
      });
    });
  };
}

rule.ruleName = ruleName;
rule.messages = messages;
rule.meta = meta;

export default createPlugin(ruleName, rule);
