export function isSecureAvatarUrl(value: string): boolean {
  if (!/^https:\/\/[^/]/i.test(value) || value.includes('\\') || /\s/.test(value)) return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' && parsed.hostname !== '';
  } catch {
    return false;
  }
}
