/** Titles that look like leftover smoke / schema / error diagnostics. */
const DIAGNOSTIC_TITLE = /(?:冒烟|schema\s*测|DAG-|Error:|自动化schema|工具调用探针|自动化最终测|自动化正确schema)/i;

export function isDiagnosticConversationTitle(title: string | null | undefined): boolean {
  return DIAGNOSTIC_TITLE.test(String(title || '').trim());
}

export function partitionConversations<T extends { title: string }>(
  conversations: T[],
): { visible: T[]; diagnostic: T[] } {
  const visible: T[] = [];
  const diagnostic: T[] = [];
  for (const conversation of conversations) {
    if (isDiagnosticConversationTitle(conversation.title)) diagnostic.push(conversation);
    else visible.push(conversation);
  }
  return { visible, diagnostic };
}
