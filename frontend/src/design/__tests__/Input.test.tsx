import { createRef, useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Input } from '../components/Input';

describe('Input', () => {
  it('label 经 <label htmlFor> 与输入框关联', () => {
    render(<Input label="Email" />);
    const input = screen.getByLabelText('Email');
    expect(input.tagName).toBe('INPUT');
  });

  it('受控:value + onChange', async () => {
    const onChange = vi.fn();
    function Harness(): React.JSX.Element {
      const [value, setValue] = useState('');
      return (
        <Input
          label="Name"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            onChange(event.target.value);
          }}
        />
      );
    }
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText('Name');
    await user.type(input, 'abc');
    expect(input).toHaveValue('abc');
    expect(onChange).toHaveBeenLastCalledWith('abc');
  });

  it('error 插槽:渲染错误文案 + aria-invalid + aria-describedby 关联', () => {
    render(<Input label="Email" error="Invalid email" />);
    const input = screen.getByLabelText('Email');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    const describedBy = input.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    const errorElement = document.getElementById(describedBy as string);
    expect(errorElement).toHaveTextContent('Invalid email');
  });

  it('hint 经 aria-describedby 关联;无 error/hint 时不带 aria-describedby', () => {
    const { rerender } = render(<Input label="A" hint="Help text" />);
    const input = screen.getByLabelText('A');
    const describedBy = input.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy as string)).toHaveTextContent('Help text');

    rerender(<Input label="A" />);
    expect(screen.getByLabelText('A')).not.toHaveAttribute('aria-describedby');
  });

  it('error 与 hint 同时存在时都进 describedby', () => {
    render(<Input label="A" hint="H" error="E" />);
    const input = screen.getByLabelText('A');
    const ids = (input.getAttribute('aria-describedby') ?? '').split(' ');
    expect(ids).toHaveLength(2);
    const texts = ids.map((id) => document.getElementById(id)?.textContent);
    expect(texts).toContain('H');
    expect(texts).toContain('E');
  });

  it('透传显式 id', () => {
    render(<Input label="Email" id="my-email" />);
    expect(screen.getByLabelText('Email')).toHaveAttribute('id', 'my-email');
  });

  it('转发 ref', () => {
    const ref = createRef<HTMLInputElement>();
    render(<Input label="R" ref={ref} />);
    expect(ref.current).toBeInstanceOf(HTMLInputElement);
  });
});
