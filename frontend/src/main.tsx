import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import "./styles.css";
import Workbench from "./Workbench";
import Materials from "./Materials";
import Notes from "./Notes";
import Topbar from "./Topbar";
import AccountCenter from "./AccountCenter";
import NotificationCenter from "./NotificationCenter";
import { AccountMenuContext } from "./AccountMenu";
import SessionGate, { type AccountSession } from "./SessionGate";
import ChatHome from "./ChatHome";
import QuizDrafts from "./QuizDrafts";
import Mistakes from "./Mistakes";
import { api, type Course } from "./workbench-api";
import NavigationIcon, { type NavigationIconName } from "./NavigationIcon";
import LearningReport from "./LearningReport";
import ReviewDashboard from "./ReviewDashboard";
import ReviewCalendar from "./ReviewCalendar";
import { courseHref, legacyHash, readRoute, type CoursePage } from "./course-routes";
import "./course-navigation.css";
import "./review-preview.css";

const navigation: [CoursePage, NavigationIconName, string][] = [["materials", "materials", "资料"], ["notes", "notes", "笔记"], ["quiz", "quiz", "测试"], ["home", "chat", "AI 辅导"]];
const globalNavigation = [
  { page: "dashboard", icon: "home", label: "主页", href: "#/home" },
  { page: "workbench", icon: "courses", label: "我的课程", href: "#/courses" },
  { page: "calendar", icon: "calendar", label: "日历", href: "#/calendar" },
] as const;

function App({ session }: { session: AccountSession }) {
  const [routeHash, setRouteHash] = useState(window.location.hash);
  const acceptedHash = useRef(window.location.hash);
  const route = readRoute(routeHash);
  const page = route.page;
  const [course, setCourse] = useState<Course | null>(null);
  const [courseReady, setCourseReady] = useState(false);
  const [courses, setCourses] = useState<Course[]>([]);
  const [chatKey, setChatKey] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("course-sidebar-collapsed") === "true");
  const [menuOpen, setMenuOpen] = useState(false);
  const sidebarRef = useRef<HTMLElement>(null);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const conversationId = page === "home" ? route.detail?.[0] ?? null : null;
  const activeCourse = route.courseId ? courses.find(item => item.course_id === route.courseId && item.status !== "deleted") ?? null : course;
  const isReviewOverview = page === "dashboard" || page === "calendar" || page === "account" || page === "notifications";
  const inCourse = !isReviewOverview && page !== "workbench" && activeCourse && activeCourse.status !== "deleted";
  const globalPage = globalNavigation.find(item => item.page === page);

  const onSelectCourse = useCallback((value: Course | null) => {
    setCourse(value);
    if (value) {
      setCourses(items => [...items.filter(item => item.course_id !== value.course_id), value]);
      if (value.status !== "deleted") localStorage.setItem("current-course-id", value.course_id);
    }
    void api<{ items: Course[] }>("/api/courses").then(data => setCourses(data.items)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!window.location.hash) window.location.replace("#/courses");
    if (isReviewOverview) {
      setCourseReady(false);
      return;
    }
    let active = true;
    api<{ items: Course[] }>("/api/courses").then(data => {
      if (!active) return;
      setCourses(data.items);
      const linked = readRoute(window.location.hash).courseId;
      const stored = localStorage.getItem("current-course-id");
      const chosen = data.items.find(item => item.course_id === linked && item.status !== "deleted")
        ?? data.items.find(item => item.course_id === stored && item.status !== "deleted")
        ?? data.items.find(item => item.status !== "deleted") ?? null;
      setCourse(chosen);
      if (chosen) localStorage.setItem("current-course-id", chosen.course_id);
      setCourseReady(true);
    }).catch(() => { if (active) { setCourses([]); setCourseReady(true); } });
    return () => { active = false; };
  }, [isReviewOverview]);

  useEffect(() => {
    const onHashChange = (event: HashChangeEvent) => {
      const hash = window.location.hash;
      if (hash === acceptedHash.current) return;
      if (!window.dispatchEvent(new Event("note-navigation", { cancelable: true }))) {
        event.stopImmediatePropagation(); window.history.replaceState(null, "", acceptedHash.current); return;
      }
      acceptedHash.current = hash;
      setRouteHash(hash);
      setMenuOpen(false);
    };
    window.addEventListener("hashchange", onHashChange, true);
    return () => window.removeEventListener("hashchange", onHashChange, true);
  }, []);

  useEffect(() => {
    if (!route.courseId || !activeCourse) return;
    setCourse(activeCourse);
    localStorage.setItem("current-course-id", activeCourse.course_id);
  }, [route.courseId, activeCourse?.course_id]);
  useEffect(() => { localStorage.setItem("course-sidebar-collapsed", String(collapsed)); }, [collapsed]);
  useEffect(() => {
    if (!menuOpen) return;
    const sidebar = sidebarRef.current;
    sidebar?.querySelector<HTMLButtonElement>("button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMenuOpen(false); menuTrigger.current?.focus(); }
      if (event.key === "Tab" && sidebar) {
        const buttons = [...sidebar.querySelectorAll<HTMLButtonElement>("button")];
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  function navigate(id: CoursePage) {
    if (!activeCourse) return;
    const remembered = id === "home" ? localStorage.getItem(`current-conversation-${activeCourse.course_id}`) : null;
    window.location.hash = courseHref(activeCourse.course_id, id, remembered ? [remembered] : []);
  }
  function selectConversation(id: string) {
    if (!activeCourse) return;
    localStorage.setItem(`current-conversation-${activeCourse.course_id}`, id);
    window.location.hash = courseHref(activeCourse.course_id, "home", [id]);
  }
  function newConversation() {
    if (!activeCourse) return;
    localStorage.removeItem(`current-conversation-${activeCourse.course_id}`);
    setChatKey(key => key + 1);
    window.location.hash = courseHref(activeCourse.course_id, "home");
  }
  const adaptedHash = legacyHash(routeHash);
  return <AccountMenuContext.Provider value={session}><div className={`shell course-shell${collapsed ? " sidebar-collapsed" : ""}${page === "report" ? " report-shell" : ""}`}>
    {menuOpen && <button className="course-menu-backdrop" aria-label="关闭导航遮罩" onClick={() => setMenuOpen(false)} />}
    <aside ref={sidebarRef} id="course-sidebar" className={menuOpen ? "is-open" : ""} aria-label="主导航">
      <div className="course-side-header"><span className="course-brand"><NavigationIcon name="courses" /><span>Final Review</span></span><button type="button" className="sidebar-toggle" aria-label={menuOpen ? "关闭导航" : collapsed ? "展开导航栏" : "收起导航栏"} aria-expanded={menuOpen || !collapsed} onClick={() => menuOpen ? setMenuOpen(false) : setCollapsed(value => !value)}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 3v18m7-14-4 5 4 5"/></svg></button></div>
      <nav aria-label="课程导航">
        {globalNavigation.map(item => (
          <button
            key={item.page}
            type="button"
            title={item.label}
            className={page === item.page ? "active" : ""}
            aria-current={page === item.page ? "page" : undefined}
            onClick={() => {
              if (window.location.hash === item.href) setMenuOpen(false);
              else window.location.hash = item.href;
            }}
          >
            <NavigationIcon name={item.icon} />
            <span className="nav-text">{item.label}</span>
          </button>
        ))}
        {inCourse && <><div className="course-nav-label"><span>当前课程</span></div><div className="course-context" title={activeCourse.name}><NavigationIcon name="courses" /><span className="nav-text">{activeCourse.name}</span></div>{navigation.map(([id, icon, text]) => <button type="button" title={text} className={`course-subnav${page === id ? " active" : ""}`} aria-current={page === id ? "page" : undefined} onClick={() => navigate(id)} key={id}><NavigationIcon name={icon} /><span className="nav-text">{text}</span></button>)}</>}
      </nav>
    </aside>
    <div className="course-mobile-header"><button ref={menuTrigger} type="button" aria-label="打开导航" aria-controls="course-sidebar" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)}>☰</button><span>{inCourse ? activeCourse.name : page === "account" ? "个人中心" : page === "notifications" ? "通知中心" : globalPage?.label ?? "我的课程"}</span></div>
    <main className={page === "home" ? "dialog-main" : page === "dashboard" ? "review-dashboard-main" : ""}>
      {page !== "home" && !(page === "notes" && adaptedHash.startsWith("#note/")) && <Topbar />}
      {page === "dashboard" ? <ReviewDashboard />
        : page === "calendar" ? <ReviewCalendar key={route.detail?.[0]} initialDate={route.detail?.[0]} />
        : page === "account" ? <AccountCenter account={session.account} onUpdate={session.update} onReauthenticate={session.reauthenticate} />
        : page === "notifications" ? <NotificationCenter />
        : !courseReady ? <p role="status">正在加载课程…</p> : page === "workbench" ? <Workbench selectedId={course?.course_id ?? null} onSelect={onSelectCourse} onEnter={value => { window.location.hash = courseHref(value.course_id); }} /> : !inCourse ? <section className="course-unavailable"><h1>课程暂不可用</h1><p>课程可能已删除，或当前账户没有访问权限。</p><a href="#/courses">返回我的课程</a></section> : <>
        {page === "home" && <ChatHome key={`${activeCourse.course_id}-${chatKey}`} courseId={activeCourse.course_id} selectedConversationId={conversationId} titleRefreshKey={refreshKey} onConversationSelected={selectConversation} onConversationRenamed={() => setRefreshKey(key => key + 1)} onConversationCreated={id => { setRefreshKey(key => key + 1); selectConversation(id); }} onNewConversation={newConversation} />}
        {page === "materials" && <Materials key={activeCourse.course_id} selectedCourse={activeCourse} />}
        {page === "notes" && <Notes hash={adaptedHash} course={activeCourse} onCourseResolved={id => { const linked = courses.find(item => item.course_id === id); if (linked && linked.course_id !== activeCourse.course_id) { if (route.courseId) window.location.hash = courseHref(id, "notes", route.detail); else onSelectCourse(linked); } }} />}
        {page === "quiz" && <QuizDrafts key={activeCourse.course_id} courseId={activeCourse.course_id} routeDetail={route.detail} />}
        {page === "mistakes" && <Mistakes courses={courses} selectedCourseId={activeCourse.course_id} />}
        {page === "report" && <LearningReport courses={courses} course={activeCourse} onCourse={value => { window.location.hash = courseHref(value.course_id, "report"); }} />}
      </>}
    </main>
  </div></AccountMenuContext.Provider>;
}
createRoot(document.getElementById("root")!).render(
  <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#123c5c", borderRadius: 6, fontFamily: '"Microsoft YaHei UI", "PingFang SC", sans-serif' } }}>
    <SessionGate>{session => <App key={session.account.id} session={session} />}</SessionGate>
  </ConfigProvider>,
);
