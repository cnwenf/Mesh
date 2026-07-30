/**
 * executionLabel —— 契约保真标签工具单测。
 * 纯函数 + 桩 t,不触 Provider / 网络。
 */
import { describe, expect, it } from 'vitest';
import {
  EXECUTION_SHORT_ID_LENGTH,
  executionDisplayLabel,
  executionShortId,
  executionTriggerLabelKey,
} from '../executionLabel';

describe('executionShortId', () => {
  it('UUID 取首段 8 个字符', () => {
    expect(executionShortId('5f1c2a6e-9b4d-4c1a-8e2f-7a3b9c0d1e2f')).toBe('5f1c2a6e');
  });

  it('短于上限的 ID 原样返回(契约栈语义化 ID)', () => {
    expect(executionShortId('exec-1')).toBe('exec-1');
  });

  it('恰为上限长度的 ID 原样返回', () => {
    expect(executionShortId('abcdefgh')).toBe('abcdefgh');
  });

  it('短 ID 上限常量为 8', () => {
    expect(EXECUTION_SHORT_ID_LENGTH).toBe(8);
  });
});

describe('executionTriggerLabelKey', () => {
  it.each(['assign', 'mention', 'autopilot', 'manual', 'chat', 'integration'])(
    '已知 trigger %s → 对应 triggerKind 键',
    (trigger) => {
      expect(executionTriggerLabelKey(trigger)).toBe('runtimes.execution.triggerKind.' + trigger);
    },
  );

  it('未知 trigger 落通用键(不拼不存在的键)', () => {
    expect(executionTriggerLabelKey('teleport')).toBe('runtimes.execution.triggerKind.unknown');
  });

  it('空串视为未知', () => {
    expect(executionTriggerLabelKey('')).toBe('runtimes.execution.triggerKind.unknown');
  });
});

describe('executionDisplayLabel', () => {
  it('trigger 文案 + 分隔符 + 短 ID', () => {
    const t = (key: string): string =>
      key === 'runtimes.execution.triggerKind.assign' ? '分派' : key;
    expect(executionDisplayLabel(t, { id: '5f1c2a6e-9b4d', trigger: 'assign' })).toBe(
      '分派 · 5f1c2a6e',
    );
  });

  it('未知 trigger 经通用文案兜底', () => {
    const t = (key: string): string => (key.endsWith('.unknown') ? '触发' : key);
    expect(executionDisplayLabel(t, { id: 'exec-9', trigger: 'teleport' })).toBe('触发 · exec-9');
  });

  it('全部已知 trigger 均可组合成非空标签', () => {
    for (const trigger of ['assign', 'mention', 'autopilot', 'manual', 'chat', 'integration']) {
      const label = executionDisplayLabel((key) => key, { id: 'exec-1', trigger });
      expect(label).toBe('runtimes.execution.triggerKind.' + trigger + ' · exec-1');
    }
  });
});
