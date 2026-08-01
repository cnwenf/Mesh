import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { CapabilitySignals } from '../CapabilitySignals';

describe('CapabilitySignals', () => {
  it('空声明不渲染列表', () => {
    const { container } = renderWithProviders(<CapabilitySignals declarations={[]} />);
    expect(container.querySelector('.mesh-skills__capabilities')).toBeNull();
  });

  it('展示显式权限并保留调用方样式类', () => {
    renderWithProviders(
      <CapabilitySignals
        declarations={[{ capability: 'issue:write', permission: 'write' }]}
        className="extra-signal"
      />,
    );
    const list = screen.getByRole('list', { name: /Required capabilities|所需能力/ });
    expect(list).toHaveClass('extra-signal');
    expect(list).toHaveTextContent('issue:write');
    expect(list).toHaveTextContent(/Write|写入/);
  });
});
