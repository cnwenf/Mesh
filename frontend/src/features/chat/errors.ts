/**
 * 聊天模块错误归一(chat-session.md §3.4 / README §6.14)。
 * 把任意捕获到的错误归一为 i18n 文案键:MeshApiError 取其 code(经 errorToI18nKey),
 * 其余(网络层/未知)回退到调用方指定的通用键。集中此判别,避免各组件重复三元分支。
 */
import { MeshApiError, errorToI18nKey } from '../../api';

/** 将未知错误归一为 i18n 键;非 MeshApiError 回退 fallbackKey(默认通用错误)。 */
export function toErrorKey(err: unknown, fallbackKey = 'common.unknownError'): string {
  return err instanceof MeshApiError ? errorToI18nKey(err) : fallbackKey;
}
