/**
 * 硬编码色值检测共享语法(Stylelint 与 ESLint 自定义规则、二者单测复用)。
 *
 * 规则语义(theme.md §5.4):颜色位属性中出现 #hex / rgb()/rgba()/hsl()/hsla()/
 * oklch()/oklab() / CSS 命名色字面量即命中;取色一律经 var(--<语义 token>)。
 * 例外仅「上一行 mesh-data-color 注释 + theme-lint-exemptions.json 登记」双要件,
 * 无整文件白名单(唯一文件级排除是生成产物 tokens*.css,由 ignoreFiles 实施)。
 */
import { readFileSync } from 'node:fs';

/** 颜色位属性(声明式属性名 / JSX style 键,kebab-case 小写)。 */
export const COLOR_PROPERTIES = new Set([
  'color',
  'background',
  'background-color',
  'border',
  'border-top',
  'border-right',
  'border-bottom',
  'border-left',
  'border-color',
  'border-top-color',
  'border-right-color',
  'border-bottom-color',
  'border-left-color',
  'outline',
  'outline-color',
  'fill',
  'stroke',
  'box-shadow',
  'text-shadow',
  'caret-color',
  'accent-color',
  'text-decoration-color',
  'column-rule-color',
]);

const BORDER_COLOR_FAMILY = /^border-[a-z-]*color$/;

/** 属性名是否颜色位(border-*-color 族按模式匹配,如 border-inline-start-color)。 */
export function isColorProperty(name) {
  const lower = String(name).toLowerCase();
  return COLOR_PROPERTIES.has(lower) || BORDER_COLOR_FAMILY.test(lower);
}

/** 白名单关键字(非颜色语义,不命中)。 */
export const COLOR_KEYWORDS = new Set([
  'transparent',
  'currentcolor',
  'inherit',
  'initial',
  'revert',
  'revert-layer',
  'unset',
  'none',
]);

/**
 * 系统色关键字——仅在 `@media (forced-colors: active)` 块内放行(theme.md §4.3)。
 * 块外出现按命名色命中(CSS 级);ESLint 侧简化为仅 currentColor 处处放行。
 */
export const SYSTEM_COLORS = new Set([
  'canvas',
  'canvastext',
  'highlight',
  'highlighttext',
  'graytext',
  'linktext',
  'visitedtext',
  'activetext',
  'buttontext',
  'buttonface',
  'buttonborder',
  'field',
  'fieldtext',
  'selecteditem',
  'selecteditemtext',
  'mark',
  'marktext',
]);

/** CSS 命名色静态表(CSS Color 4 经典 140 色 + rebeccapurple;系统色另表)。 */
export const CSS_NAMED_COLORS = new Set([
  'aliceblue', 'antiquewhite', 'aqua', 'aquamarine', 'azure', 'beige', 'bisque',
  'black', 'blanchedalmond', 'blue', 'blueviolet', 'brown', 'burlywood', 'cadetblue',
  'chartreuse', 'chocolate', 'coral', 'cornflowerblue', 'cornsilk', 'crimson', 'cyan',
  'darkblue', 'darkcyan', 'darkgoldenrod', 'darkgray', 'darkgreen', 'darkgrey',
  'darkkhaki', 'darkmagenta', 'darkolivegreen', 'darkorange', 'darkorchid', 'darkred',
  'darksalmon', 'darkseagreen', 'darkslateblue', 'darkslategray', 'darkslategrey',
  'darkturquoise', 'darkviolet', 'deeppink', 'deepskyblue', 'dimgray', 'dimgrey',
  'dodgerblue', 'firebrick', 'floralwhite', 'forestgreen', 'fuchsia', 'gainsboro',
  'ghostwhite', 'gold', 'goldenrod', 'gray', 'green', 'greenyellow', 'grey',
  'honeydew', 'hotpink', 'indianred', 'indigo', 'ivory', 'khaki', 'lavender',
  'lavenderblush', 'lawngreen', 'lemonchiffon', 'lightblue', 'lightcoral', 'lightcyan',
  'lightgoldenrodyellow', 'lightgray', 'lightgreen', 'lightgrey', 'lightpink',
  'lightsalmon', 'lightseagreen', 'lightskyblue', 'lightslategray', 'lightslategrey',
  'lightsteelblue', 'lightyellow', 'lime', 'limegreen', 'linen', 'magenta', 'maroon',
  'mediumaquamarine', 'mediumblue', 'mediumorchid', 'mediumpurple', 'mediumseagreen',
  'mediumslateblue', 'mediumspringgreen', 'mediumturquoise', 'mediumvioletred',
  'midnightblue', 'mintcream', 'mistyrose', 'moccasin', 'navajowhite', 'navy',
  'oldlace', 'olive', 'olivedrab', 'orange', 'orangered', 'orchid', 'palegoldenrod',
  'palegreen', 'paleturquoise', 'palevioletred', 'papayawhip', 'peachpuff', 'peru',
  'pink', 'plum', 'powderblue', 'purple', 'rebeccapurple', 'red', 'rosybrown',
  'royalblue', 'saddlebrown', 'salmon', 'sandybrown', 'seagreen', 'seashell', 'sienna',
  'silver', 'skyblue', 'slateblue', 'slategray', 'slategrey', 'snow', 'springgreen',
  'steelblue', 'tan', 'teal', 'thistle', 'tomato', 'turquoise', 'violet', 'wheat',
  'white', 'whitesmoke', 'yellow', 'yellowgreen',
]);

/** 命名色 + 系统色统一匹配表(系统色块外同样命中,由调用方按上下文放行)。 */
const WORD_COLORS = new Set([...CSS_NAMED_COLORS, ...SYSTEM_COLORS]);

const HEX_LITERAL = /#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b/gi;
const FUNCTION_LITERAL = /\b(?:rgba?|hsla?|oklch|oklab)\s*\(/gi;
const WORD_COLOR_LITERAL = new RegExp(`\\b(?:${[...WORD_COLORS].join('|')})\\b`, 'gi');

/**
 * 把值中的 `var(...)` 段(含嵌套括号与 fallback)以等长空白掩掉。
 * 颜色来自 var(--*) 即视为经 token 取色,fallback 中的字面量不参与命中。
 */
export function maskVarSegments(value) {
  let out = '';
  let i = 0;
  while (i < value.length) {
    if (value.slice(i, i + 4).toLowerCase() === 'var(' && (i === 0 || /[^a-z-]/i.test(value[i - 1]))) {
      let depth = 0;
      let j = i;
      for (; j < value.length; j += 1) {
        if (value[j] === '(') depth += 1;
        else if (value[j] === ')') {
          depth -= 1;
          if (depth === 0) {
            j += 1;
            break;
          }
        }
      }
      out += ' '.repeat(j - i);
      i = j;
    } else {
      out += value[i];
      i += 1;
    }
  }
  return out;
}

/**
 * 扫描一个颜色位值中的硬编码色值字面量。
 * @param value 声明值 / style 字符串。
 * @param options.allowSystemColors 处于 `@media (forced-colors: active)` 块内时为 true。
 * @returns 命中的字面量数组(空 = 通过)。
 */
export function findColorLiterals(value, { allowSystemColors = false } = {}) {
  const masked = maskVarSegments(String(value));
  const hits = [];
  for (const match of masked.matchAll(HEX_LITERAL)) hits.push(match[0]);
  for (const match of masked.matchAll(FUNCTION_LITERAL)) hits.push(match[0].trim());
  for (const match of masked.matchAll(WORD_COLOR_LITERAL)) {
    const word = match[0].toLowerCase();
    if (COLOR_KEYWORDS.has(word)) continue;
    if (allowSystemColors && SYSTEM_COLORS.has(word)) continue;
    hits.push(match[0]);
  }
  return hits;
}

/** 例外注释标记:`mesh-data-color: <非空原因>`(CSS 与 JS 注释体通用)。 */
export const EXEMPTION_MARKER = 'mesh-data-color:';

/**
 * 解析注释体内的例外原因。
 * @returns 非空原因字符串;非例外注释或原因为空返回 null。
 */
export function exemptionReason(commentText) {
  const markerIndex = String(commentText).indexOf(EXEMPTION_MARKER);
  if (markerIndex === -1) return null;
  const reason = commentText.slice(markerIndex + EXEMPTION_MARKER.length).trim();
  return reason.length > 0 ? reason : null;
}

/**
 * 载入例外登记表(theme-lint-exemptions.json)。
 * 结构:{ "files": { "<repo-relative path>": { "reason": "...", "lines": [<行号>...] } } }
 * lines 为空数组 = 该文件任意带注释处均放行(须 reason 充分,如色板生成型代码)。
 */
export function loadExemptions(registryPath) {
  try {
    const parsed = JSON.parse(readFileSync(registryPath, 'utf8'));
    if (parsed && typeof parsed === 'object' && parsed.files && typeof parsed.files === 'object') {
      return parsed;
    }
    return { files: {} };
  } catch {
    return { files: {} };
  }
}

/**
 * 判定某文件某行是否具备例外资格(登记 + 行匹配;缺登记一律 false)。
 */
export function isExemptLine(registry, relPath, line) {
  const entry = registry.files[relPath];
  if (!entry || typeof entry.reason !== 'string' || entry.reason.trim() === '') return false;
  if (!Array.isArray(entry.lines)) return false;
  if (entry.lines.length === 0) return true;
  return entry.lines.includes(line);
}
