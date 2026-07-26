/**
 * 登录后回跳目标(`?next=` 与 OAuth 往返)的站内路径守卫(auth.md §4.1 防开放重定向)。
 *
 * 策略:对「浏览器将如何解析该目标」做等价校验,而非对原始串做黑字符枚举
 * (CVE-2025-68470 的根本教训)。两层:
 *
 * 1. 控制字符/空白预检:WHATWG URL 解析器对 special-scheme 输入会从串中**任意
 *    位置删除** TAB(0x09)/LF(0x0A)/CR(0x0D),并修剪首尾 C0 控制符与空格——
 *    `/<TAB>/evil.example` 经删除后成为协议相对 `//evil.example`(外站)。凡含
 *    任何控制字符/空白的目标即视为异常载荷,直接拒绝(合法 SPA 路由不含这些字符)。
 * 2. 解析器等价校验:以站点 origin 为 base 解析目标,仅当解析结果 origin 与本站
 *    一致且路径以 `/` 开头时放行(返回解析器归一化后的 pathname+search+hash)。
 *    反斜杠归一化(`/<BACKSLASH>evil` → `//evil`)、协议相对、绝对 URL、
 *    `javascript:` 等一切变形经解析后 origin 不匹配,统一拒绝;无法解析亦拒绝。
 *
 * LoginPage 与 OAuthCallbackPage 共用此单一实现,避免守卫策略漂移。
 */

/** 回跳目标不可用时的默认路径(首页) */
export const DEFAULT_POST_AUTH_PATH = '/';

/**
 * 异常载荷字符集:C0 控制符(0x00–0x1F,含 TAB/LF/CR)+ DEL(0x7F)+ 空格。
 * 经字符码构造(不在源码中书写控制字符字面量)。浏览器 URL 归一化绕过的
 * 全部已知载荷字符均在此集;命中即拒绝。
 */
const FORBIDDEN_NEXT_CHARS = new RegExp(
  '[' +
    Array.from({ length: 0x20 }, (_, code) => String.fromCharCode(code)).join('') +
    String.fromCharCode(0x7f) +
    ' ]',
);

/**
 * 校验回跳目标为站内相对路径;非法输入返回默认路径。
 *
 * 拒绝:`//evil.example`(协议相对)、归一化后成为协议相对/外站的反斜杠变体
 * (如 `/\evil.example`)、`/<TAB>/evil.example`(TAB/LF/CR 删除后等价协议相对)、
 * `https://evil.example`(绝对 URL)、`javascript:…`(伪协议)、不可解析输入
 * (如 `http://[`)、含任何控制字符/空白的路径;反斜杠归一化后仍为站内路径者
 * 按解析器形态放行(与浏览器行为一致);放行:`/`、`/issues`、`/invite/invtk_x`、
 * `/issues?focus=1` 等站内路径(返回经 URL 解析器归一化后的形态;同源解析
 * 成功时 pathname 必以 `/` 开头,可直接拼接返回)。
 */
export function safeNextPath(raw: string | null): string {
  if (raw === null || FORBIDDEN_NEXT_CHARS.test(raw)) {
    return DEFAULT_POST_AUTH_PATH;
  }
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) {
      return DEFAULT_POST_AUTH_PATH;
    }
    return url.pathname + url.search + url.hash;
  } catch {
    return DEFAULT_POST_AUTH_PATH;
  }
}
