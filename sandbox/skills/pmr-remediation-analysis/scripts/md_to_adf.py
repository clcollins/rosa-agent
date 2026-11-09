#!/usr/bin/env python3
"""
Convert a subset of Markdown to Atlassian Document Format (ADF) JSON.

Handles the proposal template format:
  - H1/H2/H3 headings
  - Unordered list items (- or *)
  - Fenced code blocks (```)
  - Bold inline text (**...**)
  - Plain paragraphs

Usage:
  python3 md_to_adf.py proposal.md | jq .
  echo "$PROPOSAL_TEXT" | python3 md_to_adf.py

Output is a single JSON object suitable for Jira's 'body' field
(POST .../comment with Content-Type: application/json).
"""
import json
import re
import sys


def parse_inline(text):
    """Split text with **bold** markers into ADF inline nodes."""
    nodes = []
    parts = re.split(r'\*\*(.+?)\*\*', text)
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:
            nodes.append({"type": "text", "text": part,
                           "marks": [{"type": "strong"}]})
        else:
            nodes.append({"type": "text", "text": part})
    return nodes


def paragraph(text):
    return {"type": "paragraph", "content": parse_inline(text)}


def heading(level, text):
    return {"type": "heading", "attrs": {"level": level},
            "content": [{"type": "text", "text": text}]}


def code_block(lang, code):
    attrs = {"language": lang} if lang else {}
    return {"type": "codeBlock", "attrs": attrs,
            "content": [{"type": "text", "text": code}]}


def bullet_list(items):
    return {
        "type": "bulletList",
        "content": [
            {"type": "listItem",
             "content": [{"type": "paragraph",
                          "content": parse_inline(item)}]}
            for item in items
        ]
    }


def convert(md_text):
    lines = md_text.splitlines()
    content = []
    i = 0
    pending_bullets = []

    def flush_bullets():
        if pending_bullets:
            content.append(bullet_list(pending_bullets))
            pending_bullets.clear()

    while i < len(lines):
        line = lines[i]

        # Fenced code block
        fence = re.match(r'^```(\w*)', line)
        if fence:
            lang = fence.group(1)
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code_lines.append(lines[i])
                i += 1
            flush_bullets()
            content.append(code_block(lang, '\n'.join(code_lines)))
            i += 1
            continue

        # Headings
        h = re.match(r'^(#{1,6})\s+(.*)', line)
        if h:
            flush_bullets()
            content.append(heading(len(h.group(1)), h.group(2).strip()))
            i += 1
            continue

        # Bullet items
        b = re.match(r'^[-*]\s+(.*)', line)
        if b:
            pending_bullets.append(b.group(1))
            i += 1
            continue

        # Blank line — flush bullets, skip
        if not line.strip():
            flush_bullets()
            i += 1
            continue

        # Regular paragraph
        flush_bullets()
        content.append(paragraph(line))
        i += 1

    flush_bullets()
    return {"version": 1, "type": "doc", "content": content}


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    doc = {"body": convert(text)}
    print(json.dumps(doc))


if __name__ == '__main__':
    main()
