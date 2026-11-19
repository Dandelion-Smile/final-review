import { useEffect, useRef, useState, type ReactNode } from "react";
import { Modal } from "antd";
import NavigationIcon, { type NavigationIconName } from "./NavigationIcon";
import { courseHref } from "./course-routes";
import { api, ApiError, apiResponse, type Course } from "./workbench-api";
import { catalogFailed, catalogFailureSummary, catalogNotice, overviewActivity, overviewChapterSummary, overviewTests, placementLabel, sourcePosition, visibleChapters, type KnowledgeDetail, type KnowledgePoint, type KnowledgeTree, type OverviewAttempt, type OverviewMaterial, type OverviewNote, type OverviewQuiz } from "./course-overview-api";
import "./course-overview.css";

function useResource<T>(path: string | null, revision: string | number, wait = false) {
  const [state, setState] = useState<{ path: string; revision: string | number; data: T | null; error: string } | null>(null);
  const current = state?.path === path ? state : null;
  const refreshing = Boolean(path && current?.revision !== revision);
  useEffect(() => {
    if (!path || wait || !refreshing) return;
    const controller = new AbortController();
    void fetch(path, { credentials: "same-origin", signal: controller.signal })
      .then(response => apiResponse<T>(response, path))
      .then(data => {
        if (!controller.signal.aborted) setState({ path, revision, data, error: "" });
      }).catch(error => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : "加载失败，请稍后重试";
        setState(previous => ({ path, revision, data: previous?.path === path ? previous.data : null, error: message }));
      });
    return () => controller.abort();
  }, [path, revision, wait, refreshing]);
  return {
    data: current?.data ?? null,
    loading: refreshing && !current?.data,
    refreshing,
    error: refreshing ? "" : current?.error ?? "",
  };
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
    {loading && <p className="co-card-state" role="status">正在加载…</p>}
    {error && <ErrorState message={error} onRetry={onRetry} />}
    {!loading && (count === 0 && !error ? <p className="co-card-state">{empty}<a href={href}>前往{title.replace("课程", "")} →</a></p> : children)}
  </section>;
}

export default function CourseOverview({ course }: { course: Course }) {
  const root = `/api/courses/${encodeURIComponent(course.course_id)}`;
  const [refresh, setRefresh] = useState(0);
  const retry = () => setRefresh(value => value + 1);
  const [liveRefresh, setLiveRefresh] = useState({ tree: 0, materials: 0 });
  const materials = useResource<{ items: OverviewMaterial[] }>(`${root}/documents`, `${refresh}:${liveRefresh.materials}`);
  // Read the tree after documents settle so a completed parse can hand off to a build.
  const tree = useResource<KnowledgeTree>(`${root}/knowledge-tree`, `${refresh}:${liveRefresh.tree}`, materials.refreshing);
  const refreshTree = () => setLiveRefresh(value => ({ ...value, tree: value.tree + 1 }));
  const [retryingCatalog, setRetryingCatalog] = useState(false);
  const [catalogRetryError, setCatalogRetryError] = useState<{ jobId: string; message: string } | null>(null);
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
  const filtered = visibleChapters(chapters, query, tree.data?.catalog);
  const notice = tree.data ? catalogNotice(tree.data) : null;
  const chapterSummary = tree.data ? overviewChapterSummary(tree.data) : "";
  const job = tree.data?.catalog_job;
  const failedJob = job && catalogFailed(job) ? job : null;
  const failureSummary = failedJob ? catalogFailureSummary(failedJob) : "";
  const retryError = failedJob && catalogRetryError?.jobId === failedJob.job_id ? catalogRetryError.message : "";
  const stale = tree.data?.catalog?.status === "stale";
  const showStaleNote = stale && notice?.text !== "课程资料已有更新，目录尚未同步";
  const opened = needle ? searchExpanded ?? new Set(filtered.map(chapter => chapter.id)) : expanded ?? new Set(chapters.slice(0, 2).map(chapter => chapter.id));
  const materialItems = materials.data?.items ?? [];
  const readyCount = materialItems.filter(item => item.parse_status === "ready").length;
  const noteItems = [...(notes.data?.items ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const quizItems = [...(quizzes.data?.items ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const testItems = overviewTests(course.course_id, quizItems, attempts.data?.items ?? []);
  const activity = overviewActivity(tree.data, materialItems);
  const pollMaterials = activity.parsing && !materials.error;
  const pollTree = (activity.building || activity.catalog || pollMaterials) && !tree.error;
  useEffect(() => {
    if ((!pollMaterials && !pollTree) || tree.refreshing || materials.refreshing || retryingCatalog) return;
    const timer = window.setTimeout(() => setLiveRefresh(value => ({
      tree: value.tree + Number(pollTree),
      materials: value.materials + Number(pollMaterials),
    })), 10000);
    return () => window.clearTimeout(timer);
  }, [pollTree, pollMaterials, tree.refreshing, materials.refreshing, retryingCatalog]);
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

  async function retryCatalog() {
    if (!failedJob || retryingCatalog || tree.refreshing) return;
    setRetryingCatalog(true);
    setCatalogRetryError(null);
    try {
      await api(`${root}/knowledge-catalog/jobs/${encodeURIComponent(failedJob.job_id)}/retry`, "POST");
      refreshTree();
    } catch (error) {
      const message = error instanceof ApiError && error.status === 409 ? error.message : "重试请求失败，请稍后再试。";
      setCatalogRetryError({ jobId: failedJob.job_id, message });
      if (error instanceof ApiError && error.status === 409) refreshTree();
    } finally {
      setRetryingCatalog(false);
    }
  }

  function toggleChapter(id: string, open: boolean) {
    (needle ? setSearchExpanded : setExpanded)(previous => {
      const next = new Set(previous ?? opened);
      if (open) next.add(id); else next.delete(id);
      return next;
    });
  }

  return <section className="course-overview" aria-labelledby="co-title">
    <nav className="co-breadcrumb" aria-label="面包屑"><a href="#/courses">我的课程</a><Chevron /><span>课程总览</span></nav>
    <header className="co-heading"><div><span className="co-eyebrow">课程知识空间</span><h1 id="co-title">{course.name}</h1><p>{course.subject || "从课程资料出发，让每个知识点都有脉络。"}</p><div className="co-metadata"><span><NavigationIcon name="materials" />{materials.loading ? "正在加载资料…" : materials.error ? "资料统计暂不可用" : `${readyCount} 份资料已解析`}</span>{tree.data?.updated_at && <span>知识内容更新于 {updatedLabel(tree.data.updated_at)}</span>}{pollMaterials && <span role="status">资料正在解析</span>}{activity.building && !tree.error && <span role="status">知识点正在入库</span>}{materialItems.some(item => item.parse_status === "failed") && <span>部分资料解析失败</span>}</div></div><a className="co-button" href={courseHref(course.course_id, "materials")}><NavigationIcon name="materials" />查看课程资料<span aria-hidden="true">→</span></a></header>
    <div className="co-layout">
      <section className="co-panel co-knowledge" aria-labelledby="co-tree-title">
        <header className="co-tree-heading"><div className="co-tree-title"><span className="co-title-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="2" y="2" width="6" height="6" rx="1"/><path d="M5 8v11h9M5 12h9"/><rect x="14" y="9" width="7" height="6" rx="1"/><rect x="14" y="17" width="7" height="5" rx="1"/></svg></span><div><h2 id="co-tree-title">知识目录</h2><p>按章节梳理已存入的知识点</p></div></div><span className="co-tree-count">{chapterSummary}</span></header>
        <div className="co-tree-toolbar"><label className="co-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input ref={searchRef} aria-label="搜索章节或知识点" type="search" placeholder="搜索章节或知识点" value={query} onChange={event => { setQuery(event.target.value); setSearchExpanded(null); }} /><kbd>/</kbd></label><button type="button" onClick={() => (needle ? setSearchExpanded : setExpanded)(new Set(chapters.map(chapter => chapter.id)))} disabled={!chapters.length}>全部展开</button><span className="co-toolbar-divider"/><button type="button" onClick={() => (needle ? setSearchExpanded : setExpanded)(new Set())} disabled={!chapters.length}>全部收起</button></div>
        {tree.loading && <div className="co-tree-state" role="status">正在加载知识目录…</div>}
        {tree.error && <ErrorState message={tree.data ? "知识目录刷新失败，当前展示上次加载的内容。" : `知识目录加载失败：${tree.error}`} onRetry={refreshTree} />}
        {notice && !(tree.error && activity.catalog) && <div className={`co-catalog-note co-${notice.tone}`}>
          <p role={failedJob ? "alert" : "status"}>{notice.text}{failureSummary && `。${failureSummary}`}</p>
          {failedJob && failedJob.error_code !== "CatalogBudgetExceeded" && course.status !== "archived" && course.status !== "deleted" &&
            <button type="button" disabled={retryingCatalog || tree.refreshing || Boolean(tree.error)} onClick={() => void retryCatalog()}>{retryingCatalog ? "正在提交…" : "重试目录任务"}</button>}
          {retryError && <p role="alert">{retryError}</p>}
        </div>}
        {showStaleNote && <p className="co-catalog-note co-attention" role="status">课程资料已有更新，目录尚未同步</p>}
        {!chapters.length && Boolean(tree.data?.build_status.failed) && <p className="co-catalog-note co-warning">部分资料的知识整理未完成，请检查资料处理情况。</p>}
        {tree.data && (!chapters.length ? <div className="co-tree-state"><h3>还没有已入库的知识点</h3><p>已解析资料中的知识整理入库后，会按资料的章节展示在这里。</p><a href={courseHref(course.course_id, "materials")}>查看课程资料 →</a><button type="button" disabled={tree.refreshing} onClick={refreshTree}>刷新知识目录</button></div> : <>
          {needle && <p className="co-search-result" role="status">找到 {filtered.length} 个章节中的 {filtered.reduce((sum, chapter) => sum + chapter.points.length, 0)} 个知识点</p>}
          {!filtered.length ? <div className="co-tree-state"><h3>没有找到相关章节或知识点</h3><button type="button" onClick={() => setQuery("")}>清空搜索</button></div> : filtered.map(chapter => <details className="co-chapter" key={`${needle ? "search" : "browse"}:${chapter.id}`} open={opened.has(chapter.id)} onToggle={event => toggleChapter(chapter.id, event.currentTarget.open)}>
            <summary><Chevron /><span className="co-chapter-number">{chapter.id === "未分章" ? "—" : String(chapters.findIndex(item => item.id === chapter.id) + 1).padStart(2, "0")}</span><span className="co-chapter-title">{chapter.title}</span><span className="co-chapter-count">{chapter.points.length} 个知识点</span></summary>
            <div className="co-chapter-body">{chapter.description ? <p className="co-chapter-description">{chapter.description}</p> : null}{chapter.points.length ? chapter.points.map(point => {
              const placement = placementLabel(point);
              return <button type="button" className="co-point" key={point.id} onClick={() => { setPointRetry(0); setSelectedPoint(point); }} aria-label={`${point.name}，查看知识点来源`}><span>{point.name}</span>{placement && <small className="co-placement">{placement}</small>}{point.importance_score != null && point.importance_score >= 0.8 && <small className="co-important">重点</small>}<small className="co-point-source">{sourcePosition(point.sources[0])}</small><Chevron /></button>;
            }) : <p className="co-chapter-empty">当前没有可展示的知识点</p>}</div>
          </details>)}
        </>)}
        <footer className="co-tree-footer"><span/>知识点整理自当前课程已解析的资料<small>点击知识点查看来源 →</small>{chapters.length > 0 && !pollTree && !tree.error && <button type="button" disabled={tree.refreshing || retryingCatalog} onClick={refreshTree}>刷新知识目录</button>}</footer>
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
