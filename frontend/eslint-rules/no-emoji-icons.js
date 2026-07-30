/**
 * ESLint 自定义规则 `mesh/no-emoji-icons`(flat config 本地规则,design-quality §7.1)。
 *
 * 导航、按钮、状态、通知、自动值守触发器等 UI 表意位禁用 emoji 与字符图标,
 * 一律经统一 SVG 图标系统(design/components/icons.tsx)。
 *
 * 命中:JSX 文本、字符串字面量、无插值模板串中含 emoji/象形/制表符号字符
 * (U+1F300–1FAFF 象形、U+2600–27BF 杂项符号与丁字符(含 ✓✕★☆⚙⚠✅)、
 *  U+231A–23FF 技术符号(含 ⏳)、U+2B00–2BFF、U+FE0F 变体选择符)。
 * 放行:
 * - UGC 场景——评论回应/用户内容(经配置层 ignores 排除 ReactionBar 等文件);
 * - 违规行上一行注释 `// mesh-emoji-ok: <原因>`(如 i18n 开发期缺失标记 ⚠[key],
 *   仅开发模式可见的产品外诊断前缀)。
 */
const EMOJI_PATTERN =
  /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{231A}-\u{23FF}\u{2B00}-\u{2BFF}\u{FE0F}]/u;

const EXEMPTION_COMMENT = /mesh-emoji-ok:\s*\S/u;

export const noEmojiIcons = {
  meta: {
    type: 'problem',
    docs: {
      description:
        '禁止在 UI 表意位使用 emoji/字符图标;导航、按钮、状态、通知、触发器一律经统一 SVG 图标系统(§7.1)。',
    },
    messages: {
      emoji:
        'mesh/no-emoji-icons: 检测到 emoji/字符图标 "{{value}}"。UI 图标一律经 <Icon name=…>(design 统一图标系统);' +
        '仅评论回应等用户内容可用 emoji。确属例外的须在上一行注释 "// mesh-emoji-ok: <原因>"。',
    },
    schema: [],
  },
  create(context) {
    const sourceCode = context.sourceCode;

    /**
     * 违规节点(或其所属承载节点)上一行是否有 mesh-emoji-ok 例外注释(须给出原因)。
     * 锚点候选:节点自身;对象属性值上的注释写在属性键之前,故再取 Property 父节点
     * (与 no-hardcoded-colors 上溯 JSXAttribute 同理)。
     */
    function isExempted(node) {
      const anchors = [node];
      if (node.parent !== undefined && node.parent !== null && node.parent.type === 'Property') {
        anchors.push(node.parent);
      }
      for (const anchor of anchors) {
        const comments = sourceCode.getCommentsBefore(anchor);
        const prev = comments.length > 0 ? comments[comments.length - 1] : null;
        if (prev === null) continue;
        if (prev.loc.end.line !== anchor.loc.start.line - 1) continue;
        if (EXEMPTION_COMMENT.test(prev.value)) return true;
      }
      return false;
    }

    function checkString(node, value) {
      const match = EMOJI_PATTERN.exec(value);
      if (match === null) return;
      if (isExempted(node)) return;
      context.report({ node, messageId: 'emoji', data: { value: match[0] } });
    }

    return {
      Literal(node) {
        if (typeof node.value === 'string') checkString(node, node.value);
      },
      TemplateLiteral(node) {
        if (node.expressions.length > 0) return;
        const value = node.quasis.map((quasi) => quasi.value.cooked).join('');
        checkString(node, value);
      },
      JSXText(node) {
        checkString(node, node.value);
      },
    };
  },
};
