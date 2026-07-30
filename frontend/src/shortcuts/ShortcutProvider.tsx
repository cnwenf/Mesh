/**
 * 快捷键路由(README §6.12 power-user 快捷键体系)。
 *
 * 行为:
 * - window keydown 监听,卸载即清理;
 * - `mod` = macOS 上 metaKey、其他平台 ctrlKey(isMac 可注入以便测试);
 * - 输入框(input/textarea/select/contentEditable)聚焦时忽略裸键,仅保留 Ctrl/Cmd 组合;
 * - `mod+k` → onOpenPalette;裸 `?` → onOpenHelp;
 * - 序列键 `g i` / `g b` / `g m` / `g a`(SEQUENCE_WINDOW_MS 窗口,时钟可注入)
 *   与 `c` / `/` 等一律按归一化 combo 路由到注册表(ShortcutDef),受 activeContexts 过滤
 *   (global 恒激活);注册发生在 shell 层,本 Provider 只做路由。
 */
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { useShortcutRegistry } from './registry';

/** 序列键(G→…)第二键窗口,毫秒 */
export const SEQUENCE_WINDOW_MS = 1000;

const MODIFIER_ONLY_KEYS = new Set(['control', 'meta', 'shift', 'alt']);
const SEQUENCE_SECOND_KEYS = new Set(['i', 'b', 'm', 'a']);
const SEQUENCE_PREFIX = 'g';

const KEY_DISPLAY_NAMES: Record<string, string> = {
  esc: 'Esc',
  escape: 'Esc',
  space: 'Space',
  enter: 'Enter',
  return: 'Enter',
  tab: 'Tab',
  arrowup: '↑',
  arrowdown: '↓',
  arrowleft: '←',
  arrowright: '→',
  // mesh-emoji-ok: 键帽符号 ⌫ 为键盘按键排版记号(与 ↑↓←→/Kbd 同族),非 UI 表意图标
  backspace: '⌫',
  delete: 'Del',
  home: 'Home',
  end: 'End',
};

interface NavigatorWithUserAgentData extends Navigator {
  userAgentData?: { platform?: string };
}

/** 平台检测:macOS(含 iOS/iPadOS)→ true。可被 ShortcutProvider 的 isMac 注入覆盖。 */
export function detectMac(): boolean {
  if (typeof navigator === 'undefined') {
    return false;
  }
  if (/Mac|iP(hone|ad|od)/.test(navigator.platform ?? '')) {
    return true;
  }
  const uaPlatform = (navigator as NavigatorWithUserAgentData).userAgentData?.platform;
  return typeof uaPlatform === 'string' && /mac/i.test(uaPlatform);
}

function formatKeyToken(token: string, isMac: boolean): string {
  const lower = token.toLowerCase();
  if (lower === 'mod') {
    return isMac ? 'Cmd' : 'Ctrl';
  }
  if (lower === 'shift') {
    return 'Shift';
  }
  if (lower === 'alt') {
    return isMac ? 'Option' : 'Alt';
  }
  const named = KEY_DISPLAY_NAMES[lower];
  if (named !== undefined) {
    return named;
  }
  return token.length === 1 ? token.toUpperCase() : token;
}

/** 'mod+k' → 'Cmd+K'(mac)/ 'Ctrl+K'(其他);按键名归一展示(键名非 UI 文案)。 */
export function formatCombo(combo: string, isMac: boolean = detectMac()): string {
  return combo
    .split('+')
    .map((token) => formatKeyToken(token, isMac))
    .join('+');
}

function isFormFieldTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

/** 由键盘事件归一化 combo:'c'、'/'、'mod+k'、'alt+x' 等(与注册表约定一致)。 */
function comboFromEvent(event: KeyboardEvent): string {
  const lower = event.key.toLowerCase();
  const keyName = lower === ' ' ? 'space' : lower === 'escape' ? 'esc' : lower;
  const parts: string[] = [];
  if (event.ctrlKey || event.metaKey) {
    parts.push('mod');
  }
  if (event.altKey) {
    parts.push('alt');
  }
  parts.push(keyName);
  return parts.join('+');
}

export interface ShortcutProviderProps {
  children: ReactNode;
  onOpenHelp?: () => void;
  onOpenPalette?: () => void;
  /** mod 键平台判定注入(缺省 detectMac()):mac → metaKey,其他 → ctrlKey */
  isMac?: boolean;
  /** 序列键窗口(毫秒,可注入);缺省 SEQUENCE_WINDOW_MS */
  sequenceWindowMs?: number;
  /** 时钟注入(序列窗口判定);缺省 Date.now */
  now?: () => number;
}

interface PendingSequence {
  at: number;
}

export function ShortcutProvider(props: ShortcutProviderProps): React.JSX.Element {
  const propsRef = useRef(props);
  propsRef.current = props;
  const pendingRef = useRef<PendingSequence | null>(null);

  useEffect(() => {
    const runRegistered = (combo: string): void => {
      const { shortcuts, activeContexts } = useShortcutRegistry.getState();
      const def = shortcuts.find(
        (item) =>
          item.combo === combo && (item.group === 'global' || activeContexts.includes(item.group)),
      );
      def?.run();
    };

    const handleKeyDown = (event: KeyboardEvent): void => {
      const current = propsRef.current;
      const isMac = current.isMac ?? detectMac();
      const sequenceWindowMs = current.sequenceWindowMs ?? SEQUENCE_WINDOW_MS;
      const now = current.now ?? Date.now;

      const lower = event.key.toLowerCase();
      if (MODIFIER_ONLY_KEYS.has(lower)) {
        return;
      }

      const modMatches = isMac ? event.metaKey : event.ctrlKey;
      const inField = isFormFieldTarget(event.target);

      // 命令面板:输入框聚焦时同样生效(§6.12:输入框仅豁免裸键)
      if (modMatches && lower === 'k') {
        event.preventDefault();
        current.onOpenPalette?.();
        return;
      }

      if (inField && !modMatches) {
        return;
      }

      // 帮助层:裸 ?(shift+/)。部分浏览器/布局下 shift+/ 上报为 key='/' + shiftKey,
      // 两种形态都归一为帮助键,且避免误触 '/' 聚焦搜索。
      const isHelpKey = event.key === '?' || (event.key === '/' && event.shiftKey);
      if (isHelpKey && !modMatches && !event.altKey) {
        event.preventDefault();
        current.onOpenHelp?.();
        return;
      }

      // 其他带修饰键组合:直接按 combo 匹配,并终结序列待决态
      if (modMatches || event.altKey) {
        pendingRef.current = null;
        runRegistered(comboFromEvent(event));
        return;
      }

      // 序列键:G → I/B/M/A(窗口内)
      const pending = pendingRef.current;
      if (pending !== null) {
        pendingRef.current = null;
        if (now() - pending.at <= sequenceWindowMs && SEQUENCE_SECOND_KEYS.has(lower)) {
          event.preventDefault();
          runRegistered(`${SEQUENCE_PREFIX} ${lower}`);
          return;
        }
        // 超时或非序列第二键:落入下方,继续尝试匹配本键自身
      }

      if (lower === SEQUENCE_PREFIX) {
        pendingRef.current = { at: now() };
        return;
      }

      runRegistered(comboFromEvent(event));
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return <>{props.children}</>;
}
