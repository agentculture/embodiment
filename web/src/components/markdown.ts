// A small, pure Markdown parser for assistant answers. It produces data only (no HTML,
// no URLs made clickable); the renderer turns every node into a React element with
// text children, so model text can never become markup.

export type Inline =
  | { k: "text"; text: string }
  | { k: "br" }
  | { k: "code"; text: string }
  | { k: "strong"; c: Inline[] }
  | { k: "em"; c: Inline[] };

export interface ListItem {
  inline: Inline[];
  children?: ListBlock;
}
export interface ListBlock {
  type: "list";
  ordered: boolean;
  items: ListItem[];
}
export type Block =
  | { type: "p"; inline: Inline[] }
  | { type: "h"; level: 1 | 2 | 3; inline: Inline[] }
  | { type: "code"; text: string }
  | ListBlock;

const MAX_DEPTH = 4;

function pushText(out: Inline[], text: string): void {
  if (!text) return;
  const last = out[out.length - 1];
  if (last && last.k === "text") last.text += text;
  else out.push({ k: "text", text });
}

/** Inline spans: `code`, **bold**, *italic*, _italic_; [text](url) becomes "text (url)". */
export function parseInline(src: string, depth = 0): Inline[] {
  const out: Inline[] = [];
  let i = 0;
  while (i < src.length) {
    const rest = src.slice(i);
    const ch = src[i];
    let m: RegExpExecArray | null;
    if (ch === "`" && (m = /^`([^`\n]+)`/.exec(rest))) {
      out.push({ k: "code", text: m[1] });
      i += m[0].length;
    } else if (ch === "[" && (m = /^\[([^\]\n]+)\]\(([^)\s]*)\)/.exec(rest))) {
      pushText(out, m[2] ? `${m[1]} (${m[2]})` : m[1]);
      i += m[0].length;
    } else if (depth < MAX_DEPTH && rest.startsWith("**") && (m = /^\*\*(?=\S)([\s\S]*?\S)\*\*/.exec(rest))) {
      out.push({ k: "strong", c: parseInline(m[1], depth + 1) });
      i += m[0].length;
    } else if (depth < MAX_DEPTH && ch === "*" && (m = /^\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/.exec(rest))) {
      out.push({ k: "em", c: parseInline(m[1], depth + 1) });
      i += m[0].length;
    } else if (
      depth < MAX_DEPTH && ch === "_" && !/[\p{L}\p{N}_]/u.test(src[i - 1] ?? "") &&
      (m = /^_(?=[^\s_])([^_\n]*?[^\s_])_(?![\p{L}\p{N}_])/u.exec(rest))
    ) {
      out.push({ k: "em", c: parseInline(m[1], depth + 1) });
      i += m[0].length;
    } else {
      pushText(out, ch);
      i += 1;
    }
  }
  return out;
}

/** Inline content of several source lines, joined by line breaks. */
function inlineLines(lines: string[]): Inline[] {
  const out: Inline[] = [];
  lines.forEach((line, n) => {
    if (n > 0) out.push({ k: "br" });
    out.push(...parseInline(line.trim()));
  });
  return out;
}

const FENCE = /^\s*```/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const ITEM = /^(\s*)([*+-]|\d+[.)])\s+(.*)$/;

function indentOf(s: string): number {
  return s.replace(/\t/g, "    ").length;
}

/** Block structure of a reply: paragraphs, lists (one nested level), headings, code. */
export function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: ListBlock | null = null;
  let child: ListBlock | null = null;
  let baseIndent = 0;
  let lastItem: ListItem | null = null;
  const flushPara = () => {
    if (para.length) blocks.push({ type: "p", inline: inlineLines(para) });
    para = [];
  };
  const endList = () => {
    list = null;
    child = null;
    lastItem = null;
  };
  for (let n = 0; n < lines.length; n += 1) {
    const line = lines[n];
    if (FENCE.test(line)) {
      flushPara();
      endList();
      const body: string[] = [];
      n += 1;
      while (n < lines.length && !FENCE.test(lines[n])) {
        body.push(lines[n]);
        n += 1;
      }
      blocks.push({ type: "code", text: body.join("\n") });
      continue;
    }
    if (line.trim() === "") {
      flushPara();
      // a blank line between items does not end a list; the next non-item line will
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      flushPara();
      endList();
      blocks.push({ type: "h", level: Math.min(h[1].length, 3) as 1 | 2 | 3, inline: parseInline(h[2]) });
      continue;
    }
    const it = ITEM.exec(line);
    if (it) {
      flushPara();
      const indent = indentOf(it[1]);
      const ordered = /\d/.test(it[2]);
      const item: ListItem = { inline: parseInline(it[3].trim()) };
      if (list && lastItem && indent >= baseIndent + 2) {
        if (!child) {
          child = { type: "list", ordered, items: [] };
          lastItem.children = child;
        }
        child.items.push(item);
        lastItem = item;
      } else {
        if (!list || indent < baseIndent || list.ordered !== ordered) {
          list = { type: "list", ordered, items: [] };
          blocks.push(list);
          baseIndent = indent;
        }
        list.items.push(item);
        child = null;
        lastItem = item;
      }
      continue;
    }
    if (list && lastItem && /^\s/.test(line)) {
      // an indented continuation line belongs to the last item
      lastItem.inline.push({ k: "br" }, ...parseInline(line.trim()));
      continue;
    }
    endList();
    para.push(line);
  }
  flushPara();
  return blocks;
}
