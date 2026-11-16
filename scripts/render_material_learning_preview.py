"""Render a generated note with the production reading styles for visual QA."""

import argparse
import html
import json
from pathlib import Path


def render(node):
    kind = node.get("type")
    if kind == "text":
        text = html.escape(node["text"])
        for mark in node.get("marks", []):
            tag = {"bold": "strong", "italic": "em", "code": "code", "strike": "s"}.get(
                mark["type"]
            )
            if tag:
                text = f"<{tag}>{text}</{tag}>"
        return text
    content = "".join(map(render, node.get("content", [])))
    if kind == "doc":
        return content
    if kind == "sourceBlock":
        key = html.escape(node["attrs"]["block_id"], quote=True)
        return (
            f'<section class="material-note-source" id="{key}"><div>'
            f'<div class="material-note-source-content">{content}</div></div></section>'
        )
    if kind == "heading":
        level = min(3, max(1, node["attrs"].get("level", 2)))
        return f"<h{level}>{content}</h{level}>"
    if kind == "codeBlock":
        return f"<pre><code>{content}</code></pre>"
    tags = {
        "paragraph": "p",
        "bulletList": "ul",
        "orderedList": "ol",
        "listItem": "li",
        "table": "table",
        "tableRow": "tr",
        "tableHeader": "th",
        "tableCell": "td",
    }
    tag = tags.get(kind)
    if tag:
        attrs = ' class="material-note-table"' if kind == "table" else ""
        return f"<{tag}{attrs}>{content}</{tag}>"
    return content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("note", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    css = (root / "frontend/src/material-note-reader.css").read_text(encoding="utf-8")
    body = json.loads(args.note.read_text(encoding="utf-8"))
    from final_review.material_notes import node_text, validate_body

    validate_body(body, body)
    outline = []
    for node in body["content"]:
        first = (node.get("content") or [{}])[0]
        if first.get("type") == "heading" and first["attrs"]["level"] in {2, 3}:
            outline.append(
                '<a href="#'
                + html.escape(node["attrs"]["block_id"], quote=True)
                + '">'
                + html.escape(node_text(first))
                + "</a>"
            )
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
    document += '<meta name="viewport" content="width=device-width,initial-scale=1">'
    document += (
        "<title>资料学习笔记 · 效果预览</title><style>"
        + css
        + """
body{margin:0;background:#f5f7f9;font-family:'Microsoft YaHei UI',sans-serif}
main{max-width:1220px;margin:24px auto;padding:0 24px}.preview-label{font-size:12px;color:#657b8a}
.material-note-outline a{display:block;color:#193650;font-size:13px;line-height:1.8;
text-decoration:none;padding:7px 0}
.material-note-outline a:hover{text-decoration:underline}.material-note-document{background:white}
@media(max-width:700px){main{padding:0 12px}.material-note-outline{max-height:150px;overflow:auto}}
"""
        + '</style><main class="material-note-reader">'
        + '<p class="preview-label">资料学习笔记 · 真实课件生成效果预览</p>'
    )
    document += (
        '<div class="material-note-layout"><aside class="material-note-outline"><h2>目录</h2>'
    )
    document += (
        "".join(outline)
        + '</aside><article class="material-note-document"><div class="material-note-prose">'
    )
    document += (
        render(body)
        + '</div><footer class="material-note-end">根据资料整理，含少量辅助解释。</footer>'
        + "</article></div></main></html>"
    )
    args.output.write_text(document, encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
