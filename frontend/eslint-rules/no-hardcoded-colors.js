/**
 * ESLint 自定义规则 `mesh/no-hardcoded-colors`(flat config 本地规则,theme.md §5.4)。
 *
 * 命中:JSXAttribute style 对象内颜色键(camelCase 归一为 kebab 后按颜色位判定)
 * 的 Literal/TemplateLiteral 值含颜色字面量;JSXAttribute fill/stroke 字面量;
 * 任意对象表达式 style 同上。
 * 放行:var(--*) 开头/含段、关键字(transparent/currentColor/inherit/initial/unset/
 * none;简化为 currentColor 任意位置放行)、非静态值(表达式拼接)。
 * 例外:节点上一行注释 `// mesh-data-color: <原因>`(或块注释)且文件登记于
 * theme-lint-exemptions.json;缺一即 error。
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  exemptionReason,
  findColorLiterals,
  isColorProperty,
  isExemptLine,
  loadExemptions,
} from '../lint-shared/color-grammar.mjs';

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REGISTRY_PATH = path.join(FRONTEND_ROOT, 'theme-lint-exemptions.json');

/** 取静态字符串值(字符串字面量 / 无插值模板串);非静态返回 null(不参与静态门禁)。 */
function staticStringValue(node) {
  if (node.type === 'Literal' && typeof node.value === 'string') {
    return node.value;
  }
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) {
    return node.quasis.map((quasi) => quasi.value.cooked).join('');
  }
  return null;
}

/** React style 键 camelCase → CSS kebab-case(backgroundColor → background-color)。 */
function toKebabCase(key) {
  return key.replace(/[A-Z]/g, (upper) => `-${upper.toLowerCase()}`);
}

/** 对象属性键名(Identifier 或字符串 Literal),其余返回 null。 */
function propertyKeyName(node) {
  if (node.key.type === 'Identifier') return node.key.name;
  if (node.key.type === 'Literal' && typeof node.key.value === 'string') return node.key.value;
  return null;
}

export const noHardcodedColors = {
  meta: {
    type: 'problem',
    docs: {
      description:
        '禁止在 JSX 内联 style / SVG fill|stroke / style 对象中硬编码色值;颜色一律经 var(--<语义 token>)。',
    },
    messages: {
      hardcoded:
        'mesh/no-hardcoded-colors: {{prop}} 检测到硬编码色值 "{{value}}"。颜色一律经 var(--<语义 token>) 取色;' +
        '数据色例外须在违规行上一行注释 "// mesh-data-color: <原因>" 并登记 theme-lint-exemptions.json(逐文件 + 行级,禁整文件白名单)。',
    },
    schema: [],
  },
  create(context) {
    const registry = loadExemptions(REGISTRY_PATH);
    const sourceCode = context.sourceCode;
    const relPath = context.filename ? path.relative(FRONTEND_ROOT, context.filename) : null;

    /**
     * 例外双要件:上一行注释含 mesh-data-color 原因 + 登记表行级匹配。
     * 锚点候选:节点自身;无则上溯所属 JSXAttribute——注释可能写在
     * style 属性整体之前(多行 JSX 开标签),而非对象内属性键之前。
     */
    function isExempted(node) {
      if (relPath === null) return false;
      const anchors = [node];
      let current = node.parent;
      while (current) {
        if (current.type === 'JSXAttribute') {
          anchors.push(current);
          break;
        }
        current = current.parent;
      }
      for (const anchor of anchors) {
        const comments = sourceCode.getCommentsBefore(anchor);
        const prev = comments.length > 0 ? comments[comments.length - 1] : null;
        if (prev === null || exemptionReason(prev.value) === null) continue;
        if (prev.loc.end.line !== anchor.loc.start.line - 1) continue;
        return isExemptLine(registry, relPath, anchor.loc.start.line);
      }
      return false;
    }

    /**
     * @param valueNode 值节点(报错定位)
     * @param propName 颜色位名(消息用)
     * @param containerNode 承载节点(Property / JSXAttribute;例外注释锚点——
     *        注释附着在属性键前而非值字面量前,上一行判定须以承载节点为准)
     */
    function checkNode(valueNode, propName, containerNode) {
      const value = staticStringValue(valueNode);
      if (value === null) return;
      if (findColorLiterals(value).length === 0) return;
      if (isExempted(containerNode)) return;
      context.report({ node: valueNode, messageId: 'hardcoded', data: { prop: propName, value } });
    }

    return {
      JSXAttribute(node) {
        if (node.name.type !== 'JSXIdentifier') return;
        const attrName = node.name.name;
        if (attrName === 'style' || !isColorProperty(attrName)) return;
        if (node.value === null) return;
        if (node.value.type === 'Literal') {
          checkNode(node.value, attrName, node);
        } else if (
          node.value.type === 'JSXExpressionContainer' &&
          node.value.expression.type !== 'JSXEmptyExpression'
        ) {
          checkNode(node.value.expression, attrName, node);
        }
      },
      Property(node) {
        const key = propertyKeyName(node);
        if (key === null || !isColorProperty(toKebabCase(key))) return;
        checkNode(node.value, key, node);
      },
    };
  },
};
