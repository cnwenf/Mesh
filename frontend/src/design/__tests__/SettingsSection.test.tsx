/**
 * SettingsSection — 卡片分区:标题(h2)/描述/body/footer;danger 语义色调。
 * SettingsFieldRow — label + control + hint 行。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SettingsFieldRow } from '../patterns/SettingsFieldRow';
import { SettingsSection } from '../patterns/SettingsSection';

describe('SettingsSection', () => {
  it('渲染标题、描述与主体', () => {
    render(
      <SettingsSection title="General" description="Basic workspace info">
        <p>body content</p>
      </SettingsSection>,
    );
    expect(screen.getByRole('heading', { level: 2, name: 'General' })).toBeInTheDocument();
    expect(screen.getByText('Basic workspace info')).toBeInTheDocument();
    expect(screen.getByText('body content')).toBeInTheDocument();
  });

  it('无描述时不渲染描述节点', () => {
    render(
      <SettingsSection title="General">
        <p>body</p>
      </SettingsSection>,
    );
    expect(screen.queryByText('Basic workspace info')).not.toBeInTheDocument();
  });

  it('提供 footer 时渲染底部操作区', () => {
    render(
      <SettingsSection title="General" footer={<button type="button">Save</button>}>
        <p>body</p>
      </SettingsSection>,
    );
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });

  it('danger 色调追加 --danger 类(语义边界/标题)', () => {
    render(
      <SettingsSection title="Danger zone" tone="danger">
        <p>destructive</p>
      </SettingsSection>,
    );
    const region = screen.getByRole('region', { name: 'Danger zone' });
    expect(region.className).toContain('mesh-settings-section--danger');
  });

  it('默认色调不带 --danger 类', () => {
    render(
      <SettingsSection title="General">
        <p>body</p>
      </SettingsSection>,
    );
    const region = screen.getByRole('region', { name: 'General' });
    expect(region.className).not.toContain('--danger');
  });

  it('rows 布局为紧凑设置行追加稳定类名', () => {
    render(
      <SettingsSection title="Preferences" layout="rows">
        <p>body</p>
      </SettingsSection>,
    );
    expect(screen.getByRole('region', { name: 'Preferences' })).toHaveClass(
      'mesh-settings-section--rows',
    );
  });
});

describe('SettingsFieldRow', () => {
  it('渲染 label、控件与 hint', () => {
    render(
      <SettingsFieldRow label="Default theme" hint="Applies to members without a choice">
        <select aria-label="theme" />
      </SettingsFieldRow>,
    );
    expect(screen.getByText('Default theme')).toBeInTheDocument();
    expect(screen.getByText('Applies to members without a choice')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'theme' })).toBeInTheDocument();
  });

  it('省略 label/hint 时仅渲染控件', () => {
    const { container } = render(
      <SettingsFieldRow>
        <input aria-label="bare" />
      </SettingsFieldRow>,
    );
    expect(screen.getByRole('textbox', { name: 'bare' })).toBeInTheDocument();
    expect(container.querySelector('.mesh-settings-field-row__label')).toBeNull();
    expect(container.querySelector('.mesh-settings-field-row__hint')).toBeNull();
  });
});
