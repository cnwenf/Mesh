/**
 * 项目模块特性级小组件渲染测试(§6.12 就地实现)。
 * 经 renderWithProviders(locale=en)渲染,断言可见文本/aria;文案取自 i18n 目录。
 */
import { useState } from 'react';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import type { LabeledTextareaProps } from '../widgets';
import {
  AvatarInitial,
  HealthIndicator,
  LabeledTextarea,
  ProgressBar,
  StatusBadge,
} from '../widgets';

describe('StatusBadge', () => {
  it('渲染本地化状态文案并带状态修饰类', () => {
    renderWithProviders(<StatusBadge status="active" label="Active" />);
    const badge = screen.getByText('Active');
    expect(badge.className).toContain('mesh-badge');
    expect(badge.className).toContain('mesh-projects__badge');
    expect(badge.className).toContain('mesh-projects__badge--active');
  });

  it('不同状态映射到对应修饰类', () => {
    renderWithProviders(<StatusBadge status="cancelled" label="Cancelled" />);
    expect(screen.getByText('Cancelled').className).toContain('mesh-projects__badge--cancelled');
  });
});

describe('HealthIndicator', () => {
  it('未设置健康度显示「未设置」文案', () => {
    renderWithProviders(<HealthIndicator health={null} />);
    expect(screen.getByText('No health set')).toBeInTheDocument();
  });

  it.each([
    ['on_track', 'On track'],
    ['at_risk', 'At risk'],
    ['off_track', 'Off track'],
  ] as const)('健康度 %s 显示文案 %s', (health, label) => {
    renderWithProviders(<HealthIndicator health={health} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('可交互健康度通过共享 Button 适配器渲染', () => {
    renderWithProviders(<HealthIndicator health="on_track" onClick={() => undefined} />);
    expect(screen.getByRole('button')).toHaveAttribute('data-slot', 'button');
  });
});

describe('AvatarInitial', () => {
  it('经共享头像适配器渲染缩写和可访问名', () => {
    renderWithProviders(<AvatarInitial name="jane" />);
    const avatar = screen.getByRole('img', { name: 'jane' });
    expect(avatar).toHaveClass('mesh-avatar');
    expect(avatar).toHaveTextContent('J');
  });

  it('空名回退占位符 ?', () => {
    renderWithProviders(<AvatarInitial name="" />);
    expect(screen.getByRole('img', { name: '?' })).toHaveTextContent('?');
  });

  it('提供 accessibleName 时用它作为头像可访问名', () => {
    renderWithProviders(<AvatarInitial name="bob" accessibleName="Bob Jones" />);
    expect(screen.getByRole('img', { name: 'Bob Jones' })).toHaveTextContent('BJ');
  });
});

describe('LabeledTextarea', () => {
  function Controlled(props: { onChange: (value: string) => void }) {
    const [value, setValue] = useState('');
    const handleChange = (next: string): void => {
      setValue(next);
      props.onChange(next);
    };
    const textareaProps: LabeledTextareaProps = {
      label: 'Note',
      value,
      onChange: handleChange,
      placeholder: 'Say something',
    };
    return <LabeledTextarea {...textareaProps} />;
  }

  it('标签与文本域关联,输入触发 onChange 并回显', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<Controlled onChange={onChange} />);
    const textarea = screen.getByLabelText('Note');
    expect(textarea).toHaveAttribute('data-slot', 'textarea');
    expect(textarea).toBeInTheDocument();
    await user.type(textarea, 'hi');
    expect(onChange).toHaveBeenLastCalledWith('hi');
    expect(screen.getByDisplayValue('hi')).toBeInTheDocument();
  });

  it('默认 3 行,可自定义 rows 与 placeholder', () => {
    renderWithProviders(
      <LabeledTextarea label="Body" value="" onChange={() => undefined} rows={6} placeholder="p" />,
    );
    const textarea = screen.getByLabelText('Body') as HTMLTextAreaElement;
    expect(textarea.rows).toBe(6);
    expect(textarea.placeholder).toBe('p');
  });
});

describe('ProgressBar', () => {
  it('将 0..1 进度映射为百分比并暴露数值信号', () => {
    renderWithProviders(<ProgressBar progress={0.5} title="3/10 done" />);
    const bar = screen.getByRole('progressbar', { name: '3/10 done' });
    expect(bar).toHaveAttribute('aria-valuenow', '50');
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
  });

  it('钳制越界进度到 [0,100]', () => {
    renderWithProviders(
      <>
        <ProgressBar progress={1.5} title="over" />
        <ProgressBar progress={-0.2} title="under" />
      </>,
    );
    expect(screen.getByRole('progressbar', { name: 'over' })).toHaveAttribute(
      'aria-valuenow',
      '100',
    );
    expect(screen.getByRole('progressbar', { name: 'under' })).toHaveAttribute(
      'aria-valuenow',
      '0',
    );
  });
});
