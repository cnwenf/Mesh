/**
 * 安全上下文无关的 UUID v4 生成器(MES-129)。
 *
 * `crypto.randomUUID()` 仅在安全上下文(HTTPS / localhost)下存在;HTTP 部署
 * (`isSecureContext === false`)中为 `undefined`,裸调会抛 `TypeError`,导致所有
 * 带幂等键的写请求(POST/PUT/PATCH/DELETE)在 fetch 发出前即失败——前端统一归一
 * 为 `error.network`(界面提示「网络错误,请检查网络连接后重试」),而 GET 不带
 * 幂等键,故只有页面加载不受影响。
 *
 * 策略:优先复用原生 `randomUUID`;缺失时用 `crypto.getRandomValues`(不受安全
 * 上下文限制,任意上下文可用)按 RFC 4122 手工构造 v4——version 位 = 4、
 * variant 位 = 10。
 */

const UUID_BYTE_LENGTH = 16;
const VERSION_MASK = 0x0f;
const VERSION_4_BITS = 0x40; // RFC 4122 §4.1.3:version 4
const VARIANT_MASK = 0x3f;
const VARIANT_RFC4122_BITS = 0x80; // RFC 4122 §4.1.1:variant 10

export function uuidv4(): string {
  if (typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(UUID_BYTE_LENGTH);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & VERSION_MASK) | VERSION_4_BITS;
  bytes[8] = (bytes[8] & VARIANT_MASK) | VARIANT_RFC4122_BITS;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
