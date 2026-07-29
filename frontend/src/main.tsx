import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { getApiClient } from './api/instance';
import { bindSyncClient, initCrossTabSync } from './state/settingsStore';
import { ROUTE_CHANGE_EVENT } from './design/themeNegotiation';
import './design/base.css';

// 阶段 2(MES-24):绑定偏好服务端同步客户端,
// settingsStore 的 setter 将 fire-and-forget 同步到 PATCH /api/v1/users/me。
bindSyncClient(getApiClient());

// theme.md §4.2(评审 T5②):跨标签页偏好/locator 写入即时同步(storage 事件)。
initCrossTabSync();

// 路由身份变更信号(供 ThemeProvider 判断当前路由是否期望工作区默认,§2.3/H3):
// SPA 客户端导航(pushState/replaceState)不触发 popstate,故在此统一派发。
function patchHistory(method: 'pushState' | 'replaceState'): void {
  const original = history[method];
  history[method] = function (this: History, ...args: Parameters<typeof original>) {
    const result = original.apply(this, args);
    window.dispatchEvent(new CustomEvent(ROUTE_CHANGE_EVENT));
    return result;
  } as typeof original;
}
patchHistory('pushState');
patchHistory('replaceState');

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root container #root not found');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
