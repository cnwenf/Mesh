/**
 * Runtime 展示层纯函数单测:心跳新鲜度分档 / 时长格式 / 内存格式 /
 * 安装脚本生成(§4.3 安全约束:无 curl|sh,激活码经受限文件)。
 */
import { describe, expect, it } from 'vitest';
import {
  artifactFileName,
  buildInstallScript,
  formatDurationSeconds,
  formatMemoryMb,
  heartbeatAge,
} from '../format';
import type { RuntimeRelease } from '../types';

const NOW = Date.parse('2026-07-27T12:00:00Z');

describe('heartbeatAge(§4.1「5s 前」/「离线 3m」)', () => {
  it('null(从未激活)→ null', () => {
    expect(heartbeatAge(null, NOW)).toBeNull();
  });

  it('非法时间戳 → null(不渲染垃圾值)', () => {
    expect(heartbeatAge('not-a-date', NOW)).toBeNull();
  });

  it('秒级', () => {
    expect(heartbeatAge('2026-07-27T11:59:55Z', NOW)).toEqual({ value: 5, unit: 'seconds' });
  });

  it('分级', () => {
    expect(heartbeatAge('2026-07-27T11:57:00Z', NOW)).toEqual({ value: 3, unit: 'minutes' });
    expect(heartbeatAge('2026-07-27T09:00:00Z', NOW)).toEqual({ value: 3, unit: 'hours' });
    expect(heartbeatAge('2026-07-25T12:00:00Z', NOW)).toEqual({ value: 2, unit: 'days' });
  });

  it('未来时间戳钳为 0 秒', () => {
    expect(heartbeatAge('2026-07-27T12:00:30Z', NOW)).toEqual({ value: 0, unit: 'seconds' });
  });
});

describe('formatDurationSeconds(§4.4 已运行 / 上限)', () => {
  it('mm:ss', () => {
    expect(formatDurationSeconds(0)).toBe('00:00');
    expect(formatDurationSeconds(201)).toBe('03:21');
  });

  it('≥1h 时为 h:mm:ss', () => {
    expect(formatDurationSeconds(3661)).toBe('1:01:01');
  });

  it('负数 / 非有限值钳为 0', () => {
    expect(formatDurationSeconds(-5)).toBe('00:00');
    expect(formatDurationSeconds(Number.NaN)).toBe('00:00');
  });
});

describe('formatMemoryMb(§4.2 详情头)', () => {
  it('GB 换算', () => {
    expect(formatMemoryMb(32768)).toBe('32 GB');
    expect(formatMemoryMb(1536)).toBe('1.5 GB');
  });

  it('MB 原样', () => {
    expect(formatMemoryMb(512)).toBe('512 MB');
  });

  it('null / 非法 → null', () => {
    expect(formatMemoryMb(null)).toBeNull();
    expect(formatMemoryMb(-1)).toBeNull();
    expect(formatMemoryMb(Number.NaN)).toBeNull();
  });
});

describe('buildInstallScript(§4.3 安装安全)', () => {
  const release: RuntimeRelease = {
    artifact_url: 'https://releases.mesh.example/runtime/1.4.2/mesh-runtime_1.4.2_linux_x86_64.tar.gz',
    sha256: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
    signature_url:
      'https://releases.mesh.example/runtime/1.4.2/mesh-runtime_1.4.2_linux_x86_64.tar.gz.sig',
    signing_key_url: 'https://releases.mesh.example/mesh-release.pub',
  };

  it('下载 → 校验 sha256 + 签名 → 解包 → 受限文件激活,逐条可审', () => {
    const script = buildInstallScript(release, 'ACT-9F3K-2M7Q-XB4Z');
    expect(script).toContain('curl -fsSLO https://releases.mesh.example/runtime/1.4.2/');
    expect(script).toContain('sha256sum -c -');
    expect(script).toContain('minisign -Vm mesh-runtime_1.4.2_linux_x86_64.tar.gz -p mesh-release.pub');
    expect(script).toContain('tar -xzf mesh-runtime_1.4.2_linux_x86_64.tar.gz -C ~/.local/opt/mesh');
    expect(script).toContain('umask 077');
    expect(script).toContain('printf \'%s\' "ACT-9F3K-2M7Q-XB4Z" > activation.txt');
    expect(script).toContain('mesh-runtime activate --activation-file ./activation.txt');
    expect(script).toContain('shred -u activation.txt');
  });

  it('不提供 curl|sh 管道,激活码不进命令行参数', () => {
    const script = buildInstallScript(release, 'ACT-X');
    expect(script).not.toMatch(/curl[^|\n]*\|\s*sh/);
    expect(script).not.toContain('activate ACT-X');
  });
});

describe('artifactFileName', () => {
  it('取 URL 末段', () => {
    expect(artifactFileName('https://x.example/a/b/pkg.tar.gz')).toBe('pkg.tar.gz');
  });

  it('剥离 query 串', () => {
    expect(artifactFileName('https://x.example/pkg.tar.gz?sig=1')).toBe('pkg.tar.gz');
  });

  it('空末段回退默认名', () => {
    expect(artifactFileName('https://x.example/')).toBe('mesh-runtime.tar.gz');
  });
});
