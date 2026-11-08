/** Presentation only: disclose a pasted file envelope separately from prose.
 * The server validates identity and metadata when preparing/saving an edit.
 * Unknown front matter, Markdown rules and fenced examples stay in the body.
 */
export function splitKnowledgeMarkdown(markdown: string) {
  const metadata: string[] = [];
  let body = markdown;
  while (true) {
    const match = body.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
    if (!match ||
      !/^schema:\s*novel-knowledge\.v[123]\s*$/m.test(match[1]) ||
      !/^id:\s*(?:tag|genre)\.[a-z][a-z0-9_-]+\s*$/m.test(match[1]) ||
      !/^kind:\s*(?:tag|genre)\s*$/m.test(match[1])) break;
    metadata.push(match[1]);
    body = body.slice(match[0].length);
  }
  return { metadata, body };
}
