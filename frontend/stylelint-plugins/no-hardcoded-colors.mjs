/**
 * Stylelint 自定义规则 `mesh/no-hardcoded-colors`(theme.md §5.4 AST 级门禁)。
 *
 * 命中:颜色位属性(见 lint-shared/color-grammar.mjs 的 isColorProperty)值中的
 * #hex / rgb()/rgba()/hsl()/hsla()/oklch()/oklab() / CSS 命名色字面量。
 * 放行:var(--*) 取色、关键字(transparent/currentColor/inherit/…)、
 * `@media (forced-colors: active)` 块内的系统色。
 * 例外:违规节点上一行注释 `mesh-data-color: <原因>` 且文件登记于
 * theme-lint-exemptions.json;缺一即 error。无整文件白名单
 * (生成产物 tokens*.css 由 .stylelintrc.json ignoreFiles 排除,唯一文件级排除)。
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import stylelint from 'stylelint';
import {
  exemptionReason,
  findColorLiterals,
  isColorProperty,
  isExemptLine,
  loadExemptions,
} from '../lint-shared/color-grammar.mjs';

const {
  createPlugin,
  utils: { report, ruleMessages, validateOptions },
} = stylelint;

export const ruleName = 'mesh/no-hardcoded-colors';

export const messages = ruleMessages(ruleName, {
  rejected: (prop, literal) =>
    `${ruleName}: 属性 ${prop} 检测到硬编码色值 "${literal}"。颜色一律经 var(--<语义 token>) 取色;` +
    '数据色例外须在违规行上一行注释 "/* mesh-data-color: <原因> */" 并登记 theme-lint-exemptions.json(逐文件 + 行级,禁整文件白名单)。',
});

export const meta = {
  url: 'https://github.com/cnwenf/Mesh/blob/main/docs/specs/features/theme.md',
};

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REGISTRY_PATH = path.join(FRONTEND_ROOT, 'theme-lint-exemptions.json');

/** 节点是否处于 `@media (forced-colors: active)` 块内(祖先 at-rule 向上查找)。 */
function insideForcedColors(node) {
  let current = node.parent;
  while (current) {
    if (
      current.type === 'atrule' &&
      current.name.toLowerCase() === 'media' &&
      /\(forced-colors:\s*active\)/i.test(current.params)
    ) {
      return true;
    }
    current = current.parent;
  }
  return false;
}

/** 违规节点上一行是否存在 mesh-data-color 例外注释。 */
function hasExemptionComment(decl) {
  const prev = decl.prev();
  if (!prev || prev.type !== 'comment') return false;
  if (exemptionReason(prev.text) === null) return false;
  const commentEnd = prev.source?.end?.line;
  const declStart = decl.source?.start?.line;
  return commentEnd !== undefined && declStart !== undefined && commentEnd === declStart - 1;
}

const ruleFunction = (primary) => (root, result) => {
  const validOptions = validateOptions(result, ruleName, {
    actual: primary,
    possible: [true],
  });
  if (!validOptions) return;

  const registry = loadExemptions(REGISTRY_PATH);
  const sourceFile = root.source?.input?.file;
  const relPath = sourceFile !== undefined ? path.relative(FRONTEND_ROOT, sourceFile) : null;

  root.walkDecls((decl) => {
    if (decl.prop.startsWith('--')) return; // 自定义属性声明是 token 落点,不属组件取色
    if (!isColorProperty(decl.prop)) return;

    const hits = findColorLiterals(decl.value, { allowSystemColors: insideForcedColors(decl) });
    if (hits.length === 0) return;

    if (
      hasExemptionComment(decl) &&
      relPath &&
      isExemptLine(registry, relPath, decl.source.start.line)
    ) {
      return;
    }

    report({
      message: messages.rejected(decl.prop, hits[0]),
      node: decl,
      result,
      ruleName,
    });
  });
};

ruleFunction.ruleName = ruleName;
ruleFunction.messages = messages;
ruleFunction.meta = meta;

export default createPlugin(ruleName, ruleFunction);
