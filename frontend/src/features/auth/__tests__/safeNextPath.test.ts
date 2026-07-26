import { describe, expect, it } from 'vitest';
import { DEFAULT_POST_AUTH_PATH, safeNextPath } from '../safeNextPath';

describe('safeNextPath(auth.md §4.1 回跳守卫:对浏览器 URL 归一化做等价校验,防开放重定向)', () => {
  it('null(未携带回跳目标)→ 默认路径', () => {
    expect(safeNextPath(null)).toBe(DEFAULT_POST_AUTH_PATH);
  });

  it('站内相对路径原样放行(根路径 / 功能页 / 携带查询串与 hash)', () => {
    expect(safeNextPath('/')).toBe('/');
    expect(safeNextPath('/issues')).toBe('/issues');
    expect(safeNextPath('/invite/invtk_x')).toBe('/invite/invtk_x');
    expect(safeNextPath('/issues?focus=1')).toBe('/issues?focus=1');
    expect(safeNextPath('/issues#comment-2')).toBe('/issues#comment-2');
  });

  it('协议相对 URL(`//`)→ 拒绝并回落默认路径', () => {
    expect(safeNextPath('//evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('///evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
  });

  it('反斜杠变体(`/\\`,解析器归一化为 /)→ 协议相对形态拒绝;站内归一化形态按解析结果放行', () => {
    expect(safeNextPath('/\\evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('/\\/evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
    // 反斜杠归一化后仍是站内路径 → 与浏览器行为一致,放行解析器归一化形态
    expect(safeNextPath('/issues\\..\\secret')).toBe('/secret');
  });

  it('TAB/LF/CR 夹带的协议相对载荷(解析器删除控制字符后成 //)→ 拒绝并回落默认路径', () => {
    expect(safeNextPath('/\t/evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('/\n/evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('/\r/evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('/\r\n/evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
  });

  it('含任何控制字符/空白的目标一律视为异常 → 拒绝并回落默认路径', () => {
    expect(safeNextPath('/\tevil')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('/\nevil')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('/\revil')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('/ \tevil')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('/issues /detail')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('/' + String.fromCharCode(0) + 'evil')).toBe(DEFAULT_POST_AUTH_PATH);
  });

  it('绝对 URL 与伪协议(解析后 origin 不同源)→ 拒绝并回落默认路径', () => {
    expect(safeNextPath('https://evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('http://evil.example/login')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath('javascript:alert(1)')).toBe(DEFAULT_POST_AUTH_PATH);
  });

  it('不可解析输入(URL 解析器抛错)→ 拒绝并回落默认路径', () => {
    // `http://[` 为 WHATWG 解析器必抛 TypeError 的畸形输入,覆盖 catch 分支
    expect(safeNextPath('http://[')).toBe(DEFAULT_POST_AUTH_PATH);
  });

  it('空串/空白打头 → 拒绝;裸域名/反斜杠打头经解析器解析为站内路径 → 按归一化形态放行', () => {
    expect(safeNextPath('')).toBe(DEFAULT_POST_AUTH_PATH);
    expect(safeNextPath(' //evil.example')).toBe(DEFAULT_POST_AUTH_PATH);
    // 裸域名与反斜杠打头:以 origin 为 base 解析后仍是站内路径(浏览器同样不会
    // 出站),与解析器等价放行归一化形态
    expect(safeNextPath('evil.example/x')).toBe('/evil.example/x');
    expect(safeNextPath('\\evil.example')).toBe('/evil.example');
  });
});
