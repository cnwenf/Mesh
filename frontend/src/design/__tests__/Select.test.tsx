import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Select } from '../components/Select';

describe('Select', () => {
  it('label 经 <label htmlFor> 关联,children 作为选项渲染', () => {
    render(
      <Select label="Priority">
        <option value="low">Low</option>
        <option value="high">High</option>
      </Select>,
    );
    const select = screen.getByLabelText('Priority');
    expect(select.tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: 'Low' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'High' })).toBeInTheDocument();
  });

  it('受控选择触发 onChange', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <Select label="Priority" defaultValue="low" onChange={onChange}>
        <option value="low">Low</option>
        <option value="high">High</option>
      </Select>,
    );
    await user.selectOptions(screen.getByLabelText('Priority'), 'high');
    expect(onChange).toHaveBeenCalled();
    expect(screen.getByLabelText('Priority')).toHaveValue('high');
  });

  it('error 插槽:aria-invalid + 文案 + describedby 关联', () => {
    render(
      <Select label="Priority" error="Required">
        <option value="">—</option>
      </Select>,
    );
    const select = screen.getByLabelText('Priority');
    expect(select).toHaveAttribute('aria-invalid', 'true');
    const describedBy = select.getAttribute('aria-describedby');
    expect(document.getElementById(describedBy as string)).toHaveTextContent('Required');
  });

  it('转发 ref', () => {
    const ref = createRef<HTMLSelectElement>();
    render(
      <Select label="P" ref={ref}>
        <option value="x">x</option>
      </Select>,
    );
    expect(ref.current).toBeInstanceOf(HTMLSelectElement);
  });
});
