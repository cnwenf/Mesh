/**
 * API 契约通知桥(L252)— 订阅契约通知总线(notices.ts),经 ToastProvider
 * 以 i18n 文案呈现:429 退避提示(含 Retry-After 秒数)与 Deprecation/Sunset
 * 一次性升级提示。挂载于 App 层 ToastProvider 内,全路由生效。
 *
 * client 拦截层不硬编码可见文案;本组件负责取本地化文案并补 toast 关闭按钮名。
 */
import { useEffect } from 'react';
import { onApiNotice } from '../api/notices';
import { useToast } from '../design';
import { useT } from '../i18n';

/** 无渲染输出的纯订阅组件:返回 null,仅为把总线通知转成 toast。 */
export function ApiNoticeToasts(): null {
  const { addToast } = useToast();
  const t = useT();

  useEffect(() => {
    const unsubscribe = onApiNotice((notice) => {
      if (notice.kind === 'rate_limited') {
        const message =
          notice.retryAfterSeconds === undefined
            ? t('api.rateLimitedFallback')
            : t('api.rateLimited', { seconds: notice.retryAfterSeconds });
        addToast(message, { tone: 'warn', closeLabel: t('a11y.dismiss') });
        return;
      }
      addToast(t('api.deprecated'), { tone: 'warn', closeLabel: t('a11y.dismiss') });
    });
    return unsubscribe;
  }, [addToast, t]);

  return null;
}
