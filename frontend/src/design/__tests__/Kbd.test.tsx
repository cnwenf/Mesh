import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Kbd } from '../components/Kbd';

describe('Kbd(快捷键帮助层的按键帽)', () => {
  it('渲染为原生 <kbd> 元素并展示子内容', () => {
    render(<Kbd>K</Kbd>);
    const element = screen.getByText('K');
    expect(element.tagName).toBe('KBD');
    expect(element.className).toContain('mesh-kbd');
    expect(element).toHaveAttribute('data-slot', 'kbd');
  });
});
