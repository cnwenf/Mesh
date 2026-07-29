import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { getApiClient } from './api/instance';
import { bindSyncClient, initCrossTabSync } from './state/settingsStore';
import './design/base.css';

// 阶段 2(MES-24):绑定偏好服务端同步客户端,
// settingsStore 的 setter 将 fire-and-forget 同步到 PATCH /api/v1/users/me。
bindSyncClient(getApiClient());

// theme.md §4.2(评审 T5②):跨标签页偏好/locator 写入即时同步(storage 事件)。
initCrossTabSync();

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root container #root not found');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
