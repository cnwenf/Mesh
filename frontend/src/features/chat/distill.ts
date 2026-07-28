/**
 * 沉淀为评论的正文汇编(chat-session.md §4 沉淀)。
 * 把会话消息逐条转为 `**角色**\n\n正文` 的 Markdown,用户/agent 之间以分隔线隔断;
 * 系统消息不入沉淀正文。父级把结果预填进 DistillDialog 供编辑后一次提交。
 */
import type { ChatMessage } from './types';

/** 由会话消息汇编沉淀正文(角色名本地化文案由调用方传入)。 */
export function buildDistillBody(
  messages: readonly ChatMessage[],
  roleUser: string,
  roleAgent: string,
): string {
  return messages
    .filter((message) => message.role === 'user' || message.role === 'agent')
    .map((message) => {
      const role = message.role === 'user' ? roleUser : roleAgent;
      return `**${role}**\n\n${message.content}`;
    })
    .join('\n\n---\n\n');
}
