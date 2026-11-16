"""Versioned source documents. Only server-owned provenance becomes index input."""

import json
import re
from copy import deepcopy
from hashlib import sha256

STRATEGY = "text-images-v1"
MAX_BODY_BYTES = 2_000_000
BLOCK_TYPES = {"sourceBlock", "teachingNote", "nativeFragment"}
FORMATS = {
    "heading",
    "paragraph",
    "bulletList",
    "orderedList",
    "listItem",
    "codeBlock",
    "table",
    "tableRow",
    "tableHeader",
    "tableCell",
}
MARKS = {"bold", "italic", "strike", "code"}
IDENTIFIER = re.compile(r"^[\w.-]{1,100}$")


class NoteConflict(ValueError):
    def __init__(self, message, latest_revision_id=None):
        super().__init__(message)
        self.latest_revision_id = latest_revision_id


def digest(value):
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def node_text(node):
    if node["type"] == "text":
        return node["text"]
    if node["type"] == "hardBreak":
        return "\n"
    children = node.get("content", [])
    separator = (
        ""
        if node["type"] in {"paragraph", "heading", "codeBlock"}
        else "\t"
        if node["type"] == "tableRow"
        else "\n"
    )
    return separator.join(node_text(child) for child in children)


def semantic_input(body, *, searchable_only=False):
    return [
        dict(
            block_id=n["attrs"]["block_id"],
            page=n["attrs"].get("page"),
            origin=n["attrs"]["content_origin"],
            searchable=n["attrs"]["searchable"],
            text=node_text(n),
        )
        for n in body["content"]
        if n["type"] in BLOCK_TYPES
        and (not searchable_only or n["attrs"]["searchable"])
        and node_text(n).strip()
    ]


def input_hash(body):
    return digest(semantic_input(body))


def initial_body(pages, source_hash):
    nodes = []
    for page in pages:
        number = page["position"]
        for block in page["blocks"]:
            if not block["text"].strip():
                continue
            block_id = "native-" + digest([source_hash, number, block["shape_path"]])[:32]
            attrs = dict(
                block_id=block_id,
                page=number,
                content_origin="native",
                searchable=block["searchable"],
                exclusion_reasons=block.get("exclusion_reasons", []),
            )
            paragraphs = block.get("paragraphs") or [{"text": block["text"]}]
            children = []
            for p in paragraphs:
                text = p["text"]
                if p.get("bullet"):
                    text = "  " * p.get("level", 0) + p.get("bullet_marker", "•") + " " + text
                children.append(
                    dict(
                        type="heading" if block["kind"] == "title" else "paragraph",
                        content=[dict(type="text", text=text)],
                    )
                )
            nodes.append(
                dict(
                    type="sourceBlock" if block["searchable"] else "nativeFragment",
                    attrs=attrs,
                    content=children,
                )
            )
        if page.get("notes", "").strip():
            nodes.append(
                dict(
                    type="teachingNote",
                    attrs=dict(
                        block_id="notes-" + digest([source_hash, number])[:32],
                        page=number,
                        content_origin="teaching_note",
                        searchable=page["notes_searchable"],
                        exclusion_reasons=[] if page["notes_searchable"] else ["备注文字不可靠"],
                    ),
                    content=[
                        dict(type="paragraph", content=[dict(type="text", text=page["notes"])])
                    ],
                )
            )
        if page["needs_page_image"]:
            nodes.append(
                dict(
                    type="pageImage",
                    attrs=dict(block_id=f"image-{number}", page=number, hidden=False),
                )
            )
    return dict(type="doc", content=nodes)


def validate_body(body, previous):
    """Ignore claimed provenance; bind each block to its previous server version."""
    if len(json.dumps(body, ensure_ascii=False).encode()) > MAX_BODY_BYTES:
        raise ValueError("资料笔记超过大小限制")
    if not isinstance(body, dict) or set(body) != {"type", "content"} or body["type"] != "doc":
        raise ValueError("资料笔记根节点无效")
    old = {n["attrs"]["block_id"]: n for n in previous["content"]}
    result, seen = deepcopy(body), set()
    count = 0

    def check(node, depth=0):
        nonlocal count
        count += 1
        if count > 20000 or depth > 12 or not isinstance(node, dict):
            raise ValueError("资料笔记节点过多或层级无效")
        kind = node.get("type")
        if not isinstance(kind, str):
            raise ValueError("正文节点类型无效")
        if kind == "text":
            if set(node) - {"type", "text", "marks"} or not isinstance(node.get("text"), str):
                raise ValueError("文字节点无效")
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", node["text"]):
                raise ValueError("文字包含异常控制字符")
            marks = node.get("marks", [])
            if not isinstance(marks, list):
                raise ValueError("文字格式无效")
            for mark in marks:
                if (
                    not isinstance(mark, dict)
                    or set(mark) != {"type"}
                    or not isinstance(mark["type"], str)
                    or mark["type"] not in MARKS
                ):
                    raise ValueError("不支持的文字格式")
            return
        if kind == "hardBreak" and set(node) == {"type"}:
            return
        if kind not in FORMATS or set(node) - {"type", "content", "attrs"}:
            raise ValueError("不支持的正文节点")
        allowed = {"level"} if kind == "heading" else {"start"} if kind == "orderedList" else set()
        attrs = node.get("attrs", {})
        if not isinstance(attrs, dict) or set(attrs) - allowed:
            raise ValueError("正文节点属性无效")
        for key, value in attrs.items():
            if type(value) is not int or not 1 <= value <= (6 if key == "level" else 10000):
                raise ValueError("正文格式属性无效")
        content = node.get("content", [])
        if not isinstance(content, list):
            raise ValueError("正文内容无效")
        if kind in {"table", "tableRow", "tableCell", "tableHeader"}:
            allowed_children = {
                "table": {"tableRow"},
                "tableRow": {"tableCell", "tableHeader"},
                "tableCell": {"paragraph"},
                "tableHeader": {"paragraph"},
            }[kind]
            if not content or any(
                c.get("type") not in allowed_children for c in content if isinstance(c, dict)
            ):
                raise ValueError("表格结构无效")
            if kind == "table":
                widths = [len(row.get("content", [])) for row in content if isinstance(row, dict)]
                if not widths or len(set(widths)) != 1 or not 1 <= widths[0] <= 8:
                    raise ValueError("表格列数无效或行宽不一致")
                if len(content) > 61:
                    raise ValueError("表格行数过多")
        for child in content:
            check(child, depth + 1)

    if not isinstance(result["content"], list):
        raise ValueError("资料笔记内容无效")
    for node in result["content"]:
        if not isinstance(node, dict) or set(node) - {"type", "attrs", "content"}:
            raise ValueError("资料笔记节点无效")
        kind, attrs = node.get("type"), node.get("attrs", {})
        if not isinstance(kind, str):
            raise ValueError("资料节点类型无效")
        if not isinstance(attrs, dict):
            raise ValueError("资料来源属性无效")
        block_id = attrs.get("block_id", "")
        if not isinstance(block_id, str) or not IDENTIFIER.fullmatch(block_id) or block_id in seen:
            raise ValueError("资料块 ID 无效或重复")
        seen.add(block_id)
        prior = old.get(block_id)
        if kind == "pageImage":
            if (
                prior is None
                or prior["type"] != "pageImage"
                or "content" in node
                or set(attrs) - {"block_id", "page", "hidden"}
                or attrs.get("page") != prior["attrs"]["page"]
                or type(attrs.get("hidden", False)) is not bool
            ):
                raise ValueError("原页图片不属于当前笔记")
            node["attrs"] = {**prior["attrs"], "hidden": attrs.get("hidden", False)}
            continue
        if kind not in BLOCK_TYPES or set(attrs) - {
            "block_id",
            "page",
            "content_origin",
            "searchable",
            "exclusion_reasons",
            "source_refs",
            "content_role",
        }:
            raise ValueError("资料来源节点无效")
        refs = attrs.get("source_refs", [])
        if not isinstance(refs, list) or any(
            not isinstance(ref, str) or not ref or len(ref) > 100 for ref in refs
        ):
            raise ValueError("来源引用无效")
        if attrs.get("content_role", "source") not in {"source", "explanation", "summary"}:
            raise ValueError("内容类别无效")
        if not isinstance(node.get("content"), list):
            raise ValueError("资料来源内容无效")
        for child in node["content"]:
            check(child)
        if prior and prior["type"] == "pageImage":
            raise ValueError("不能将图片节点改为正文")
        if prior:
            node["attrs"] = deepcopy(prior["attrs"])
            node["type"] = prior["type"]
            if node_text(node) != node_text(prior):
                node["attrs"]["content_origin"] = (
                    "user_added"
                    if prior["attrs"]["content_origin"] == "user_added"
                    else "user_edit"
                )
        else:
            if block_id.startswith(("native-", "notes-", "image-")):
                raise ValueError("新增资料块不能使用原文 ID")
            node["type"] = "sourceBlock"
            node["attrs"] = dict(
                block_id=block_id,
                page=None,
                content_origin="user_added",
                searchable=True,
                exclusion_reasons=[],
            )
    return result
