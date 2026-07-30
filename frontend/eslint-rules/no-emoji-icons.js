/**
 * ESLint 自定义规则 `mesh/no-emoji-icons`(flat config 本地规则,design-quality §7.1)。
 *
 * 导航、按钮、状态、通知、自动值守触发器等 UI 表意位禁用 emoji 与字符图标,
 * 一律经统一 SVG 图标系统(design/components/icons.tsx)。
 *
 * 命中:JSX 文本、字符串字面量、模板串字面片段(含带插值模板)中含 emoji/象形/制表符号字符
 * (U+1F300–1FAFF 象形、U+2600–27BF 杂项符号与丁字符(含 ✓✕★☆⚙⚠✅)、
 *  U+231A–23FF 技术符号(含 ⏳)、U+2B00–2BFF、U+FE0F 变体选择符)。
 * 放行:
 * - UGC 场景——评论回应/用户内容(经配置层 ignores 排除 ReactionBar 等文件);
 * - 违规行上一行注释 `// mesh-emoji-ok: <原因>`(如 i18n 开发期缺失标记 ⚠[key],
 *   仅开发模式可见的产品外诊断前缀)。
 */
/**
 * 覆盖:象形/旗帜/ enclosed 区(U+1F000–1FAFF,含国旗 U+1F1E6–1F1FF 与 🀄🎴 等
 * U+1F000–1F2FF)、杂项符号与丁字符 U+2600–27BF(含 ✓✕★☆⚙⚠✅)、技术符号
 * U+231A–23FF(含 ⏳⌚)、箭头补充 U+2B00–2BFF、变体选择符 U+FE0F。
 */
const EMOJI_PATTERN =
  /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{231A}-\u{23FF}\u{2B00}-\u{2BFF}\u{FE0F}]/u;

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
        // 逐字面片段扫描:带插值的模板串同样不得在字面片段夹带 emoji
        // (验收 R1-M5:整体跳过即门禁可绕过)。例外注释锚点用整个模板节点
        // (quasi 前有反引号 token,getCommentsBefore 取不到上一行注释)。
        for (const quasi of node.quasis) {
          const fragment = quasi.value.cooked;
          if (fragment !== null && fragment !== '') checkString(node, fragment);
        }
      },
      JSXText(node) {
        checkString(node, node.value);
      },
    };
  },
};
