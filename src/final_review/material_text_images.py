"""Native text with independent original-page illustrations; no visual/OCR path."""

from copy import deepcopy

from .material_notes import semantic_input

AMBIGUOUS = {
    "文字对象重叠或阅读顺序无法确定",
    "包含连接线或箭头",
    "包含图形或流程图",
    "组合对象坐标无法确定",
    "页面对象提取异常",
    "存在无法确定的兼容对象",
    "对象类型无法确定",
    "对象超出页面边界",
    "包含自由图形",
}


def classify_pages(pages):
    pages = deepcopy(pages)
    for page in pages:
        ambiguous = sorted(set(page["reasons"]) & AMBIGUOUS)
        page["needs_page_image"] = page["route"] != "native"
        for block in page["blocks"]:
            issues = list(block.get("text_issues", [])) + ambiguous
            if "\ufffd" in block["text"]:
                issues.append("异常字符")
            # Bare page numbers and placeholder labels cannot create knowledge input.
            if not block["text"].strip() or block["text"].strip().isdigit():
                issues.append("无独立正文")
            if block["kind"] == "table":
                issues.append("表格片段")
            block["searchable"] = not issues
            block["exclusion_reasons"] = sorted(set(issues))
            if set(issues) - {"无独立正文", "页码或装饰占位文字"}:
                page["needs_page_image"] = True
        page["image_status"] = "queued" if page["needs_page_image"] else "not_required"
        page["status"] = "completed"
        page["quality"] = (
            "native_checked" if all(b["searchable"] for b in page["blocks"]) else "review_needed"
        )
        page["notes_searchable"] = bool(page.get("notes", "").strip()) and not (
            "\ufffd" in page.get("notes", "") or "备注提取异常" in page["reasons"]
        )
    return pages


def text_sections(body):
    return [
        dict(
            position_kind="slide",
            position=unit["page"],
            text=unit["text"],
            block_id=unit["block_id"],
            content_origin=unit["origin"],
        )
        for unit in semantic_input(body, searchable_only=True)
    ]
