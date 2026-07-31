import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { DetailLayout } from '../patterns/DetailLayout';

describe('DetailLayout(详情模板:桌面两栏 / 窄容器属性抽屉,§4.4/§8.3)', () => {
  it('渲染头部、summary chips、主内容与侧栏', () => {
    render(
      <DetailLayout
        header={<div data-testid="header">头</div>}
        summaryChips={<span data-testid="chip">进行中</span>}
        main={<div data-testid="main">主</div>}
        aside={<div data-testid="aside">属性</div>}
        asideTitle="属性"
        asideTriggerLabel="属性"
        closeLabel="关闭"
      />,
    );
    expect(screen.getByTestId('detail-layout')).toBeInTheDocument();
    expect(screen.getByTestId('header')).toBeInTheDocument();
    expect(screen.getByTestId('detail-summary-chips')).toBeInTheDocument();
    expect(screen.getByTestId('chip')).toBeInTheDocument();
    expect(screen.getByTestId('main')).toBeInTheDocument();
    expect(screen.getByTestId('detail-aside')).toBeInTheDocument();
    // 属性按钮在场(窄容器经 CSS 显示;宽容器 CSS 隐藏)
    expect(screen.getByTestId('detail-aside-trigger')).toHaveAttribute('aria-expanded', 'false');
  });

  it('点击属性按钮打开底部 Drawer,关闭后回到触发点', async () => {
    const user = userEvent.setup();
    render(
      <DetailLayout
        header={<span />}
        main={<span />}
        aside={<div data-testid="aside-content">负责人</div>}
        asideTitle="属性"
        asideTriggerLabel="打开属性"
        closeLabel="关闭"
      />,
    );
    await user.click(screen.getByTestId('detail-aside-trigger'));
    expect(screen.getByTestId('detail-aside-trigger')).toHaveAttribute('aria-expanded', 'true');
    const dialog = screen.getByRole('dialog', { name: '属性' });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByTestId('detail-aside-sheet')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '关闭' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('detail-aside-trigger')).toHaveAttribute('aria-expanded', 'false');
  });

  it('无 aside 时不渲染属性按钮/侧栏/抽屉', () => {
    render(<DetailLayout header={<span />} main={<div data-testid="main">主</div>} />);
    expect(screen.queryByTestId('detail-aside-trigger')).toBeNull();
    expect(screen.queryByTestId('detail-aside')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('无 summaryChips 时不渲染 chips 容器', () => {
    render(<DetailLayout header={<span />} main={<span />} />);
    expect(screen.queryByTestId('detail-summary-chips')).toBeNull();
  });

  it('asideTitle/triggerLabel 缺省时按钮文案回退为空串且不崩溃', () => {
    render(<DetailLayout header={<span />} main={<span />} aside={<span>属性内容</span>} />);
    const trigger = screen.getByTestId('detail-aside-trigger');
    expect(trigger).toBeInTheDocument();
    expect(trigger).toHaveTextContent('');
  });

  it('className 合并', () => {
    render(<DetailLayout header={<span />} main={<span />} className="issue-detail" />);
    expect(screen.getByTestId('detail-layout')).toHaveClass('issue-detail');
  });
});
