"use strict";

// Render a small Markdown subset with DOM nodes. Model output is always text,
// never HTML; only explicit HTTP(S) links can become clickable links.
function renderAnswer(text, doc = document) {
  const element = (tag, value) => {
    const el = doc.createElement(tag);
    if (value !== undefined) el.textContent = value;
    return el;
  };
  function inline(parent, value, depth = 0) {
    if (depth > 6) { parent.append(doc.createTextNode(value)); return; }
    const pattern = /(`[^`\n]+`|\*\*[^\n]+?\*\*|__[^\n]+?__|\*[^*\n]+\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
    let offset = 0;
    for (const match of value.matchAll(pattern)) {
      parent.append(doc.createTextNode(value.slice(offset, match.index)));
      const token = match[0];
      let child;
      if (token.startsWith("`")) child = element("code", token.slice(1, -1));
      else if (token.startsWith("[")) {
        const link = token.match(/^\[([^\]]+)\]\((.+)\)$/);
        child = element("a", link[1]);
        child.setAttribute("href", link[2]);
        child.setAttribute("target", "_blank");
        child.setAttribute("rel", "noopener noreferrer");
      } else {
        const strong = token.startsWith("**") || token.startsWith("__");
        child = element(strong ? "strong" : "em");
        inline(child, token.slice(strong ? 2 : 1, strong ? -2 : -1), depth + 1);
      }
      parent.append(child);
      offset = match.index + token.length;
    }
    parent.append(doc.createTextNode(value.slice(offset)));
  }
  const notePrefix = /^(?:\*\*Note:\*\*|\*\*Note\*\*:|__Note:__|__Note__:|Note:|[△⚠])\s*/i;
  function prose(tag, value) {
    const el = element(tag);
    if (notePrefix.test(value.trim())) {
      el.className = "answer-note";
      el.setAttribute("role", "note");
      const icon = element("span", "△");
      icon.className = "answer-note-icon";
      icon.setAttribute("aria-hidden", "true");
      const label = element("span", "Note: ");
      label.className = "sr-only";
      const content = element("span");
      inline(content, value.trim().replace(notePrefix, ""));
      el.append(icon, label, content);
    } else inline(el, value);
    return el;
  }
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  const root = element("div");
  root.className = "message-text answer-content";
  const cells = line => line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, "|"));
  const divider = line => line && line.includes("|") && cells(line).every(cell => /^:?-{3,}:?$/.test(cell));
  const listItem = line => line.match(/^\s*(?:(\d+)[.)]|([-+*]))\s+(.+)$/);
  const tableAt = i => lines[i]?.includes("|") && divider(lines[i + 1]);
  const blockAt = i => /^\s*(?:#{1,6}\s|```|>\s?|(?:---+|\*\*\*+)\s*$)/.test(lines[i]) || listItem(lines[i]) || tableAt(i);
  let i = 0;
  while (i < lines.length) {
    if (!lines[i].trim()) { i++; continue; }
    if (/^\s*```/.test(lines[i])) {
      const content = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) content.push(lines[i++]);
      const pre = element("pre");
      pre.append(element("code", content.join("\n")));
      root.append(pre);
      if (i < lines.length) i++;
      continue;
    }
    if (tableAt(i)) {
      const headers = cells(lines[i]);
      const alignment = cells(lines[i + 1]);
      const wrapper = element("div");
      wrapper.className = "answer-table-wrap";
      wrapper.setAttribute("role", "region");
      wrapper.setAttribute("aria-label", "Answer data");
      wrapper.setAttribute("tabindex", "0");
      const table = element("table"), head = element("thead"), row = element("tr"), body = element("tbody");
      function cell(tag, value, column) {
        const el = element(tag);
        if (tag === "th") el.setAttribute("scope", "col");
        if (/^:.*:$/.test(alignment[column])) el.className = "answer-center";
        else if (/:$/.test(alignment[column])) el.className = "answer-number";
        else if (tag === "td" && /^(?:[€£$]\s*[\d,.]+|[\d,.]+\s*(?:EUR|€|%))$/.test(value)) el.className = "answer-number";
        inline(el, value);
        return el;
      }
      headers.forEach((value, column) => row.append(cell("th", value, column)));
      head.append(row);
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        const values = cells(lines[i++]), tr = element("tr");
        headers.forEach((_, column) => tr.append(cell("td", values[column] || "", column)));
        body.append(tr);
      }
      table.append(head, body); wrapper.append(table); root.append(wrapper);
      continue;
    }
    const heading = lines[i].match(/^\s*(#{1,6})\s+(.+?)\s*#*$/);
    if (heading) {
      const el = element(`h${Math.min(heading[1].length + 2, 6)}`);
      inline(el, heading[2]); root.append(el); i++; continue;
    }
    const item = listItem(lines[i]);
    if (item) {
      const ordered = Boolean(item[1]), list = element(ordered ? "ol" : "ul");
      if (ordered && Number(item[1]) !== 1) list.setAttribute("start", item[1]);
      while (i < lines.length) {
        const next = listItem(lines[i]);
        if (!next || Boolean(next[1]) !== ordered) break;
        const li = element("li"); inline(li, next[3]); list.append(li); i++;
      }
      root.append(list); continue;
    }
    if (/^\s*>/.test(lines[i])) {
      const content = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) content.push(lines[i++].replace(/^\s*>\s?/, ""));
      root.append(prose("blockquote", content.join("\n"))); continue;
    }
    if (/^\s*(?:---+|\*\*\*+)\s*$/.test(lines[i])) { root.append(element("hr")); i++; continue; }
    const paragraph = [lines[i++]];
    while (i < lines.length && lines[i].trim() && !blockAt(i)) paragraph.push(lines[i++]);
    const value = paragraph.join("\n"), p = prose("p", value);
    if (/^(?:\*\*|__)?(?:total\b|grand total\b)/i.test(value.trim()) && /[€£$]|\bEUR\b/.test(value)) p.className = "answer-total";
    root.append(p);
  }
  return root;
}

if (typeof module !== "undefined" && module.exports) module.exports = {renderAnswer};
