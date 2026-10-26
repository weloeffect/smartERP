const {test} = require("node:test");
const assert = require("node:assert/strict");
const {renderAnswer} = require("../static/answer-format.js");

// A minimal DOM records the elements and attributes the renderer creates.
class Element {
  constructor(tag, text = "") { this.tag = tag; this.text = text; this.children = []; this.attrs = {}; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attrs[name] = value; }
}
const doc = {createElement: tag => new Element(tag), createTextNode: text => new Element("#text", text)};
const find = (root, tag) => [root, ...root.children.flatMap(child => find(child, tag))].filter(el => el.tag === tag);

test("renders existing bold invoice lists without raw Markdown markers", () => {
  const result = renderAnswer("**Overdue invoices (2 total):**\n\n1. INV-2018 · Terra · €522.25\n2. INV-2026 · Paper · €166.25\n\n**Total outstanding: €688.50**", doc);
  assert.equal(find(result, "strong").length, 2);
  assert.equal(find(result, "ol").length, 1);
  assert.equal(find(result, "li").length, 2);
  assert.ok(!result.textContent.includes("**"));
  assert.equal(find(result, "p").at(-1).className, "answer-total");
  assert.ok(result.textContent.includes("€688.50"));
});

test("renders accessible record tables, preserving IDs, dates, amounts, and alignment", () => {
  const result = renderAnswer("## Overdue invoices\n\n| Invoice | Customer | Amount | Due date |\n| --- | --- | ---: | --- |\n| INV-2018 | Terra Engineering | €522.25 | 2026-10-24 |\n| INV-2026 | Paper & Co. | €166.25 | 2026-10-08 |", doc);
  assert.equal(find(result, "h4")[0].textContent, "Overdue invoices");
  assert.equal(find(result, "table").length, 1);
  assert.equal(find(result, "th").length, 4);
  assert.equal(find(result, "td").length, 8);
  assert.equal(find(result, "th")[0].attrs.scope, "col");
  assert.equal(find(result, "td")[2].className, "answer-number");
  assert.equal(find(result, "td")[3].textContent, "2026-10-24");
  assert.equal(find(result, "td")[4].textContent, "INV-2026");
  assert.equal(find(result, "div")[1].attrs.tabindex, "0");
});

test("never creates HTML or active elements from untrusted answer text", () => {
  const input = '<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n**<iframe src="bad">**';
  const result = renderAnswer(input, doc);
  for (const tag of ["script", "img", "iframe"]) assert.equal(find(result, tag).length, 0);
  assert.ok(result.textContent.includes('<img src=x onerror=alert(1)>'));
});

test("creates safe web links and leaves executable URLs as inert text", () => {
  const result = renderAnswer("[Docs](https://example.com/docs) [bad](javascript:alert(1)) [data](data:text/html,bad)", doc);
  const links = find(result, "a");
  assert.equal(links.length, 1);
  assert.equal(links[0].attrs.href, "https://example.com/docs");
  assert.equal(links[0].attrs.rel, "noopener noreferrer");
  assert.ok(result.textContent.includes("javascript:alert(1)"));
});

test("keeps fenced code literal and handles incomplete formatting", () => {
  const result = renderAnswer("```html\n<b>€45.00</b>\n**literal**\n```\n\nUnclosed **bold\n\n`INV-2018`", doc);
  assert.equal(find(result, "pre")[0].textContent, "<b>€45.00</b>\n**literal**");
  assert.equal(find(result, "b").length, 0);
  assert.equal(find(result, "code").at(-1).textContent, "INV-2018");
  assert.ok(result.textContent.includes("Unclosed **bold"));
});

test("supports tables without edge pipes, escaped pipes, and missing cells", () => {
  const result = renderAnswer("Customer | Amount\n--- | ---:\nPaper \\| Co. | €81.00\nAcme |", doc);
  assert.equal(find(result, "td")[0].textContent, "Paper | Co.");
  assert.equal(find(result, "td")[3].textContent, "");
});

test("renders note labels as a triangle while preserving accessible note content", () => {
  for (const prefix of ["Note:", "**Note:**", "**Note**:", "△"]) {
    const result = renderAnswer(`${prefix} Meridian Labs and Urban Collective are tied at **€1,517.50** each.`, doc);
    const note = find(result, "p")[0];
    assert.equal(note.className, "answer-note");
    assert.equal(note.attrs.role, "note");
    assert.equal(note.children[0].textContent, "△");
    assert.equal(note.children[0].attrs["aria-hidden"], "true");
    assert.equal(note.children[1].className, "sr-only");
    assert.equal(note.children[2].textContent, "Meridian Labs and Urban Collective are tied at €1,517.50 each.");
    assert.equal(find(note, "strong")[0].textContent, "€1,517.50");
  }
});

test("renders blockquote notes with one triangle and leaves ordinary prose intact", () => {
  const result = renderAnswer("> Note: Two customers are tied.\n\nCustomer notes: paid on time.", doc);
  assert.equal(find(result, "blockquote")[0].attrs.role, "note");
  assert.equal(find(result, "p")[0].textContent, "Customer notes: paid on time.");
});
