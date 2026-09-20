// Parses the resume markdown the pipeline produces into structured blocks for preview.
// Mirrors scripts/format_resume_doc.py's classification so the preview matches the exports.
// Pure: returns data, never touches the DOM (the preview builds nodes from this, so
// resume text is only ever inserted via textContent).

const DATE_RE = /\b(19|20)\d{2}\b/;

export function parseResume(md) {
  const blocks = [];
  let prev = null;
  for (const raw of String(md || '').split(/\r?\n/)) {
    const line = raw.trimEnd();
    if (!line.trim()) continue;
    let block;
    if (line.startsWith('# ')) block = { type: 'name', text: line.slice(2).trim() };
    else if (line.startsWith('## ')) block = { type: 'section', text: line.slice(3).trim() };
    else if (line.startsWith('### ')) block = { type: 'role', text: line.slice(4).trim() };
    else if (line.startsWith('- ')) block = { type: 'bullet', text: line.slice(2).trim() };
    else if (prev === 'name') block = { type: 'tagline', text: line.trim() };
    else if (prev === 'role' && DATE_RE.test(line)) block = { type: 'date', text: line.trim() };
    else block = { type: 'text', text: line.trim() };
    blocks.push(block);
    prev = block.type;
  }
  return blocks;
}

// Group consecutive bullets so the preview can render one <ul> per run.
export function groupBullets(blocks) {
  const out = [];
  for (const b of blocks) {
    if (b.type === 'bullet') {
      const last = out[out.length - 1];
      if (last && last.type === 'bullets') last.items.push(b.text);
      else out.push({ type: 'bullets', items: [b.text] });
    } else out.push(b);
  }
  return out;
}
