/**
 * Field 字段外壳契约测试(design-quality §7.4)。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Field } from '../components/Field';

describe('Field(label/control/hint/error 一体)', () => {
  it('label 经 htmlFor 关联 render-prop 下发的控件 id', () => {
    render(
      <Field label="标题" id="demo">
        {({ controlProps }) => <input {...controlProps} />}
      </Field>,
    );
    expect(screen.getByLabelText('标题')).toBe(screen.getByRole('textbox'));
    expect(screen.getByRole('textbox')).toHaveAttribute('id', 'demo');
  });

  it('hint 与 error 同时经 aria-describedby 关联且 aria-invalid 置位', () => {
    render(
      <Field label="标题" id="demo" hint="建议一句话" error="不能为空">
        {({ controlProps }) => <input {...controlProps} />}
      </Field>,
    );
    const control = screen.getByRole('textbox');
    expect(control).toHaveAttribute('aria-invalid', 'true');
    const ids = (control.getAttribute('aria-describedby') ?? '').split(' ');
    expect(ids).toHaveLength(2);
    expect(document.getElementById(ids[0] ?? '')).toHaveTextContent('不能为空');
    expect(document.getElementById(ids[1] ?? '')).toHaveTextContent('建议一句话');
  });

  it('无 hint/error 时不设 aria-describedby 与 aria-invalid', () => {
    render(
      <Field label="标题">
        {({ controlProps }) => <input {...controlProps} />}
      </Field>,
    );
    const control = screen.getByRole('textbox');
    expect(control).not.toHaveAttribute('aria-describedby');
    expect(control).not.toHaveAttribute('aria-invalid');
  });

  it('required 经 aria-required 表达并渲染 aria-hidden 星号', () => {
    const { container } = render(
      <Field label="标题" required>
        {({ controlProps }) => <input {...controlProps} />}
      </Field>,
    );
    expect(screen.getByRole('textbox')).toHaveAttribute('aria-required', 'true');
    const star = container.querySelector('.mesh-field__required');
    expect(star).not.toBeNull();
    expect(star).toHaveAttribute('aria-hidden', 'true');
  });

  it('id 缺省时自动生成稳定前缀', () => {
    render(
      <Field label="标题">
        {({ controlProps }) => <input {...controlProps} data-testid="auto" />}
      </Field>,
    );
    expect(screen.getByTestId('auto').id).toMatch(/^mesh-field-/);
  });
});
