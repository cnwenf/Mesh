import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { EmailChipsInput, MAX_BATCH_EMAILS, splitEmailInput } from '../EmailChipsInput';

function Harness(props: { initial?: string[]; maxCountHint?: string }): React.JSX.Element {
  const [emails, setEmails] = useState<string[]>(props.initial ?? []);
  return (
    <EmailChipsInput
      label="Emails"
      emails={emails}
      onChange={setEmails}
      invalidFormatHint="invalid email skipped"
      maxCountHint={props.maxCountHint ?? 'too many'}
      removeLabel="Remove"
    />
  );
}

describe('splitEmailInput(输入拆分:分隔符/去空/小写归一)', () => {
  it('按逗号/分号/空白拆分并小写归一', () => {
    expect(splitEmailInput('A@x.com, b@y.com;c@z.com d@w.com')).toEqual([
      'a@x.com',
      'b@y.com',
      'c@z.com',
      'd@w.com',
    ]);
  });

  it('空段被过滤', () => {
    expect(splitEmailInput('  ,  ; ')).toEqual([]);
  });
});

describe('EmailChipsInput(邀请邮箱 chip 输入,§4.2)', () => {
  it('回车提交成 chip', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByTestId('email-chips-input');
    await user.type(input, 'jane@corp.com{Enter}');
    expect(screen.getAllByTestId('email-chip')).toHaveLength(1);
    expect(screen.getByText('jane@corp.com')).toBeTruthy();
  });

  it('重复邮箱不重复添加(小写归一去重)', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['jane@corp.com']} />);
    await user.type(screen.getByTestId('email-chips-input'), 'JANE@corp.com{Enter}');
    expect(screen.getAllByTestId('email-chip')).toHaveLength(1);
  });

  it('非法格式不入 chip 并提示', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(screen.getByTestId('email-chips-input'), 'not-an-email{Enter}');
    expect(screen.queryAllByTestId('email-chip')).toHaveLength(0);
    expect(screen.getByTestId('email-chips-notice').textContent).toBe('invalid email skipped');
  });

  it('粘贴批量拆分入 chip', () => {
    render(<Harness />);
    const input = screen.getByTestId('email-chips-input');
    fireEvent.paste(input, {
      clipboardData: { getData: () => 'a@x.com, b@y.com;c@z.com' },
    });
    expect(screen.getAllByTestId('email-chip')).toHaveLength(3);
  });

  it('超过 50 上限提示并不再添加', async () => {
    const user = userEvent.setup();
    const initial = Array.from({ length: MAX_BATCH_EMAILS }, (_, index) => `u${index}@x.com`);
    render(<Harness initial={initial} />);
    await user.type(screen.getByTestId('email-chips-input'), 'extra@x.com{Enter}');
    expect(screen.getAllByTestId('email-chip')).toHaveLength(MAX_BATCH_EMAILS);
    expect(screen.getByTestId('email-chips-notice').textContent).toBe('too many');
  });

  it('点击 × 移除 chip', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['jane@corp.com']} />);
    await user.click(screen.getByRole('button', { name: 'Remove jane@corp.com' }));
    expect(screen.queryAllByTestId('email-chip')).toHaveLength(0);
  });
});
