/**
 * Runtime 展示层纯函数(§4.1–§4.4):心跳新鲜度 / 时长 / 内存 / 安装脚本生成。
 * 无副作用、无 i18n 依赖(单位枚举交由调用方经 t() 本地化),便于单测逐分支覆盖。
 */
import type { RuntimeRelease } from './types';

const MS_PER_SECOND = 1_000;
const MS_PER_MINUTE = 60 * MS_PER_SECOND;
const MS_PER_HOUR = 60 * MS_PER_MINUTE;
const MS_PER_DAY = 24 * MS_PER_HOUR;
const MB_PER_GB = 1024;
const SECONDS_PER_MINUTE = 60;
const SECONDS_PER_HOUR = 3600;

export type AgeUnit = 'seconds' | 'minutes' | 'hours' | 'days';

export interface HeartbeatAge {
  /** 距今数量(与 unit 配对;<1 单位向下取整为 0,如「0 秒前」) */
  readonly value: number;
  readonly unit: AgeUnit;
}

/**
 * 心跳新鲜度(§4.1「5s 前」/「离线 3m」的数值部分)。
 * lastHeartbeatAt 为 null(从未激活)返回 null;非法时间戳亦返回 null(不渲染垃圾值)。
 */
export function heartbeatAge(lastHeartbeatAt: string | null, nowMs: number): HeartbeatAge | null {
  if (lastHeartbeatAt === null) return null;
  const at = Date.parse(lastHeartbeatAt);
  if (Number.isNaN(at)) return null;
  const elapsedMs = Math.max(0, nowMs - at);
  if (elapsedMs < MS_PER_MINUTE) {
    return { value: Math.floor(elapsedMs / MS_PER_SECOND), unit: 'seconds' };
  }
  if (elapsedMs < MS_PER_HOUR) {
    return { value: Math.floor(elapsedMs / MS_PER_MINUTE), unit: 'minutes' };
  }
  if (elapsedMs < MS_PER_DAY) {
    return { value: Math.floor(elapsedMs / MS_PER_HOUR), unit: 'hours' };
  }
  return { value: Math.floor(elapsedMs / MS_PER_DAY), unit: 'days' };
}

/**
 * 秒 → `mm:ss`(≥1h 时 `h:mm:ss`),用于「已运行 03:21 / 上限 30:00」(§4.4)。
 * 负数 / 非有限值钳为 0。
 */
export function formatDurationSeconds(totalSeconds: number): string {
  const safe = Number.isFinite(totalSeconds) && totalSeconds > 0 ? Math.floor(totalSeconds) : 0;
  const hours = Math.floor(safe / SECONDS_PER_HOUR);
  const minutes = Math.floor((safe % SECONDS_PER_HOUR) / SECONDS_PER_MINUTE);
  const seconds = safe % SECONDS_PER_MINUTE;
  const mm = String(minutes).padStart(2, '0');
  const ss = String(seconds).padStart(2, '0');
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** MB → 「32 GB」/「512 MB」(§4.2 详情头);null → null(渲染「—」)。 */
export function formatMemoryMb(memoryMb: number | null): string | null {
  if (memoryMb === null || !Number.isFinite(memoryMb) || memoryMb < 0) return null;
  if (memoryMb >= MB_PER_GB) {
    const gb = memoryMb / MB_PER_GB;
    return `${Number(gb.toFixed(1))} GB`;
  }
  return `${Math.round(memoryMb)} MB`;
}

/** 发布包文件名(取 artifact_url 末段,供校验 / 解包命令逐条引用)。 */
export function artifactFileName(artifactUrl: string): string {
  const trimmed = artifactUrl.split('?')[0];
  const segments = trimmed.split('/');
  const last = segments[segments.length - 1];
  return last !== undefined && last !== '' ? last : 'mesh-runtime.tar.gz';
}

/**
 * §4.3 安装脚本生成:下载签名发布包 + 公钥 → 本地校验 sha256 与签名 →
 * 解包到可审阅目录 → 激活码写入 0600 文件再激活、用后即毁。
 *
 * 安全约束(runtime.md §3.1 安装红线):不提供 `curl | sh` 管道,命令逐条可审;
 * 激活码不进命令行参数 / shell 历史(经 `--activation-file` 受限文件读入)。
 */
export function buildInstallScript(release: RuntimeRelease, activationCode: string): string {
  const fileName = artifactFileName(release.artifact_url);
  return [
    '# a. Download the signed release bundle and the release public key',
    `curl -fsSLO ${release.artifact_url}`,
    `curl -fsSLO ${release.signature_url}`,
    `curl -fsSLO ${release.signing_key_url}`,
    '',
    '# b. Verify the checksum and the signature before executing anything',
    `echo "${release.sha256}  ${fileName}" | sha256sum -c -`,
    `minisign -Vm ${fileName} -p ${artifactFileName(release.signing_key_url)}`,
    '',
    '# c. Unpack into an auditable directory (nothing is executed implicitly)',
    `mkdir -p ~/.local/opt/mesh && tar -xzf ${fileName} -C ~/.local/opt/mesh`,
    '',
    '# d. Write the one-time activation code to a 0600 file, activate, then shred it',
    '#    The code never appears in command-line arguments or shell history.',
    `umask 077 && printf '%s' "${activationCode}" > activation.txt`,
    '~/.local/opt/mesh/mesh-runtime activate --activation-file ./activation.txt',
    'shred -u activation.txt',
  ].join('\n');
}
