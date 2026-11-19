"""Published catalog scopes and verified excerpts shared by notes and quizzes."""

import re

from .course_chat import normalize_chapter
from .knowledge_catalog_store import CatalogRepository
from .source_locators import material_version


def resolve_scope(store, course_id, chapter, point_names, document_ids=None, *, message=""):
    repo = CatalogRepository(store)
    with store.transaction():
        version = repo.current(course_id)["version"]
        if not version:
            raise ValueError("当前学习集还没有已发布的知识目录。")
        points = {p["point_id"]: p for p in repo.snapshot(course_id)["points"]}
    chapters = version["chapters"]
    utterance = normalize_chapter(message)
    mentioned = [c for c in chapters if normalize_chapter(c["title"]) in utterance]
    ordinal = re.search(r"第\d+章", utterance)
    if len(mentioned) == 1:
        canonical = normalize_chapter(mentioned[0]["title"])
        chapter = (ordinal.group() + re.sub(r"^第\d+章", "", canonical)) if ordinal else canonical
        point_names = [
            n
            for n in point_names
            if normalize_chapter(n) not in {canonical, normalize_chapter(chapter)}
        ]
    elif len(mentioned) > 1:
        raise ValueError("一次请选择一个正式章节：" + "、".join(c["title"] for c in mentioned))
    elif ordinal and not chapter:
        chapter = ordinal.group()
    if (
        not chapter
        and len(point_names) == 1
        and any(
            normalize_chapter(c["title"]) == normalize_chapter(point_names[0]) for c in chapters
        )
    ):
        chapter, point_names = point_names[0], []
    if not chapter and not point_names:
        raise ValueError("请选择正式章节或知识点：" + "、".join(c["title"] for c in chapters))
    selected = None
    if chapter:
        target = normalize_chapter(chapter)
        number = re.match(r"第(\d+)章", target)
        title = target[number.end() :].strip(" ·：:-") if number else target
        matches = [
            c
            for c in chapters
            if title and title == re.sub(r"^第\d+章[ ·：:-]*", "", normalize_chapter(c["title"]))
        ]
        if number:
            index = int(number[1]) - 1
            ordinal = chapters[index] if 0 <= index < len(chapters) else None
            matches = [ordinal] if ordinal and (not title or ordinal in matches) else []
        if (
            not matches
            and not number
            and not point_names
            and any(
                normalize_chapter(p["input"]["rows"][0]["name"]) == target for p in points.values()
            )
        ):
            point_names = [chapter]
        elif len(matches) != 1:
            raise ValueError("请确认要使用的正式章节：" + "、".join(c["title"] for c in chapters))
        else:
            selected = matches[0]
    assigned = [
        a
        for a in version["assignments"]
        if a["chapter_id"] is not None
        and (not selected or a["chapter_id"] == selected["chapter_id"])
    ]
    wanted = {normalize_chapter(n) for n in point_names}
    found, refs = set(), []
    for assignment in assigned:
        pid = str(assignment["point_id"])
        point = points.get(pid)
        if not point:
            continue
        rows = point["input"]["rows"]
        name = rows[0]["name"]
        if wanted and normalize_chapter(name) not in wanted:
            continue
        if wanted and normalize_chapter(name) in found:
            raise ValueError("同名知识点有多个，请同时指定正式章节。")
        found.add(normalize_chapter(name))
        if point["classification_input_hash"] != assignment["classification_input_hash"]:
            raise ValueError("所选知识点的证据已变化，请更新目录后重试。")
        for r in rows:
            if document_ids is not None and r["document_id"] not in document_ids:
                continue
            if r["quote"] and r["quote"] in r["locator"].get("content", ""):
                refs.append(
                    dict(
                        point_id=pid,
                        name=name,
                        fact_id=r["fact_id"],
                        chunk_id=r["locator_id"],
                        quote=r["quote"],
                        document_id=r["document_id"],
                        material_version_id=r["material_version_id"],
                    )
                )
    if wanted - found:
        raise ValueError("正式目录内没有匹配到全部指定知识点，请使用知识目录中的名称。")
    if wanted - {normalize_chapter(ref["name"]) for ref in refs}:
        raise ValueError("所选资料未覆盖全部指定知识点，请调整资料或知识点范围。")
    if not refs:
        raise ValueError("所选范围没有带已核验原文的知识点，请选择其他章节或资料。")
    return dict(
        version_id=str(version["id"]),
        chapter_id=str(selected["chapter_id"]) if selected else None,
        title=selected["title"] if selected else "、".join(point_names),
        refs=refs,
    )


def scoped_chunks(store, document, scope):
    """Read current chunks and expose only the excerpts approved for this scope."""
    refs = [r for r in scope["refs"] if r["document_id"] == document["document_id"]]
    if not refs:
        return []
    version = material_version(document)["material_version_id"]
    chunks = {c["chunk_id"]: c for c in store.list_material_chunks(document["document_id"])}
    excerpts = {}
    for ref in refs:
        cid = ref["chunk_id"]
        if (
            ref["material_version_id"] != version
            or cid not in chunks
            or ref["quote"] not in chunks[cid]["content"]
        ):
            raise ValueError("来源资料版本或引文已变化，请重新选择知识目录范围。")
        excerpts.setdefault(cid, {})[ref["quote"]] = None
    return [{**chunks[cid], "content": "\n".join(quotes)} for cid, quotes in excerpts.items()]


def citation_links(scope, chunk_id, quote, name=None):
    return [
        r
        for r in scope["refs"]
        if r["chunk_id"] == chunk_id
        and quote.strip()
        and quote in r["quote"]
        and (name is None or name == r["name"])
    ]


def quiz_documents(materials, config):
    if not config.catalog_scope:
        return materials.select(config.source_document_ids, config.chapter)
    return [
        {**document, "_chat_chunks": scoped_chunks(materials.store, document, config.catalog_scope)}
        for document in materials.select(config.source_document_ids)
    ]
