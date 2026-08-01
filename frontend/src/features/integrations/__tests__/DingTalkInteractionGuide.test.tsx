import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { DingTalkInteractionGuide } from '../DingTalkInteractionGuide';

describe('DingTalkInteractionGuide', () => {
  it('documents command affordances, acknowledgement, queue position, and two-stage stop feedback', async () => {
    renderWithProviders(
      <DingTalkInteractionGuide verbosity="final_only" ackTemplate="✅ 已接收，处理中" />,
    );

    expect(screen.getByTestId('dingtalk-command-help')).toHaveTextContent('/btw');
    expect(screen.getByTestId('dingtalk-command-help')).toHaveTextContent('/stop');
    expect(screen.getByTestId('dingtalk-command-help')).toHaveTextContent('/help');
    expect(screen.getByTestId('dingtalk-ack-preview')).toHaveTextContent('✅ 已接收，处理中');
    expect(screen.getByTestId('dingtalk-position-preview')).toHaveTextContent(/position 2/i);
    expect(screen.getByTestId('dingtalk-stop-feedback')).toHaveTextContent('⏳');
    expect(screen.getByTestId('dingtalk-stop-feedback')).toHaveTextContent('🛑');
    expect(screen.getByTestId('dingtalk-verbosity-preview')).toHaveTextContent(
      /Final result only/i,
    );
    expect(screen.getByTestId('dingtalk-notification-preview')).toHaveTextContent(
      /Final result only/i,
    );
    expect(screen.getByTestId('dingtalk-notification-body')).toHaveTextContent(
      /one final result notification/i,
    );

    await userEvent.click(screen.getByTestId('dingtalk-command-btw'));
    expect(screen.getByTestId('dingtalk-command-input')).toHaveValue('/btw ');
    await userEvent.type(screen.getByTestId('dingtalk-command-input'), 'focus on payment');
    expect(screen.getByTestId('dingtalk-command-preview')).toHaveTextContent(
      '/btw focus on payment',
    );

    await userEvent.click(screen.getByTestId('dingtalk-command-stop'));
    expect(screen.getByTestId('dingtalk-command-input')).toHaveValue('/stop ');
    await userEvent.click(screen.getByTestId('dingtalk-command-help-button'));
    expect(screen.getByTestId('dingtalk-command-input')).toHaveValue('/help');
  });

  it('previews every approval-card lifecycle state with disabled terminal controls and Mesh fallback', async () => {
    renderWithProviders(
      <DingTalkInteractionGuide verbosity="progress" ackTemplate="✅ Received" />,
    );

    expect(screen.getByTestId('dingtalk-notification-body')).toHaveTextContent(
      /accepted, running, and final/i,
    );
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Approval request/i);
    expect(screen.getByTestId('dingtalk-card-approve')).toBeEnabled();
    await userEvent.click(screen.getByTestId('dingtalk-card-approve'));
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Processing/i);
    expect(screen.getByTestId('dingtalk-card-approve')).toBeDisabled();

    await userEvent.selectOptions(screen.getByTestId('dingtalk-card-state'), 'approved');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Approved/i);
    expect(screen.getByTestId('dingtalk-card-approve')).toBeDisabled();

    await userEvent.selectOptions(screen.getByTestId('dingtalk-card-state'), 'duplicate');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Already processed/i);

    await userEvent.selectOptions(screen.getByTestId('dingtalk-card-state'), 'expired');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Expired/i);
    expect(screen.getByTestId('dingtalk-card-fallback')).toHaveAttribute('href', '/');

    await userEvent.selectOptions(screen.getByTestId('dingtalk-card-state'), 'forbidden');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/No permission/i);

    await userEvent.selectOptions(screen.getByTestId('dingtalk-card-state'), 'failed');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Failed/i);
    expect(screen.getByTestId('dingtalk-card-fallback')).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByTestId('dingtalk-card-state'), 'rejected');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Rejected/i);

    await userEvent.selectOptions(screen.getByTestId('dingtalk-card-state'), 'pending');
    await userEvent.click(screen.getByTestId('dingtalk-card-reject'));
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Processing/i);
  });
});
