/**
 * 快捷键分发(README §6.12 / search-command-palette.md §4.3.1,评审 P2 四层链)。
 *
 * 按键分发总优先级链(从高到低,高层覆盖低层,每次按键只执行一个 handler):
 *
 * 1. **输入控件**:焦点在 input/textarea/select/contentEditable 时,只放行
 *    显式 mod 组合;表单语义键(Esc/Tab/Enter)走浏览器原生语义,不由本分发器路由;
 * 2. **最上层弹层**:overlayStack 非空时背景页面快捷键全屏蔽,仅弹层自身
 *    键绑定(onKeyDown)与 Esc 分层关闭语义生效(§4.5);
 * 3. **页面上下文组**:combo 命中且 group 属 activeContexts 的 handler 中取
 *    特异性最高者(issue > board > global;chat 独占,§4.3.1 规则 1);
 * 4. **全局组**:global 组恒激活,与第 3 层同一仲裁(global 特异性最低)。
 *
 * IME 豁免(评审 P1):compositionstart→compositionend 期间(isComposing 为真)
 * 一切快捷键不触发——含单字符、序列键与聊天 Enter 发送(发送处理器自查)。
 * 序列键 G→I/B/M/A 保持 1000ms 窗口;等待态经 sequencePendingLabel 呈现
 * (data-testid=sequence-hint)。`/` 分发时 preventDefault(回避浏览器快速查找)。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:分发工具函数与 Provider 同文件 */
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { arbitrateShortcut, useShortcutRegistry } from './registry';
import { handleOverlayEscape, isFormFieldElement, topOverlay } from './overlayStack';

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

/** 组合输入判定(评审 P1):isComposing 或keyCode 229 均视为 IME 组合中。 */
function isComposingEvent(event: KeyboardEvent): boolean {
  return event.isComposing === true || event.keyCode === 229;
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
  /**
   * 序列等待态提示文案(经 i18n 外部化,如 t('shortcuts.sequencePending'));
   * 提供时渲染 data-testid=sequence-hint 固定提示,缺省不渲染。
   */
  sequencePendingLabel?: string;
}

interface PendingSequence {
  at: number;
}

export function ShortcutProvider(props: ShortcutProviderProps): React.JSX.Element {
  const propsRef = useRef(props);
  propsRef.current = props;
  const pendingRef = useRef<PendingSequence | null>(null);
  const composingRef = useRef(false);
  const [sequencePending, setSequencePending] = useState(false);

  const clearPending = (): void => {
    if (pendingRef.current !== null) {
      pendingRef.current = null;
      setSequencePending(false);
    }
  };

  useEffect(() => {
    const onCompositionStart = (): void => {
      composingRef.current = true;
      // 组合输入开始即终结序列待决态(组合中的字符不是序列第二键)。
      clearPending();
    };
    const onCompositionEnd = (): void => {
      composingRef.current = false;
    };
    window.addEventListener('compositionstart', onCompositionStart);
    window.addEventListener('compositionend', onCompositionEnd);
    return () => {
      window.removeEventListener('compositionstart', onCompositionStart);
      window.removeEventListener('compositionend', onCompositionEnd);
    };
  }, []);

  useEffect(() => {
    /** 第 3/4 层:注册表确定性仲裁(§4.3.1),每次按键只执行一个 handler。 */
    const runArbitrated = (combo: string): void => {
      const { shortcuts, activeContexts } = useShortcutRegistry.getState();
      const def = arbitrateShortcut(shortcuts, combo, activeContexts);
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

      // IME 豁免(评审 P1):组合输入期间一切快捷键不触发。
      if (composingRef.current || isComposingEvent(event)) {
        return;
      }

      const modMatches = isMac ? event.metaKey : event.ctrlKey;
      const inField = isFormFieldElement(event.target);

      // —— 第 1 层:输入控件。只放行显式 mod 组合;Esc/Tab/Enter 为浏览器
      // 原生表单语义,不由分发器路由(评审 R4 注)。
      if (inField && !modMatches) {
        return;
      }

      // —— 第 2 层:最上层弹层。背景页面快捷键全屏蔽,仅弹层自身键绑定
      // 与 Esc 分层关闭语义生效(§4.3.1 / §4.5)。
      const overlay = topOverlay();
      if (overlay !== null) {
        overlay.onKeyDown?.(event);
        if (lower === 'escape') {
          handleOverlayEscape();
        }
        return;
      }

      // 命令面板:显式 mod 组合,输入框聚焦时同样生效(第 1 层已放行 mod)。
      if (modMatches && lower === 'k') {
        event.preventDefault();
        clearPending();
        current.onOpenPalette?.();
        return;
      }

      // 帮助层:裸 ?(shift+/)。部分浏览器/布局下 shift+/ 上报为 key='/' + shiftKey,
      // 两种形态都归一为帮助键,且避免误触 '/' 聚焦搜索。
      const isHelpKey = event.key === '?' || (event.key === '/' && event.shiftKey);
      if (isHelpKey && !modMatches && !event.altKey) {
        event.preventDefault();
        clearPending();
        current.onOpenHelp?.();
        return;
      }

      // 其他带修饰键组合:直接按 combo 仲裁,并终结序列待决态。
      if (modMatches || event.altKey) {
        clearPending();
        runArbitrated(comboFromEvent(event));
        return;
      }

      // —— 序列键:G → I/B/M/A(窗口内,§4.5)
      const pending = pendingRef.current;
      if (pending !== null) {
        clearPending();
        if (now() - pending.at <= sequenceWindowMs && SEQUENCE_SECOND_KEYS.has(lower)) {
          event.preventDefault();
          runArbitrated(`${SEQUENCE_PREFIX} ${lower}`);
          return;
        }
        // 超时或非序列第二键:落入下方,继续尝试匹配本键自身。
      }

      if (lower === SEQUENCE_PREFIX) {
        pendingRef.current = { at: now() };
        setSequencePending(true);
        const windowMs = sequenceWindowMs;
        window.setTimeout(() => {
          // 窗口到期且仍处于同一待决态 → 收起提示(缓冲在下次按键时失效)。
          const still = pendingRef.current;
          if (still !== null && now() - still.at >= windowMs) {
            clearPending();
          }
        }, windowMs + 20);
        return;
      }

      // —— 第 3/4 层:页面上下文组 > 全局组(同一仲裁,§4.3.1)。
      const combo = comboFromEvent(event);
      if (combo === '/') {
        // '/' 聚焦搜索须 preventDefault,回避浏览器内置快速查找冲突(§4.5)。
        event.preventDefault();
      }
      runArbitrated(combo);
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const showHint = sequencePending && props.sequencePendingLabel !== undefined;

  return (
    <>
      {props.children}
      {showHint ? (
        <div className="mesh-sequence-hint" data-testid="sequence-hint" role="status">
          {props.sequencePendingLabel}
        </div>
      ) : null}
    </>
  );
}
