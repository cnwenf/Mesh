/**
 * 按键帽:快捷键帮助层的 <kbd> 展示单元。
 */
import type { ReactNode } from 'react';
import './components.css';

export interface KbdProps {
  children: ReactNode;
}

export function Kbd(props: KbdProps): React.JSX.Element {
  return <kbd className="mesh-kbd">{props.children}</kbd>;
}
