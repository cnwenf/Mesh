/**
 * 键盘入口一次性内联提示(onboarding.md §4.2,L513):
 * 成员首次进入工作区时,以单条可关闭内联横幅告知两个效率入口——
 * 命令面板(Ctrl/Cmd+K,跨模块搜索 + 命令全集)与快捷键帮助层(?,当前上下文键位)。
 * 非遮罩 tour(§1.3「不做交互式 tour」非目标);关闭即本地记忆不再出现,
 * 「已使用」同样落记忆(在浮层打开处统一调用 dismissKeyboardHint)。
 */
import { useState } from 'react';
import { Banner } from '../../design';
import { useT } from '../../i18n';
import { formatCombo } from '../../shortcuts/ShortcutProvider';
import { dismissKeyboardHint, isKeyboardHintDismissed } from './keyboardHint';

export function KeyboardHintBanner(): React.JSX.Element | null {
  const t = useT();
  // 惰性初始化:挂载时读一次本地记忆;组件自身 state 覆盖「本次点击关闭」,
  // 存储写入失败(隐私模式)也能保证本会话立即消失。
  const [dismissed, setDismissed] = useState(isKeyboardHintDismissed);
  if (dismissed) return null;
  return (
    <div className="mesh-onboarding__keyboard-hint" data-testid="keyboard-hint">
      <Banner
        tone="info"
        onDismiss={() => {
          dismissKeyboardHint();
          setDismissed(true);
        }}
        dismissLabel={t('onboarding.keyboardHint.dismiss')}
      >
        {t('onboarding.keyboardHint.palette', { combo: formatCombo('mod+k') })}{' '}
        {t('onboarding.keyboardHint.help')}
      </Banner>
    </div>
  );
}
