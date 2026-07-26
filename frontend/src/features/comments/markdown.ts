/**
 * 客户端 Markdown 预览(composer 编辑/预览切换用)。
 * 仅用于「用户正在输入的草稿」的本地预览 —— 经 marked 解析后再经 DOMPurify
 * 白名单净化,产出可安全渲染的 HTML。已落库评论的展示一律使用服务端净化的
 * `body_html`(comment-inbox.md §5.1),不走此路径。
 *
 * 安全:绝不把用户输入直接当 HTML;marked 输出先经 DOMPurify 净化。
 */
import DOMPurify from 'dompurify';
import { marked } from 'marked';

/** 同步解析(marked 默认同步;关闭异步以避免 Promise 返回值)。 */
marked.setOptions({ async: false, gfm: true, breaks: true });

/** DOMPurify 配置类型:从 sanitize 第二参推导,避免依赖其命名空间导出。 */
type SanitizeConfig = NonNullable<Parameters<typeof DOMPurify.sanitize>[1]>;

/** DOMPurify 白名单配置:允许常见排版标签,禁止脚本/事件属性(默认即剥离)。 */
const SANITIZE_CONFIG: SanitizeConfig = {
  ALLOWED_TAGS: [
    'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'strong', 'em', 'del', 's', 'blockquote', 'code', 'pre',
    'ul', 'ol', 'li', 'a', 'img', 'table', 'thead', 'tbody',
    'tr', 'th', 'td', 'span', 'div', 'input',
  ],
  ALLOWED_ATTR: ['href', 'title', 'src', 'alt', 'class', 'target', 'rel', 'checked', 'disabled', 'type'],
  ALLOW_DATA_ATTR: false,
};

/**
 * 把 Markdown 草稿渲染为净化后的 HTML 字符串(供 dangerouslySetInnerHTML)。
 * 空输入返回空串。净化失败时回退空串(绝不返回未净化内容)。
 */
export function renderMarkdownPreview(markdown: string): string {
  const trimmed = markdown.trim();
  if (trimmed === '') return '';
  try {
    const rawHtml = marked.parse(trimmed) as string;
    // sanitize 在启用 RETURN_TRUSTED_TYPE 时可返回 TrustedHTML;此处强制 string。
    return DOMPurify.sanitize(rawHtml, SANITIZE_CONFIG) as string;
  } catch {
    return '';
  }
}
