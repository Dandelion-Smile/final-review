import { useEffect, useRef, useState, type ReactNode } from "react";
import { Modal } from "antd";
import NavigationIcon, { type NavigationIconName } from "./NavigationIcon";
import { courseHref } from "./course-routes";
import { api, type Course } from "./workbench-api";
import { overviewTests, sourcePosition, type KnowledgeDetail, type KnowledgePoint, type KnowledgeTree, type OverviewAttempt, type OverviewMaterial, type OverviewNote, type OverviewQuiz } from "./course-overview-api";
import "./course-overview.css";

type Resource<T> = { data: T | null; loading: boolean; error: string };
function useResource<T>(path: string | null, revision: number): Resource<T> {
  const [state, setState] = useState<Resource<T>>({ data: null, loading: Boolean(path), error: "" });
  useEffect(() => {
    let active = true;
    setState({ data: null, loading: Boolean(path), error: "" });
    if (path) void api<T>(path).then(data => {
      if (active) setState({ data, loading: false, error: "" });
    }).catch(error => {
      if (active) setState({ data: null, loading: false, error: error instanceof Error ? error.message : "加载失败，请稍后重试" });
    });
    return () => { active = false; };
  }, [path, revision]);
  return state;
}

function Chevron({ className = "" }: { className?: string }) {
  return <svg className={`co-chevron ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>;
}
function updatedLabel(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN", { month: "long", day: "numeric" });
}
function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="co-error" role="alert"><p>{message}</p><button type="button" onClick={onRetry}>重新加载</button></div>;
}
function ContentCard({ title, icon, count, href, loading, error, empty, onRetry, children }: {
  title: string; icon: NavigationIconName; count?: number; href: string; loading: boolean;
  error: string; empty: string; onRetry: () => void; children: ReactNode;
}) {
  return <section className="co-panel co-content-card" aria-label={title}>
    <header><h2><NavigationIcon name={icon} />{title}<span>{count ?? "—"}</span></h2><a href={href} aria-label={`查看全部${title}`}>查看全部<Chevron /></a></header>
    {loading ? <p className="co-card-state" role="status">正在加载…</p> : error ? <ErrorState message={error} onRetry={onRetry} /> : count === 0 ? <p className="co-card-state">{empty}<a href={href}>前往{title.replace("课程", "")} →</a></p> : children}
  </section>;
}

export default function CourseOverview({ course }: { course: Course }) {
  const root = `/api/courses/${encodeURIComponent(course.course_id)}`;
  const [refresh, setRefresh] = useState(0);
  const retry = () => setRefresh(value => value + 1);
  const tree = useResource<KnowledgeTree>(`${root}/knowledge-tree`, refresh);
  const materials = useResource<{ items: OverviewMaterial[] }>(`${root}/documents`, refresh);
  const notes = useResource<{ items: OverviewNote[] }>(`${root}/notes`, refresh);
  const quizzes = useResource<{ items: OverviewQuiz[] }>(`${root}/quizzes`, refresh);
  const attempts = useResource<{ items: OverviewAttempt[] }>(`${root}/formal-attempts`, refresh);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string> | null>(null);
  const [searchExpanded, setSearchExpanded] = useState<Set<string> | null>(null);
  const [selectedPoint, setSelectedPoint] = useState<KnowledgePoint | null>(null);
  const [pointRetry, setPointRetry] = useState(0);
  const pointDetail = useResource<KnowledgeDetail>(selectedPoint ? `${root}/knowledge-points/${encodeURIComponent(selectedPoint.id)}` : null, pointRetry);
  const searchRef = useRef<HTMLInputElement>(null);
  const chapters = tree.data?.chapters ?? [];
  const needle = query.trim().toLocaleLowerCase();
  const filtered = chapters.map(chapter => ({ ...chapter, points: chapter.points.filter(point => !needle || chapter.title.toLocaleLowerCase().includes(needle) || point.name.toLocaleLowerCase().includes(needle)) })).filter(chapter => chapter.points.length);
  const opened = needle ? searchExpanded ?? new Set(filtered.map(chapter => chapter.id)) : expanded ?? new Set(chapters.slice(0, 2).map(chapter => chapter.id));
  const materialItems = materials.data?.items ?? [];
  const readyCount = materialItems.filter(item => item.parse_status === "ready").length;
  const noteItems = [...(notes.data?.items ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const quizItems = [...(quizzes.data?.items ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const testItems = overviewTests(course.course_id, quizItems, attempts.data?.items ?? []);
  const pending = Boolean(tree.data?.build_status.queued || tree.data?.build_status.running || materialItems.some(item => ["queued", "running"].includes(item.parse_status)));
  useEffect(() => {
    if (!pending) return;
    const timer = window.setTimeout(() => setRefresh(value => value + 1), 10000);
    return () => window.clearTimeout(timer);
  }, [pending, refresh]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key === "/" && !selectedPoint && !target.isContentEditable && !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        event.preventDefault(); searchRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selectedPoint]);

  function toggleChapter(id: string, open: boolean) {
    (needle ? setSearchExpanded : setExpanded)(previous => {
      const next = new Set(previous ?? opened);
      if (open) next.add(id); else next.delete(id);
      return next;
    });
  }

  return <section className="course-overview" aria-labelledby="co-title">
    <nav className="co-breadcrumb" aria-label="面包屑"><a href="#/courses">我的课程</a><Chevron /><span>课程总览</span></nav>
    <header className="co-heading"><div><span className="co-eyebrow">课程知识空间</span><h1 id="co-title">{course.name}</h1><p>{course.subject || "从课程资料出发，让每个知识点都有脉络。"}</p><div className="co-metadata"><span><NavigationIcon name="materials" />{materials.loading ? "正在加载资料…" : materials.error ? "资料统计暂不可用" : `${readyCount} 份资料已解析`}</span>{tree.data?.updated_at && <span>知识目录更新于 {updatedLabel(tree.data.updated_at)}</span>}{pending && <span role="status">知识目录正在更新…</span>}</div></div><a className="co-button" href={courseHref(course.course_id, "materials")}><NavigationIcon name="materials" />查看课程资料<span aria-hidden="true">→</span></a></header>
    <div className="co-layout">
      <section className="co-panel co-knowledge" aria-labelledby="co-tree-title">
        <header className="co-tree-heading"><div className="co-tree-title"><span className="co-title-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="2" y="2" width="6" height="6" rx="1"/><path d="M5 8v11h9M5 12h9"/><rect x="14" y="9" width="7" height="6" rx="1"/><rect x="14" y="17" width="7" height="5" rx="1"/></svg></span><div><h2 id="co-tree-title">知识目录</h2><p>按章节梳理已存入的知识点</p></div></div><span className="co-tree-count">{tree.data ? `${chapters.length} 章 · ${tree.data.point_count} 个知识点` : ""}</span></header>
        <div className="co-tree-toolbar"><label className="co-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input ref={searchRef} aria-label="搜索章节或知识点" type="search" placeholder="搜索章节或知识点" value={query} onChange={event => { setQuery(event.target.value); setSearchExpanded(null); }} /><kbd>/</kbd></label><button type="button" onClick={() => (needle ? setSearchExpanded : setExpanded)(new Set(chapters.map(chapter => chapter.id)))} disabled={!chapters.length}>全部展开</button><span className="co-toolbar-divider"/><button type="button" onClick={() => (needle ? setSearchExpanded : setExpanded)(new Set())} disabled={!chapters.length}>全部收起</button></div>
        {tree.loading ? <div className="co-tree-state" role="status">正在加载知识目录…</div> : tree.error ? <ErrorState message={`知识目录加载失败：${tree.error}`} onRetry={retry} /> : !chapters.length ? <div className="co-tree-state"><h3>{pending ? "知识目录整理中" : "还没有已入库的知识点"}</h3><p>{pending ? "资料正在处理，完成后目录会自动更新。" : tree.data?.build_status.failed ? "部分资料的知识整理未完成，请检查资料处理情况。" : "已解析资料中的知识整理入库后，会按资料的章节展示在这里。"}</p><a href={courseHref(course.course_id, "materials")}>查看课程资料 →</a><button type="button" onClick={retry}>刷新知识目录</button></div> : <>
          {needle && <p className="co-search-result" role="status">找到 {filtered.length} 个章节中的 {filtered.reduce((sum, chapter) => sum + chapter.points.length, 0)} 个知识点</p>}
          {!filtered.length ? <div className="co-tree-state"><h3>没有找到相关章节或知识点</h3><button type="button" onClick={() => setQuery("")}>清空搜索</button></div> : filtered.map(chapter => <details className="co-chapter" key={`${needle ? "search" : "browse"}:${chapter.id}`} open={opened.has(chapter.id)} onToggle={event => toggleChapter(chapter.id, event.currentTarget.open)}>
            <summary><Chevron /><span className="co-chapter-number">{chapter.title === "未分章" ? "—" : String(chapters.findIndex(item => item.id === chapter.id) + 1).padStart(2, "0")}</span><span className="co-chapter-title">{chapter.title}</span><span className="co-chapter-count">{chapter.points.length} 个知识点</span></summary>
            <div className="co-chapter-body">{chapter.points.map(point => <button type="button" className="co-point" key={point.id} onClick={() => { setPointRetry(0); setSelectedPoint(point); }} aria-label={`${point.name}，查看知识点来源`}><span>{point.name}</span>{point.importance_score != null && point.importance_score >= 0.8 && <small className="co-important">重点</small>}<small className="co-point-source">{sourcePosition(point.sources[0])}</small><Chevron /></button>)}</div>
          </details>)}
        </>}
        <footer className="co-tree-footer"><span/>知识点整理自当前课程已解析的资料<small>点击知识点查看来源 →</small></footer>
      </section>
      <aside className="co-resources" aria-label="课程内容">
        <ContentCard title="课程笔记" icon="notes" count={notes.data?.items.length} href={courseHref(course.course_id, "notes")} loading={notes.loading} error={notes.error} empty="还没有课程笔记。" onRetry={retry}>{noteItems.slice(0, 2).map(note => <a className="co-resource" key={note.asset_id} href={courseHref(course.course_id, "notes", [note.asset_id, note.latest_revision_id])}><span className="co-resource-icon"><NavigationIcon name="notes" /></span><span className="co-resource-text"><strong>{note.title}</strong><small>{note.sources.length ? `${note.sources.length} 份来源资料 · ` : ""}{updatedLabel(note.updated_at)}更新</small></span><Chevron /></a>)}</ContentCard>
        <ContentCard title="课程资料" icon="materials" count={materials.data?.items.length} href={courseHref(course.course_id, "materials")} loading={materials.loading} error={materials.error} empty="还没有上传资料。" onRetry={retry}>{[...materialItems].sort((a, b) => (b.updated_at || b.uploaded_at || "").localeCompare(a.updated_at || a.uploaded_at || "")).slice(0, 2).map(material => {
          const extension = (material.file_name || material.title).split(".").pop()?.toUpperCase();
          return <a className="co-resource" key={material.document_id} href={courseHref(course.course_id, "materials", material.parse_status === "ready" ? [material.document_id] : [])}><span className={`co-resource-icon co-file${extension?.startsWith("PPT") ? " co-ppt" : ""}`}>{extension && ["PPT", "PPTX", "PDF", "DOC", "DOCX", "MD", "TXT"].includes(extension) ? extension.replace(/X$/, "") : <NavigationIcon name="materials" />}</span><span className="co-resource-text"><strong>{material.title}</strong><small><span className={`co-status ${material.parse_status === "ready" ? "co-ready" : ""}`}>{({ ready: "已解析", queued: "排队中", running: "解析中", failed: "解析失败" } as Record<string, string>)[material.parse_status] ?? "待处理"}</span>{material.chapter || updatedLabel(material.updated_at || material.uploaded_at)}</small></span><Chevron /></a>;
        })}</ContentCard>
        <ContentCard title="课程测试" icon="quiz" count={quizzes.data?.items.length} href={courseHref(course.course_id, "quiz")} loading={quizzes.loading || attempts.loading} error={quizzes.error} empty="还没有课程测试。" onRetry={retry}>{attempts.error && <ErrorState message={`作答记录加载失败：${attempts.error}`} onRetry={retry} />}{testItems.slice(0, 2).map(test => <a className="co-resource" key={test.id} href={test.href}><span className="co-resource-icon"><NavigationIcon name="quiz" /></span><span className="co-resource-text"><strong>{test.title}</strong><small><span className={`co-status ${test.status === "查看结果" ? "co-ready" : ""}`}>{test.status}</span>{test.meta}</small></span>{test.score ? <span className="co-score">{test.score}</span> : <Chevron />}</a>)}</ContentCard>
        <p className="co-resources-hint">一门课程的知识与学习记录，都在这里。</p>
      </aside>
    </div>
    <footer className="co-footer">Final Review<span>让每一次复习都有迹可循</span></footer>
    <Modal open={Boolean(selectedPoint)} onCancel={() => setSelectedPoint(null)} footer={null} title={selectedPoint?.name} width={620} className="co-point-modal" destroyOnHidden>
      {selectedPoint && <><p className="co-modal-description">{selectedPoint.description}</p><h3>知识事实</h3>{pointDetail.loading ? <p role="status">正在加载知识事实…</p> : pointDetail.error ? <ErrorState message={pointDetail.error} onRetry={() => setPointRetry(value => value + 1)} /> : <ul className="co-facts">{pointDetail.data?.facts.filter(fact => fact.status === "supported" && selectedPoint.fact_ids.includes(fact.id)).map(fact => <li key={fact.id}>{fact.fact_text}</li>)}</ul>}<h3>来源资料</h3><div className="co-sources">{selectedPoint.sources.map(source => <a key={`${source.document_id}:${source.locator_id}`} href={courseHref(course.course_id, "materials", [source.document_id, source.locator_id])}><NavigationIcon name="materials" /><span>{source.title}<small>{source.chapter} · {sourcePosition(source)}</small></span><Chevron /></a>)}</div></>}
    </Modal>
  </section>;
}
