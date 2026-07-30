# MES-111 Phase 1 设计系统底座走查存证

真实浏览器(Chromium + mock 契约栈)走查截图,覆盖桌面/手机 × 亮/暗四组合,
验证 design-quality.md §5/§6 令牌与排版体系落地后的真实渲染:

- `desktop-home-light.png` / `desktop-home-dark.png`:1440×900 首页,亮暗双主题。
  canvas→surface→raised 表面分层、品牌强调色、14px 正文密度、状态色三元组。
- `desktop-login-light.png` / `desktop-login-dark.png`:1440×900 登录页 PublicFlow
  框架;暗色经 `mesh.settings.v1` 持久化偏好预置(theme.md 协商链,防闪烁)。
- `phone-home-light.png` / `phone-home-dark.png` / `phone-board-light.png`:
  390×844 手机形态,底部导航在场、无页面级横向溢出。

页面级逐页存证(成员/收件箱/聊天/设置等)随后续各页面批次补齐。
