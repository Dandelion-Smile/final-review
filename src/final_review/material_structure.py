"""Conservative, model-free PPTX inspection. Preview text is never a search chunk."""

import re
from hashlib import sha256
from io import BytesIO

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

STRATEGY_VERSION = "hybrid-slides-v1"
# Individually inspected backgrounds/logos from the course's recurring template.
# Only inherited artwork can match; slide-owned screenshots always require vision.
# Adding/changing these rules requires a strategy-version bump after this release.
DECORATIVE_TEMPLATE_IMAGES = frozenset(
    {
        "0d7e4cbcd0e7eedddc2553759b65224c58c19599e011491b0daf453a114587b3",
        "2c45db9fed4a12657f46007986047bbd772a896c4191dc14eedad633c10e4693",
        "fc8f6abe67cd9fa011b9a443264539fab99935b6516f90590779612e80a71693",
        "2050d868bfa46ad6146dc91130a4d59dd862c2579b935ad48c31f3c8c67acc09",
        "24729ecf1560d3ed855134a57a865bba299e23c2bcddba03e85f21b4384b41d4",
    }
)
_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")


def _bullet_properties(paragraph, shape, slide, presentation):
    """Resolve inherited list properties, respecting explicit buNone overrides."""
    level = paragraph.level
    candidates = [paragraph._p.pPr]

    def frame_styles(owner):
        if owner is None or not owner.has_text_frame:
            return
        styles = owner.text_frame._txBody.find(qn("a:lstStyle"))
        if styles is not None:
            candidates.extend(
                [styles.find(qn(f"a:lvl{level + 1}pPr")), styles.find(qn("a:defPPr"))]
            )
        if owner is not shape:
            candidates.extend(p._p.pPr for p in owner.text_frame.paragraphs if p.level == level)

    frame_styles(shape)
    if shape.is_placeholder:
        idx = shape.placeholder_format.idx
        frame_styles(
            next(
                (p for p in slide.slide_layout.placeholders if p.placeholder_format.idx == idx),
                None,
            )
        )
        frame_styles(
            next(
                (
                    p
                    for p in slide.slide_layout.slide_master.placeholders
                    if p.placeholder_format.type == shape.placeholder_format.type
                ),
                None,
            )
        )
        style_name = "titleStyle" if "TITLE" in str(shape.placeholder_format.type) else "bodyStyle"
    else:
        style_name = "otherStyle"
    master = slide.slide_layout.slide_master._element.find(qn("p:txStyles"))
    if master is not None:
        style = master.find(qn("p:" + style_name))
        if style is not None:
            candidates.extend([style.find(qn(f"a:lvl{level + 1}pPr")), style.find(qn("a:defPPr"))])
    default = presentation._element.find(qn("p:defaultTextStyle"))
    if default is not None:
        candidates.append(default.find(qn(f"a:lvl{level + 1}pPr")))
    for candidate in candidates:
        if candidate is None:
            continue
        for node in candidate:
            kind = node.tag.rsplit("}", 1)[-1]
            if kind == "buNone":
                return None
            if kind == "buChar":
                return {"kind": "bullet", "marker": node.get("char", "•")}
            if kind == "buAutoNum":
                return {
                    "kind": "number",
                    "format": node.get("type", "arabicPeriod"),
                    "start": int(node.get("startAt", "1")),
                }
    return None


def inspect_presentation(content: bytes) -> list[dict]:
    return presentation_pages(Presentation(BytesIO(content)))


def presentation_pages(presentation) -> list[dict]:
    pages = []
    for number, slide in enumerate(presentation.slides, 1):
        blocks, reasons = [], []
        needs_ocr = False
        decorative_images = []

        def reason(message):
            if message not in reasons:
                reasons.append(message)

        def walk(shapes, transform=(1, 1, 0, 0), prefix=""):
            nonlocal needs_ocr
            sx, sy, tx, ty = transform
            for shape in shapes:
                path = f"{prefix}/{shape.shape_id}"
                try:
                    x, y = tx + shape.left * sx, ty + shape.top * sy
                    w, h = shape.width * sx, shape.height * sy
                    if (
                        x < 0
                        or y < 0
                        or x + w > presentation.slide_width * 1.01
                        or y + h > presentation.slide_height * 1.01
                    ):
                        reason("对象超出页面边界")
                    if shape.rotation:
                        reason("存在旋转对象")
                    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                        xf = shape._element.grpSpPr.xfrm
                        if xf is None or not xf.chExt.cx or not xf.chExt.cy:
                            reason("组合对象坐标无法确定")
                            nested = transform
                        else:
                            gx, gy = w / xf.chExt.cx, h / xf.chExt.cy
                            nested = (gx, gy, x - xf.chOff.x * gx, y - xf.chOff.y * gy)
                        walk(shape.shapes, nested, path)
                        continue
                    tags = {node.tag.rsplit("}", 1)[-1] for node in shape._element.iter()}
                    text_issues = []
                    if shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
                        text_issues.append("图形内文字，关系不确定")
                    if shape.is_placeholder and any(
                        kind in str(shape.placeholder_format.type)
                        for kind in ("SLIDE_NUMBER", "FOOTER", "DATE")
                    ):
                        text_issues.append("页码或装饰占位文字")
                    if tags & {
                        "oMath",
                        "oMathPara",
                        "math",
                        "m",
                        "relIds",
                        "dataModel",
                        "dgm",
                        "oleObj",
                        "AlternateContent",
                        "contentPart",
                    }:
                        text_issues.append("复杂结构中的原生片段")
                    if any(
                        node.get("baseline", "0") not in {"0", "0.0"}
                        or node.get("typeface", "").lower() in {"cambria math", "symbol"}
                        or node.get("hidden") in {"1", "true"}
                        for node in shape._element.iter()
                    ):
                        text_issues.append("公式、上下标或隐藏文字")
                    if shape.rotation:
                        text_issues.append("旋转文字")
                    if shape.shape_type == MSO_SHAPE_TYPE.FREEFORM:
                        reason("包含自由图形")
                    if any(node.get("hidden") in {"1", "true"} for node in shape._element.iter()):
                        reason("包含隐藏对象")
                    if tags & {"oMath", "oMathPara", "math", "m"}:
                        reason("包含公式")
                    if any(
                        node.get("baseline", "0") not in {"0", "0.0"}
                        or node.get("typeface", "").lower() in {"cambria math", "symbol"}
                        for node in shape._element.iter()
                    ):
                        reason("包含公式或上下标排版")
                    if tags & {"relIds", "dataModel", "dgm"}:
                        reason("包含 SmartArt")
                    if "oleObj" in tags:
                        reason("包含嵌入对象")
                    if shape.has_chart:
                        reason("包含图表")
                    if shape.has_table:
                        reason("包含表格")
                        text = "\n".join(
                            "\t".join(cell.text for cell in row.cells) for row in shape.table.rows
                        )
                        blocks.append(
                            dict(
                                shape_path=path,
                                kind="table",
                                text_issues=["表格片段"],
                                text=_safe(text),
                                paragraphs=[],
                                x=x,
                                y=y,
                                width=w,
                                height=h,
                            )
                        )
                    if (
                        "pic" in tags
                        or "blip" in tags
                        or shape.shape_type
                        in {
                            MSO_SHAPE_TYPE.PICTURE,
                            MSO_SHAPE_TYPE.LINKED_PICTURE,
                        }
                    ):
                        reason("存在图片或截图")
                        needs_ocr = True
                    if "cxnSp" in tags:
                        reason("包含连接线或箭头")
                    if shape.has_text_frame:
                        paragraphs = []
                        counters = {}
                        for paragraph in shape.text_frame.paragraphs:
                            raw = paragraph.text.replace("\v", "\n")
                            if _BAD.search(raw):
                                reason("原生文字包含异常字符")
                                text_issues.append("异常字符")
                                needs_ocr = True
                            if not raw.strip():
                                continue
                            properties = _bullet_properties(paragraph, shape, slide, presentation)
                            marker = ""
                            if properties:
                                if properties["kind"] == "number":
                                    value = (
                                        counters.get(paragraph.level, properties["start"] - 1) + 1
                                    )
                                    counters[paragraph.level] = value
                                    marker = f"{value}."
                                else:
                                    marker = properties["marker"]
                            paragraphs.append(
                                dict(
                                    text=_safe(raw),
                                    level=paragraph.level,
                                    bullet=bool(properties),
                                    bullet_marker=marker,
                                    list_properties=properties,
                                )
                            )
                        if paragraphs:
                            is_title = bool(
                                slide.shapes.title is not None
                                and shape.shape_id == slide.shapes.title.shape_id
                            )
                            blocks.append(
                                dict(
                                    shape_path=path,
                                    kind="title" if is_title else "text",
                                    text_issues=text_issues,
                                    text="\n".join(p["text"] for p in paragraphs),
                                    paragraphs=paragraphs,
                                    x=x,
                                    y=y,
                                    width=w,
                                    height=h,
                                )
                            )
                        # Empty rectangles/lines can encode a diagram, not just decoration.
                        if shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
                            if not paragraphs or str(shape.auto_shape_type).split(" ")[0] not in {
                                "RECTANGLE",
                                "ROUNDED_RECTANGLE",
                            }:
                                reason("包含图形或流程图")
                    elif not (
                        shape.has_table
                        or shape.has_chart
                        or shape.shape_type
                        in {
                            MSO_SHAPE_TYPE.PICTURE,
                            MSO_SHAPE_TYPE.LINKED_PICTURE,
                            MSO_SHAPE_TYPE.LINE,
                        }
                    ):
                        reason("对象类型无法确定")
                except Exception:
                    reason("页面对象提取异常")
                    needs_ocr = True

        walk(slide.shapes)
        # Non-placeholder artwork inherited from the layout/master is also visible.
        for inherited in (slide.slide_layout.shapes, slide.slide_layout.slide_master.shapes):
            for shape in inherited:
                if (
                    shape.shape_type == MSO_SHAPE_TYPE.PICTURE
                    and sha256(shape.image.blob).hexdigest() in DECORATIVE_TEMPLATE_IMAGES
                ):
                    decorative_images.append(sha256(shape.image.blob).hexdigest())
                    continue
                if not shape.is_placeholder and shape.shape_type in {
                    MSO_SHAPE_TYPE.PICTURE,
                    MSO_SHAPE_TYPE.LINKED_PICTURE,
                    MSO_SHAPE_TYPE.GROUP,
                    MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT,
                }:
                    reason("版式或母版包含视觉内容")
                    needs_ocr = True
        for node in slide._element.iter():
            local = node.tag.rsplit("}", 1)[-1]
            if local in {"AlternateContent", "contentPart"}:
                reason("存在无法确定的兼容对象")
        blocks.sort(key=lambda b: (b["kind"] != "title", b["y"], b["x"]))
        for i, first in enumerate(blocks):
            for second in blocks[i + 1 :]:
                overlap = min(first["y"] + first["height"], second["y"] + second["height"])
                overlap -= max(first["y"], second["y"])
                if overlap > min(first["height"], second["height"]) * 0.25:
                    reason("文字对象重叠或阅读顺序无法确定")
        body = "\n\n".join(
            "\n".join(
                ("  " * p["level"] + p["bullet_marker"] + " " if p["bullet"] else "") + p["text"]
                for p in block["paragraphs"]
            )
            if block["paragraphs"]
            else block["text"]
            for block in blocks
        )
        notes = ""
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                notes = slide.notes_slide.notes_text_frame.text
                if _BAD.search(notes):
                    reason("备注包含异常字符")
                notes = _safe(notes)
        except Exception:
            reason("备注提取异常")
        if not body.strip():
            reason("原生文字为空")
        title = next((b["text"] for b in blocks if b["kind"] == "title"), f"第 {number} 页")
        pages.append(
            dict(
                position_kind="slide",
                position=number,
                title=title,
                native_text=body,
                notes=notes,
                blocks=blocks,
                route="visual" if reasons else "native",
                reasons=reasons or ["原生文字提取完整"],
                needs_ocr=needs_ocr,
                decorative_template_images=decorative_images,
                quality="pending",
                status="pending",
            )
        )
    return pages


def _safe(text):
    return _BAD.sub("\ufffd", text)


def preview_version(source_hash):
    return sha256((source_hash + STRATEGY_VERSION).encode()).hexdigest()


def native_record(page):
    """No paraphrase and no visual-verification claim on the native fast path."""
    text = page["native_text"]
    if page["notes"].strip():
        text += "\n\n教学备注（原文）：\n" + page["notes"]
    return dict(
        kind="knowledge",
        title=page["title"],
        quality="native_checked",
        issues=[],
        verification_method="native_integrity",
        blocks=[
            dict(
                block_id="native",
                title=page["title"],
                text=text,
                quality="native_checked",
                issues=[],
                evidence=[
                    dict(
                        source_id="native",
                        origin="native",
                        quote=page["native_text"],
                    )
                ],
            )
        ],
    )
