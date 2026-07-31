import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import * as design from '../index';
import { ConversationLayout } from '../patterns/ConversationLayout';

describe('ConversationLayout(design-quality.md §4.4 Conversation 模板)', () => {
  it('双栏结构:list 与 detail 各为带可访问名的 section', () => {
    render(
      <ConversationLayout list={<p>列表内容</p>} listLabel="会话列表" detailLabel="会话详情">
        <p>详情内容</p>
      </ConversationLayout>,
    );
    const list = screen.getByLabelText('会话列表');
    const detail = screen.getByLabelText('会话详情');
    expect(list.tagName).toBe('SECTION');
    expect(detail.tagName).toBe('SECTION');
    expect(list).toHaveClass('mesh-conversation-layout__list');
    expect(detail).toHaveClass('mesh-conversation-layout__detail');
    expect(list).toHaveTextContent('列表内容');
    expect(detail).toHaveTextContent('详情内容');
  });

  it('缺省 activePane=list(根节点 data-active-pane)', () => {
    const { container } = render(
      <ConversationLayout list={<p>l</p>} listLabel="L">
        <p>d</p>
      </ConversationLayout>,
    );
    expect(container.querySelector('.mesh-conversation-layout')).toHaveAttribute(
      'data-active-pane',
      'list',
    );
  });

  it('activePane=detail 驱动手机单栏可见窗格(CSS 据 data-active-pane 切换)', () => {
    const { container } = render(
      <ConversationLayout list={<p>l</p>} listLabel="L" activePane="detail">
        <p>d</p>
      </ConversationLayout>,
    );
    expect(container.querySelector('.mesh-conversation-layout')).toHaveAttribute(
      'data-active-pane',
      'detail',
    );
  });

  it('detailLabel 可省略(详情区无 aria-label 不报错)', () => {
    render(
      <ConversationLayout list={<p>l</p>} listLabel="L">
        <p>无标签详情</p>
      </ConversationLayout>,
    );
    expect(screen.getByText('无标签详情')).toBeInTheDocument();
  });

  it('自定义 className 合并到根节点', () => {
    const { container } = render(
      <ConversationLayout list={<p>l</p>} listLabel="L" className="mesh-chat">
        <p>d</p>
      </ConversationLayout>,
    );
    const root = container.querySelector('.mesh-conversation-layout')!;
    expect(root).toHaveClass('mesh-chat');
  });

  it('桶导出暴露 ConversationLayout 与 RunStateBadge(§11.1 patterns 层公共 API)', () => {
    expect(design.ConversationLayout).toBeTypeOf('function');
    expect(design.RunStateBadge).toBeTypeOf('function');
    expect(design.RUN_STATE_TONES).toBeTypeOf('object');
    expect(design.RUN_STATE_ICONS).toBeTypeOf('object');
  });
});
