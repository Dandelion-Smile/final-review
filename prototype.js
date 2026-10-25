/* Standalone navigation prototype. No calls to the existing application or backend. */
(() => {
  'use strict';
  const app = document.getElementById('app'), dialog = document.getElementById('dialog');
  const storageKey = 'final-review-ui-v2-design-20261025-v1';
  const original = structuredClone(window.FRMock);
  let db = structuredClone(original), saved = null;
  try { saved = JSON.parse(localStorage.getItem(storageKey)); } catch (_) { /* File-mode storage can be unavailable. */ }
  if (saved?.db?.now === original.now) db = saved.db;
  const ui = { currentSetId: saved?.currentSetId || null, expanded: saved?.expanded || false, menu: false, sidebarCollapsed: saved?.sidebarCollapsed === true,
    setSearch: '', setTab: 'all', setSort: 'recent', noteSearch: '', noteType: 'all', quizSearch: '', quizTab: 'all', knowledgeFilter: null,
    materialSearch: '', materialChapter: 'all', materialSource: 'all', selectedMaterials: new Set(), tutorScope: null,
    calendarMonth: '2026-10', calendarDate: '2026-10-25', calendarView: 'month', reportDays: 7 };
  const paths = {
    home:'M3 10l9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H8v7H4a1 1 0 0 1-1-1z',
    book:'M12 5c-3-2-6-2-10-1v16c4-1 7-1 10 1m0-16c3-2 6-2 10-1v16c-4-1-7-1-10 1V5',
    folder:'M3 5h6l2 3h10v12H3z',calendar:'M4 5h16v16H4zM8 2v6m8-6v6M4 10h16',
    file:'M6 2h8l4 4v16H6zM14 2v5h5M9 11h6m-6 4h6',note:'M5 3h14v18H5zM8 7h8M8 11h8M8 15h5',
    chart:'M3 21h18M5 17V9h3v8m3 0V4h3v13m3 0V7h3v10',chat:'M3 3h18v14H8l-5 4zM7 8h1m3 0h1m3 0h1',
    settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8m-8 4-2-1 2-4 2 1 3-2V3h5v3l3 2 2-1 2 4-2 1v3l2 1-2 4-2-1-3 2v2H9v-2l-3-2-2 1-2-4 2-1z',
    help:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20m-3-14a3 3 0 1 1 5 2c-2 1-2 2-2 4m0 3v1',
    chevron:'M9 5l7 7-7 7',down:'M5 9l7 7 7-7',back:'M19 12H5m6-6-6 6 6 6',search:'M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14m5-2 6 6',
    plus:'M12 4v16M4 12h16',bell:'M5 17h14l-2-4V8a5 5 0 0 0-10 0v5zM10 21h4',
    clock:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20m0-16v6l4 2',upload:'M12 17V3m-5 5 5-5 5 5M3 15v6h18v-6',
    check:'M4 12l5 5L20 6',close:'M5 5l14 14M5 19 19 5',cap:'M2 8l10-5 10 5-10 5-10-5m4 3v6l6 3 6-3v-6m4-3v8',
    monitor:'M3 3h18v14H3zM12 17v5m-5 0h10',sigma:'M19 3H5l8 9-8 9h14',layers:'M12 2 2 7l10 5 10-5-10-5M2 12l10 5 10-5M2 17l10 5 10-5',
    bookmark:'M6 3h12v19l-6-5-6 5z',send:'M3 3l19 9L3 21l4-9-4-9m4 9h15',bot:'M5 7h14v13H5zM12 3v4M9 12h.1m5.9 0h.1M9 16h6M2 11v5m20-5v5',
    export:'M14 3h7v7m0-7-10 10M9 4H3v17h17v-6',edit:'M14 4l6 6M4 20l5-1L21 7l-6-6L3 13z',menu:'M3 6h18M3 12h18M3 18h18',target:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20m0-5a5 5 0 1 0 0-10 5 5 0 0 0 0 10m0-5h.1',
    'sidebar-close':'M3 3h18v18H3zM8 3v18m8-14-4 5 4 5',
    'sidebar-open':'M3 3h18v18H3zM8 3v18m5-14 4 5-4 5',
  };
  const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.file}"/></svg>`;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
  const find = (items, id) => items.find(x => x.id === id);
  const setBy = id => find(db.studySets, id);
  const inSet = (items, id) => items.filter(x => x.studySetId === id);
  const letter = i => 'ABCD'[i] || '未答';
  const base = id => `/study-sets/${encodeURIComponent(id)}`;
  const url = (id, section) => `${base(id)}/${section}`;
  const href = route => `#${route}`;
  const link = (text, route, cls = 'btn', ico = '') => `<a class="${cls}" href="${href(route)}">${ico ? icon(ico) : ''}${esc(text)}</a>`;
  const button = (text, action, cls = 'btn', data = '', ico = '') => `<button class="${cls}" data-action="${action}" ${data}>${ico ? icon(ico) : ''}${esc(text)}</button>`;
  const badge = (text, color = '') => `<span class="badge ${color}">${esc(text)}</span>`;
  const tile = (ico, color = '') => `<span class="tile ${color}">${icon(ico)}</span>`;
  const progress = (percent, color = '') => `<div class="progress ${color}" role="progressbar" aria-label="进度" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"><span style="width:${percent}%"></span></div>`;
  const fmt = value => new Date(value).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric' });
  const time = value => new Date(value).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false });
  const localDate = value => value.slice(0,10);
  const daysUntil = value => Math.max(0,Math.round((Date.parse(localDate(value)+'T00:00:00+08:00')-Date.parse(localDate(db.now)+'T00:00:00+08:00'))/86400000));
  const answered = attempt => attempt.questionIds.filter(id => Number.isInteger(attempt.answers[id])).length;
  const correct = attempt => attempt.questionIds.filter(id => attempt.answers[id] === find(db.questions,id)?.correct).length;
  const score = attempt => Math.round(correct(attempt) / attempt.questionIds.length * 100);
  const attemptsFor = quiz => db.attempts.filter(a => a.quizId === quiz.id);
  const ongoing = quiz => attemptsFor(quiz).find(a => a.status === 'in_progress');
  const latest = quiz => attemptsFor(quiz).filter(a => a.status === 'graded').sort((a,b) => b.submittedAt.localeCompare(a.submittedAt))[0];
  const attemptUrl = a => `${url(a.studySetId,'quizzes')}/${a.quizId}/attempts/${a.id}${a.status === 'graded' ? '/result' : ''}`;
  function persist() { try { localStorage.setItem(storageKey, JSON.stringify({db,currentSetId:ui.currentSetId,expanded:ui.expanded,sidebarCollapsed:ui.sidebarCollapsed})); } catch (_) {} }
  function toast(message) { const el = document.getElementById('toast'); el.textContent = message; el.classList.add('visible'); clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove('visible'),3500); }
  function route() {
    const parts = (location.hash.slice(1) || '/home').split('/').filter(Boolean).map(x => { try { return decodeURIComponent(x); } catch (_) { return ''; } });
    if (parts[0] === 'study-sets' && parts[1]) return { page:parts[2] || 'materials', setId:parts[1], itemId:parts[3], attemptId:parts[5], result:parts[6] === 'result', parts };
    return { page: parts[0] || 'home', parts };
  }
  function go(path) { if (location.hash === href(path)) render(); else location.hash = path; }
  function empty(title, description, action = '') { return `<section class="panel empty">${tile('book')}<h2>${esc(title)}</h2><p>${esc(description)}</p>${action}</section>`; }
  function head(title, description = '', action = '') { return `<div class="page-head"><div><h1>${esc(title)}</h1>${description ? `<p>${esc(description)}</p>` : ''}</div><div class="actions">${action}</div></div>`; }
  function panel(title, body, extra = '') { return `<section class="panel"><div class="panel-head"><h2>${esc(title)}</h2>${extra}</div>${body}</section>`; }
  function search(name, placeholder, value = '') { return `<label class="search">${icon('search')}<input data-input="${name}" aria-label="${esc(placeholder)}" placeholder="${esc(placeholder)}" value="${esc(value)}"></label>`; }
  function tabs(name, choices, selected) { return `<div class="tabs" role="group" aria-label="${esc(name)}">${choices.map(([id,title]) => `<button data-action="tab" data-group="${name}" data-value="${id}" class="${selected === id ? 'selected' : ''}" aria-pressed="${selected === id}">${esc(title)}</button>`).join('')}</div>`; }
  function select(name, choices, selected) { return `<select data-select="${name}" aria-label="${esc(choices[0][1])}">${choices.map(([id,text]) => `<option value="${esc(id)}" ${selected === id ? 'selected' : ''}>${esc(text)}</option>`).join('')}</select>`; }
  function sidebar(r) {
    const nav = (name, ico, path, active, sub=false) => `<a class="nav ${active?'active':''} ${sub?'subnav':''}" href="${href(path)}" aria-label="${name}" title="${name}" ${active?'aria-current="page"':''}>${icon(ico)}<span class="nav-text">${name}</span></a>`;
    const navAction = (name,action,ico) => `<button class="nav" data-action="${action}" aria-label="${name}" title="${name}">${icon(ico)}<span class="nav-text">${name}</span></button>`;
    const set = setBy(ui.currentSetId), sections = [['materials','资料','folder'],['notes','笔记','note'],['quizzes','测试','file'],['report','学习报告','chart'],['tutor','AI 辅导','chat']];
    const mobile = matchMedia('(max-width:760px)').matches, toggleLabel = mobile ? '关闭导航' : ui.sidebarCollapsed ? '展开导航栏' : '收起导航栏';
    return `<aside id="study-sidebar" class="sidebar ${ui.menu?'open':''}" aria-label="主导航"><div class="side-header"><a class="brand" href="#/home" aria-label="Final Review 主页">${icon('cap')}<span>Final Review</span></a><button class="sidebar-toggle" data-action="toggle-sidebar" aria-label="${toggleLabel}" title="${toggleLabel}" aria-controls="study-sidebar" aria-expanded="${mobile ? ui.menu : !ui.sidebarCollapsed}">${icon(mobile?'close':ui.sidebarCollapsed?'sidebar-open':'sidebar-close')}</button></div>${nav('主页','home','/home',r.page==='home')}${nav('我的学习集','book','/study-sets',r.page==='study-sets')}${nav('日历','calendar','/calendar',r.page==='calendar')}${set && ui.expanded ? `<div class="nav-label"><span>当前学习集</span></div><button class="set-selector" data-action="switch-set" aria-label="切换学习集：${esc(set.name)}" title="${esc(set.name)}">${icon(set.icon||'book')}<span class="nav-text">${esc(set.name)}</span><span class="set-chevron">${icon('down')}</span></button>${sections.map(([id,label,ico]) => nav(label,ico,url(set.id,id),r.setId===set.id && r.page===id,true)).join('')}` : ''}<div class="side-footer">${navAction('帮助中心','help','help')}${navAction('个人设置','settings','settings')}</div></aside>`;
  }
  function refreshSidebar() {
    const sidebarEl = document.getElementById('study-sidebar');
    if (sidebarEl) sidebarEl.outerHTML = sidebar(route());
    document.querySelector('.shell')?.classList.toggle('sidebar-collapsed',ui.sidebarCollapsed);
  }
  function breadcrumbs(r) {
    if (!r.setId) return `<b>${({home:'主页','study-sets':'我的学习集',calendar:'复习日历'})[r.page] || '页面'}</b>`;
    const set = setBy(r.setId), names = {materials:'资料',notes:'笔记',quizzes:'测试',tutor:'AI 辅导',report:'学习报告'};
    let out = `${link('我的学习集','/study-sets','')}<span>/</span>${link(set?.name || '学习集',url(r.setId,'materials'),'')}<span>/</span>`;
    if (r.itemId) out += `${link(names[r.page]||'内容',url(r.setId,r.page),'')}<span>/</span><b>${esc(r.page === 'notes' ? find(db.notes,r.itemId)?.title || '笔记' : r.result ? '测试结果' : '在线答题')}</b>`;
    else out += `<b>${names[r.page] || '页面'}</b>`;
    return out;
  }
  function topbar(r) {
    const a = find(db.attempts,r.attemptId), q = find(db.quizzes,r.itemId);
    const focus = a && q && a.status==='in_progress' && !r.result;
    return `<header class="topbar"><div class="row grow">${button('','mobile-menu','icon-button mobile-nav','aria-label="打开导航"','menu')}${focus ? `${link('返回测试',url(r.setId,'quizzes'),'btn small','back')}<strong>${esc(q.title)}</strong>` : `<nav class="breadcrumbs" aria-label="面包屑">${breadcrumbs(r)}</nav>`}</div><div class="top-actions">${focus ? `<small class="num" style="color:#398b61">✓ 本地已保存</small><span class="num">${icon('clock')} <b id="elapsed">${elapsed(a)}</b></span>`:''}${button('','notifications','icon-button dot','aria-label="通知"','bell')}<button class="profile" data-action="settings"><span class="avatar">林</span><span>${esc(db.user.name)}</span>${icon('down')}</button></div></header>`;
  }
  function render() {
    const r = route();
    if (r.page === 'study-sets' && !r.setId) ui.expanded = false;
    if (r.setId && setBy(r.setId)) { if (ui.currentSetId !== r.setId) { ui.selectedMaterials.clear(); ui.tutorScope = null; } ui.currentSetId = r.setId; ui.expanded = true; }
    ui.menu = false;
    const focus = r.page === 'quizzes' && find(db.attempts,r.attemptId)?.status==='in_progress' && !r.result;
    app.innerHTML = `<div class="shell ${focus?'focus':''} ${ui.sidebarCollapsed?'sidebar-collapsed':''}">${sidebar(r)}<div class="workspace">${topbar(r)}<div class="preview-ribbon"><span>V2 设计原型 · 全部为本地示例 · 2026.10.25</span><button data-action="reset">重置演示</button></div><main id="main-content">${content(r)}</main></div></div>`;
    persist();
  }
  function content(r) {
    if (r.setId && !setBy(r.setId)) return notFound();
    if (r.page === 'home') return home();
    if (r.page === 'study-sets') return studySets();
    if (r.page === 'calendar') return calendar();
    if (!r.setId) return notFound();
    if (r.page === 'materials') return materialsPage(r);
    if (r.page === 'notes') return r.itemId ? notePage(r) : notesPage(r);
    if (r.page === 'quizzes') return r.attemptId ? attemptPage(r) : quizzesPage(r);
    if (r.page === 'tutor') return tutorPage(r);
    if (r.page === 'report') return reportPage(r);
    return notFound();
  }
  function notFound() { return empty('没有找到这个内容','内容可能已被移除，或者链接中的学习集与内容不匹配。',link('返回我的学习集','/study-sets','btn primary')); }
  function home() {
    const reading = find(db.notes,db.reading.noteId), active = db.attempts.find(a=>a.status==='in_progress'), q = active && find(db.quizzes,active.quizId), exam = db.exams[0];
    const continueReading = reading ? `<article class="card continue"><div class="row">${tile('note')}<div>${badge('上次学习')}<h3>${esc(reading.title)}</h3><small>${esc(setBy(reading.studySetId)?.name)} · 核心概念</small></div></div><p class="muted" style="margin:16px 0 0">上次阅读到：传输层的功能</p>${progress(db.reading.percent)}${link('继续阅读',`${url(reading.studySetId,'notes')}/${reading.id}`,'btn primary wide','book')}</article>` : '';
    const continueQuiz = active ? `<article class="card continue">${badge('未完成测试','red')}<h3>${esc(q.title)}</h3><p class="muted">${esc(setBy(active.studySetId)?.name)} · ${esc(q.chapter)}</p><p style="margin-top:22px" class="muted">共 ${active.questionIds.length} 题 · 已完成 ${answered(active)} 题</p>${progress(Math.round(answered(active)/active.questionIds.length*100))}${link('继续作答',attemptUrl(active),'btn soft wide','file')}</article>` : `<article class="card continue">${badge('继续练习','green')}<h3>本次测试已完成</h3><p class="muted">回看解析，查漏补缺。</p>${link('查看学习报告',url('network','report'),'btn soft wide','chart')}</article>`;
    return `<div class="columns"><div class="stack"><section class="panel hero"><div><h1>下午好，${esc(db.user.name)}</h1><p>从上次停下的地方继续复习，保持专注，一点一点接近目标。</p></div><div class="hero-art" aria-hidden="true">${icon('book')}</div></section>${panel('继续学习',`<div class="two">${continueReading}${continueQuiz}</div>`)}</div><div class="stack">${panel('近期考试',`<div class="exam-box"><div class="row">${tile('calendar')}<div class="grow"><h3>${esc(exam.title)}</h3><small>${fmt(exam.startsAt)} · 期末考试</small></div><div><span class="countdown">${daysUntil(exam.startsAt)}</span><small> 天</small></div></div></div><p class="muted" style="margin-top:18px">每天留一点时间，稳步完成复习计划。</p>`,link('查看全部','/calendar','link'))}${panel('待完成',`<div class="check-task"><label for="todo-read">阅读传输层复习笔记<br><small>梳理 TCP 与 UDP 的区别</small></label><input id="todo-read" type="checkbox" aria-label="标记阅读待办完成"></div><div class="check-task"><label for="todo-test">完成第三章综合练习<br><small>从当前作答进度继续</small></label><input id="todo-test" type="checkbox" aria-label="标记测试待办完成"></div>`,link('查看安排','/calendar','link'))}</div></div><section class="panel" style="margin-top:20px"><div class="panel-head"><h2>最近学习集</h2>${link('查看全部','/study-sets','link')}</div><div class="grid">${db.studySets.filter(s=>s.status==='active').slice(0,3).map((s,i)=>`<article class="card"><div class="row"><span class="tile book-cover ${s.color}">${icon(s.icon)}</span><div><h3>${esc(s.name)}</h3><p class="muted" style="font-size:12px;margin-top:9px">${inSet(db.materials,s.id).length} 份资料 · ${inSet(db.notes,s.id).length} 篇笔记</p><small>最近学习：${fmt(s.lastVisitedAt)}</small></div></div>${link('进入学习集',url(s.id,'materials'),'btn soft wide','chevron')}</article>`).join('')}</div></section>`;
  }
  function studySets() {
    return head('我的学习集','把每门课程的资料与复习内容放在一起',button('新建学习集','new-set','btn primary','','plus')) + `<section class="panel toolbar">${search('setSearch','搜索学习集',ui.setSearch)}${tabs('setTab',[['all','全部'],['recent','最近学习'],['archived','已归档']],ui.setTab)}${select('setSort',[['recent','最近学习'],['name','名称排序']],ui.setSort)}</section><div id="set-cards" class="grid">${setCards()}</div>`;
  }
  function setCards() {
    let items = db.studySets.filter(s=>s.name.toLowerCase().includes(ui.setSearch.toLowerCase()) && (ui.setTab==='archived' ? s.status==='archived' : ui.setTab==='recent' ? s.status!=='archived' : true));
    items.sort(ui.setSort === 'name' ? (a,b)=>a.name.localeCompare(b.name,'zh-CN') : (a,b)=>b.lastVisitedAt.localeCompare(a.lastVisitedAt));
    if (ui.setTab==='recent') items = items.slice(0,3);
    return `${ui.setTab==='all' && !ui.setSearch ? `<button class="create-card" data-action="new-set"><span>＋</span><h3>创建新的学习集</h3><small>添加一门课程，开始整理资料与复习内容</small></button>` : ''}${items.map(s=>`<article class="set-card"><div class="row">${tile(s.icon,s.color)}<div class="grow"><h2>${esc(s.name)}</h2>${s.status==='archived'?badge('已归档','gray'):''}</div>${button('','set-info','icon-button',`data-id="${s.id}" aria-label="${esc(s.name)}的更多信息"`,'down')}</div><p class="description">${esc(s.description)}</p><div class="set-stats">${[['materials','资料',db.materials],['notes','笔记',db.notes],['quizzes','测试',db.quizzes]].map(([page,name,items])=>`<a href="${href(url(s.id,page))}">${name}<b>${inSet(items,s.id).length}</b></a>`).join('')}</div><div class="set-bottom"><small>${icon('clock')} 最近学习：${fmt(s.lastVisitedAt)}</small>${link('进入学习集',url(s.id,'materials'),'link')}</div></article>`).join('')}${!items.length ? empty('没有匹配的学习集','试试其他关键词，或创建一个新的学习集。') : ''}`;
  }
  function materialsPage(r) {
    const items = inSet(db.materials,r.setId);
    return head('课程资料','上传课件、讲义和习题，作为笔记、测试与答疑的依据',button('上传资料','upload','btn primary','','upload')) + `<section class="panel" style="margin-bottom:20px"><div class="upload-zone">${icon('upload')}<h3>拖入文件，或 ${button('点击上传','upload','link')}</h3><p>支持 PDF、Word、PPT 和图片 · 本原型仅演示资料状态</p></div></section><section class="panel"><div class="toolbar">${search('materialSearch','搜索资料',ui.materialSearch)}${select('materialChapter',[['all','全部章节'],...Array.from(new Set(items.map(x=>x.chapter))).map(x=>[x,x])],ui.materialChapter)}${select('materialSource',[['all','全部来源'],...Array.from(new Set(items.map(x=>x.sourceType))).map(x=>[x,x])],ui.materialSource)}</div><div id="material-table">${materialTable(r.setId)}</div><div id="batch">${batchActions(r.setId)}</div></section>`;
  }
  function materialTable(setId) {
    const items = inSet(db.materials,setId).filter(x=>x.title.includes(ui.materialSearch) && (ui.materialChapter==='all'||x.chapter===ui.materialChapter) && (ui.materialSource==='all'||x.sourceType===ui.materialSource));
    if (!items.length) return empty('暂时没有资料','上传第一份课件，或调整搜索和筛选条件。',button('上传资料','upload','btn primary','','upload'));
    return `<div class="table-wrap"><table class="material-table"><thead><tr><th>选择</th><th>文件名称</th><th>章节</th><th>来源类型</th><th>处理状态</th><th>上传时间</th><th>操作</th></tr></thead><tbody>${items.map(m=>`<tr class="${ui.selectedMaterials.has(m.id)?'selected':''}"><td><input type="checkbox" data-material="${m.id}" aria-label="选择${esc(m.title)}" ${ui.selectedMaterials.has(m.id)?'checked':''}></td><td><div class="row"><span class="file-icon ${m.fileType}">${m.fileType}</span>${esc(m.title)}</div></td><td>${esc(m.chapter)}</td><td>${esc(m.sourceType)}</td><td>${m.status==='ready'?badge('✓ 已就绪','green'):m.status==='processing'?`${badge('正在解析','orange')}${progress(m.progress,'orange')}<small>内容识别 ${m.progress}%</small>`:`${badge('需要重试','red')}<br><small>${esc(m.error)}</small>`}</td><td>${fmt(m.uploadedAt)}</td><td>${m.status==='failed'?button('重试','retry','btn small',`data-id="${m.id}"`):button('查看','material','link',`data-id="${m.id}"`)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function batchActions(setId) {
    const selected = [...ui.selectedMaterials].map(id=>find(db.materials,id)).filter(m=>m?.studySetId===setId), usable = selected.length>0 && selected.every(m=>m.status==='ready');
    return `<div class="batch"><div>已选择 <strong>${selected.length}</strong> 份资料${selected.some(m=>m.status!=='ready')?'<br><small>请等待处理完成、重试或移除不可用资料</small>':''}</div><div class="actions">${button('生成笔记','generate-note','btn primary',usable?'':'disabled','note')}${button('生成测试','generate-quiz','btn',usable?'':'disabled','file')}${button('向 AI 提问','ask-selected','btn',usable?'':'disabled','chat')}</div></div>`;
  }
  function notesPage(r) {
    return head('课程笔记','从资料整理重点，把理解留在可随时回看的笔记里',button('生成笔记','generate-note','btn primary','','plus'))+`<section class="panel toolbar">${search('noteSearch','搜索笔记',ui.noteSearch)}${select('noteType',[['all','全部类型'],['考前冲刺','考前冲刺'],['知识整理','知识整理'],['考点清单','考点清单']],ui.noteType)}</section><div id="note-cards" class="grid">${noteCards(r.setId)}</div>`;
  }
  function noteCards(id) {
    const notes = inSet(db.notes,id).filter(n=>n.title.includes(ui.noteSearch) && (ui.noteType==='all'||n.type===ui.noteType));
    return notes.length ? notes.map(n=>`<article class="panel"><div class="row between">${tile('note')}${badge(n.status==='confirmed'?'已确认':'待复核',n.status==='confirmed'?'green':'orange')}</div><h2 style="font-size:19px;margin:18px 0 10px">${esc(n.title)}</h2><p class="muted">${esc(n.type)} · 基于 ${n.sourceRefIds.length} 份资料</p><p style="margin:12px 0 18px"><small>更新于 ${fmt(n.updatedAt)} · ${n.sections.length} 个章节</small></p>${link(n.id===db.reading.noteId?'继续阅读':'打开笔记',`${url(id,'notes')}/${n.id}`,'btn soft wide','book')}</article>`).join('') : empty('还没有可显示的笔记','先上传资料，再生成第一篇笔记。',button('生成笔记','generate-note','btn primary'));
  }
  function sourceChips(ids) {
    return (ids || []).map(id=>{const ref=find(db.sourceRefs,id),m=ref&&find(db.materials,ref.materialId);return m?button(`${m.title} · ${ref.pageStart}–${ref.pageEnd} 页`,'source','citation',`data-id="${id}"`):'';}).join('');
  }
  function sourceCards(ids) {
    return ids.map(id=>{const ref=find(db.sourceRefs,id),m=ref&&find(db.materials,ref.materialId);return m?`<article class="source-card"><button data-action="source" data-id="${ref.id}"><div class="row"><span class="file-icon ${m.fileType}">${m.fileType}</span><div><h3>${esc(m.title)}</h3><small>第 ${ref.pageStart}–${ref.pageEnd} 页</small></div></div><p>${esc(ref.quote)}</p><span class="badge">打开来源</span></button></article>`:'';}).join('');
  }
  function notePage(r) {
    const n=find(db.notes,r.itemId);if(!n||n.studySetId!==r.setId)return notFound();
    return head(n.title,`基于 ${n.sourceRefIds.length} 份资料生成 · 更新于 ${fmt(n.updatedAt)} ${time(n.updatedAt)}`,`${button('编辑','edit-note','btn','','edit')}${button('导出','export','btn primary','','export')}`)+`${n.status==='draft'?'<div class="tip" style="margin-bottom:16px">草稿 · 等待复核。确认与版本编辑将在正式 V2 中接入。</div>':''}<div class="reading-layout"><aside class="panel outline"><h2>笔记目录</h2><nav aria-label="笔记目录">${n.sections.map((s,i)=>`<a href="#${esc(s.id)}" data-section="${s.id}"><span>${i+1}</span>${esc(s.title)}</a>`).join('')}</nav></aside><article class="panel article">${n.sections.map((s,i)=>`<section id="section-${s.id}"><h2>${i+1}. ${esc(s.title)}</h2>${s.html}</section>`).join('')}</article><aside class="panel sources"><h2>相关资料</h2><p class="muted" style="font-size:12px;margin-top:8px">当前内容基于以下资料整理</p><div class="source-list">${sourceCards(n.sourceRefIds)}</div><div class="tip" style="margin-top:18px"><h3>${icon('chat')} 向 AI 追问</h3>${button('解释当前笔记中的概念','ask-note','btn soft wide',`data-id="${n.id}"`)}</div></aside></div>`;
  }
  function quizzesPage(r) {
    const q=inSet(db.quizzes,r.setId), active=q.filter(ongoing).length, done=q.filter(x=>!ongoing(x)&&latest(x)).length;
    return head('课程测试','用练习检验理解，用结果找到下一步重点。',button('生成测试','generate-quiz','btn primary','','plus'))+`<div class="columns"><div><section class="panel toolbar">${tabs('quizTab',[['all',`全部测试 (${q.length})`],['active',`进行中 (${active})`],['done',`已完成 (${done})`],['mistakes','错题']],ui.quizTab)}${search('quizSearch','搜索测试名称',ui.quizSearch)}</section><div id="quiz-cards">${quizCards(r.setId)}</div></div><div class="stack">${panel('按需练习',`${button('选择资料生成','generate-quiz','quick-card','','file')}${button('复习错题','show-mistakes','quick-card','','target')}`)}${panel('学习建议',`<div class="suggestion"><span class="step">1</span><div><strong>先完成章节测试</strong><br><small>检验基础知识掌握情况</small></div></div><div class="suggestion"><span class="step">2</span><div><strong>再进行综合练习</strong><br><small>串联知识点，巩固理解</small></div></div><div class="suggestion"><span class="step">3</span><div><strong>最后复习错题</strong><br><small>针对薄弱点重点突破</small></div></div>`)}</div></div>`;
  }
  function quizCards(id) {
    if(ui.quizTab==='mistakes')return mistakesView(id);
    const quizzes=inSet(db.quizzes,id).filter(q=>q.title.includes(ui.quizSearch)&&(ui.quizTab==='all'||(ui.quizTab==='active'?!!ongoing(q):!ongoing(q)&&!!latest(q))));
    if(!quizzes.length)return empty('暂无匹配的测试','选择可用资料生成测试，或者调整筛选条件。',button('生成测试','generate-quiz','btn primary'));
    return `<section class="panel">${quizzes.map(q=>{const a=ongoing(q),last=latest(q);return `<article class="card quiz-card">${tile('file',!a&&last?'green':'')}<div><div class="row wrap"><h2>${esc(q.title)}</h2>${badge(q.type)}</div><p>围绕${esc(q.chapter)}的核心知识点，检验概念理解与应用。</p><div class="quiz-meta"><span>${icon('file')} ${q.questionIds.length} 题</span><span>${icon('clock')} 建议 ${q.durationMinutes} 分钟</span><span>${esc(q.chapter)}</span></div><small>${attemptsFor(q).filter(x=>x.status==='graded').length} 次已完成作答</small></div><div class="quiz-actions">${badge(a?'进行中':last?'已完成':'未开始',a?'orange':last?'green':'red')}${a?`<div><small>已作答 ${answered(a)} / ${q.questionIds.length} 题</small>${progress(Math.round(answered(a)/q.questionIds.length*100))}</div>`:last?`<div><small>最近得分 ${score(last)} / 100</small>${progress(score(last),'green')}</div>`:''}${a?link('继续作答',attemptUrl(a),'btn primary'):last?link('查看结果',attemptUrl(last),'btn soft'):button('开始测试','start','btn primary',`data-id="${q.id}"`)}<div class="actions">${button('查看试卷','preview-quiz','btn small',`data-id="${q.id}"`)}${button('导出打印','export','btn small')}</div></div></article>`;}).join('')}</section>`;
  }
  function mistakesView(setId) {
    const map=new Map();
    inSet(db.attempts,setId).filter(a=>a.status==='graded').forEach(a=>a.questionIds.forEach(id=>{const q=find(db.questions,id);if(a.answers[id]!==q.correct&&(!ui.knowledgeFilter||q.knowledgePointId===ui.knowledgeFilter))map.set(id,{q,a});}));
    return panel('错题复习',`${ui.knowledgeFilter?`<p class="muted">知识点：${esc(find(db.knowledgePoints,ui.knowledgeFilter)?.title)} ${button('清除筛选','clear-knowledge','link')}</p>`:''}${map.size?[...map.values()].map(({q,a},i)=>`<div class="review-row"><h3>${i+1}. ${esc(q.stem)}</h3><p class="muted">${esc(find(db.knowledgePoints,q.knowledgePointId)?.title)}</p>${link('查看作答与解析',attemptUrl(a),'btn small')}</div>`).join(''):'<p class="muted">暂无相关错题。完成测试后会在这里汇总。</p>'}`);
  }
  function elapsed(a) { return `${String(Math.floor(a.elapsedSeconds/60)).padStart(2,'0')}:${String(a.elapsedSeconds%60).padStart(2,'0')}`; }
  function attemptPage(r) {
    const a=find(db.attempts,r.attemptId),q=find(db.quizzes,r.itemId);
    if(!a||!q||a.studySetId!==r.setId||a.quizId!==q.id)return notFound();
    if(a.status==='graded')return resultPage(a,q);
    if(r.result)return empty('本次作答尚未交卷','完成作答并交卷后，才能查看结果。',link('继续作答',attemptUrl(a),'btn primary'));
    const index=a.currentIndex,current=find(db.questions,a.questionIds[index]);
    return `<div class="attempt-columns"><section class="panel question-panel"><div class="row between"><div class="row"><strong>第 ${index+1} 题</strong><span class="muted">/ 共 ${a.questionIds.length} 题</span>${badge('单选题')}</div>${button(a.markedQuestionIds.includes(current.id)?'已标记':'标记','mark','link',`data-id="${a.id}"`,'bookmark')}</div><h1 class="question-title">${esc(current.stem)}</h1><fieldset style="border:0;padding:0;margin:0"><legend class="muted" style="margin-bottom:10px;font-size:12px">选择一个答案</legend><div class="options">${current.options.map((option,i)=>`<label class="option"><input type="radio" name="answer" value="${i}" data-attempt="${a.id}" data-question="${current.id}" ${a.answers[current.id]===i?'checked':''}><strong>${letter(i)}.</strong><span>${esc(option)}</span></label>`).join('')}</div></fieldset><div class="question-footer">${button('上一题','question','btn',`data-index="${index-1}" data-id="${a.id}" ${index===0?'disabled':''}`,'back')}${button(index===a.questionIds.length-1?'检查并交卷':'下一题',index===a.questionIds.length-1?'submit-check':'question','btn primary',`data-index="${index+1}" data-id="${a.id}"`,'chevron')}</div></section><aside class="panel answer-panel"><h2>答题卡</h2><div class="legend"><span><i></i>已答</span><span><i class="current"></i>当前</span><span><i class="unanswered"></i>未答</span><span><i class="marked"></i>已标记</span></div><div class="answer-grid">${a.questionIds.map((qid,i)=>`<button data-action="question" data-id="${a.id}" data-index="${i}" class="${Number.isInteger(a.answers[qid])?'answered':''} ${index===i?'current':''}" aria-label="第${i+1}题，${Number.isInteger(a.answers[qid])?'已答':'未答'}${a.markedQuestionIds.includes(qid)?'，已标记':''}" ${index===i?'aria-current="step"':''}>${i+1}${a.markedQuestionIds.includes(qid)?'<span class="flag">⚑</span>':''}</button>`).join('')}</div><div class="answer-bottom"><div class="row between"><span class="muted">已完成 ${answered(a)} / ${a.questionIds.length} 题</span><small>${Math.round(answered(a)/a.questionIds.length*100)}%</small></div>${progress(Math.round(answered(a)/a.questionIds.length*100))}${button('交卷','submit-check','btn primary wide',`data-id="${a.id}" style="margin-top:24px"`)}<p class="muted" style="font-size:12px;margin-top:12px">答案仅保存在此浏览器。返回测试可继续本次作答。</p></div></aside></div>`;
  }
  function resultPage(a,q) {
    return head('测试结果',`${q.title} · ${fmt(a.submittedAt)} · 用时 ${elapsed(a)}`,`${link('返回测试',url(a.studySetId,'quizzes'),'btn','back')}${link('学习报告',url(a.studySetId,'report'),'btn primary','chart')}`)+`<div class="stat-grid"><section class="panel stat"><div class="score num">${score(a)}<small> / 100</small></div><div>本次得分<br><small>示例客观题评分</small></div></section><section class="panel stat">${tile('check','green')}<div><span class="muted">答对题目</span><b>${correct(a)} / ${a.questionIds.length}</b></div></section><section class="panel stat">${tile('file')}<div><span class="muted">未作答</span><b>${a.questionIds.length-answered(a)} 题</b></div></section></div><section class="panel"><div class="panel-head"><h2>逐题回顾与解析</h2>${button('再做一次','start','btn soft',`data-id="${q.id}"`)}</div>${a.questionIds.map((id,i)=>{const x=find(db.questions,id),ok=a.answers[id]===x.correct;return `<article class="review-row"><div class="row between"><h3>${i+1}. ${esc(x.stem)}</h3>${badge(ok?'正确':Number.isInteger(a.answers[id])?'错误':'未答',ok?'green':'red')}</div><p>你的答案：${letter(a.answers[id])} ${esc(x.options[a.answers[id]]||'')}<br>正确答案：${letter(x.correct)} ${esc(x.options[x.correct])}</p><div class="explanation">${esc(x.explanation)}${x.sourceRefIds.length?`<div>${sourceChips(x.sourceRefIds)}</div>`:'<br><small>本题为原型示例，未绑定原始资料页。</small>'}</div></article>`;}).join('')}</section>`;
  }
  function tutorPage(r) {
    const messages=db.messages.filter(x=>x.studySetId===r.setId||(!x.studySetId&&r.setId==='network')),scope=ui.tutorScope || inSet(db.materials,r.setId).filter(x=>x.status==='ready').map(x=>x.id);
    return `<div class="chat-layout"><div class="stack">${panel(`${esc(setBy(r.setId).name)} AI 辅导`,`<div class="row between wrap"><p class="muted">基于当前学习集资料答疑，支持生成笔记和测试。</p>${button(`当前范围：${scope.length} 份已就绪资料`,'scope','btn small','','folder')}</div>`)}<section class="panel chat-panel"><div class="messages" id="messages">${messages.length?messages.map(m=>`<div class="message ${m.role}">${m.role==='assistant'?`<span class="bot-avatar">${icon('bot')}</span>`:'<span class="avatar">林</span>'}<div class="bubble">${esc(m.content)}${m.sourceRefIds?.length?`<div>${sourceChips(m.sourceRefIds)}</div>`:''}${m.artifact?`<div class="artifact">${tile(m.artifact.type==='note'?'note':'file')}<div class="grow"><strong>${esc(m.artifact.title)}</strong><br><small>已保存到本学习集</small></div>${link('打开',m.artifact.route,'btn primary small')}</div>`:''}</div></div>`).join(''):`<div class="empty">${tile('bot')}<h2>从一个问题开始</h2><p>先上传资料，再围绕当前学习集提问。</p></div>`}</div><form id="chat-form" class="composer"><div class="actions">${button('生成笔记','generate-note','btn small','','note')}${button('生成测试','generate-quiz','btn small','','file')}</div><div class="composer-box"><textarea name="message" aria-label="向 AI 提问" placeholder="提问，或让 AI 帮你生成笔记和测试…" required maxlength="600"></textarea><button class="btn primary" type="submit" aria-label="发送问题">${icon('send')}</button></div><small>设计演示：回答使用本地预设内容。</small></form></section></div><aside class="stack">${panel('快捷操作',`${button('生成笔记','generate-note','quick-card','','note')}${button('生成测试','generate-quiz','quick-card','','file')}${button('总结知识点','summary','quick-card','','book')}`)}${panel('相关资料',sourceCards(db.sourceRefs.filter(s=>scope.includes(s.materialId)).map(s=>s.id)) || '<p class="muted">当前范围暂无可用资料。</p>')}${panel('最近提问',messages.filter(m=>m.role==='user').slice(-3).reverse().map(m=>`<p class="list-row">${esc(m.content)}</p>`).join('')||'<small>还没有提问记录</small>')}</aside></div>`;
  }
  function reportData(id) {
    const end=new Date(db.now),start=new Date(end);start.setDate(start.getDate()-ui.reportDays+1);start.setHours(0,0,0,0);
    const attempts=inSet(db.attempts,id).filter(a=>a.status==='graded'&&new Date(a.submittedAt)>=start&&new Date(a.submittedAt)<=end);
    const total=attempts.reduce((sum,a)=>sum+answered(a),0),right=attempts.reduce((sum,a)=>sum+correct(a),0);
    const stats=db.knowledgePoints.map(k=>{let count=0,hit=0;attempts.forEach(a=>a.questionIds.forEach(qid=>{const q=find(db.questions,qid);if(q.knowledgePointId===k.id&&Number.isInteger(a.answers[qid])){count++;if(a.answers[qid]===q.correct)hit++;}}));return {...k,count,hit,accuracy:count?Math.round(hit/count*100):null};});
    return {attempts,total,right,accuracy:total?Math.round(right/total*100):null,stats};
  }
  function reportPage(r) {
    const d=reportData(r.setId),weak=d.stats.filter(x=>x.count>0).sort((a,b)=>a.accuracy-b.accuracy)[0];
    return head('学习报告','根据在线测试与重练记录更新，找到薄弱点并安排下一步。',select('reportDays',[['7','最近 7 天'],['30','最近 30 天']],String(ui.reportDays)))+`<div class="stat-grid">${[['file','完成测试',`${d.attempts.length} 次`],['edit','作答次数',`${d.total} 次`],['chart','答题正确率',d.accuracy===null?'尚未检验':`${d.accuracy}%`]].map(([ico,title,value])=>`<section class="panel stat">${tile(ico)}<div><span class="muted">${title}</span><b class="num">${value}</b><small>基于已评分作答</small></div></section>`).join('')}</div><div class="report-grid"><div class="stack">${panel('各知识点测试表现',`<div class="knowledge-row muted"><span>知识点</span><span>正确率</span><span>作答数</span><span>表现</span></div>${d.stats.map(k=>`<div class="knowledge-row"><strong>${esc(k.title)}</strong>${k.accuracy===null?'<span class="muted">尚未检验</span>':progress(k.accuracy,k.accuracy<60?'orange':'')}<span class="muted">${k.count||'—'}</span><span class="num ${k.accuracy!==null&&k.accuracy<60?'danger':''}">${k.accuracy===null?'—':k.accuracy+'%'}</span></div>`).join('')}<small>作答数按记录累计，重复作答会再次计数。</small>`)}${panel('最近测试表现',trend(d.attempts))}</div><div class="stack">${panel('建议优先复习',weak?`<div class="tip"><div class="row">${tile('target','orange')}<div><h2>${esc(weak.title)}</h2>${badge('建议加强','orange')}</div></div><p class="muted" style="margin:16px 0">最近 ${weak.count} 次相关作答中答对 ${weak.hit} 次，正确率 ${weak.accuracy}%。</p>${button('查看相关错题','knowledge-mistakes','btn primary wide',`data-id="${weak.id}"`,'book')}${button('生成针对性练习','targeted-quiz','btn wide',`data-id="${weak.id}" style="margin-top:10px"`,'file')}</div>`:'<p class="muted">完成一次测试后，会根据真实作答生成建议。</p>')}${panel('学习建议',`<div class="suggestion"><span class="step">1</span><div><strong>先复习已发现的薄弱点</strong><br><small>结合错题与资料，解释每个失分原因。</small></div></div><div class="suggestion"><span class="step">2</span><div><strong>再补充尚未检验的范围</strong><br><small>没有作答数据不代表已经掌握。</small></div></div><div class="suggestion"><span class="step">3</span><div><strong>带着问题继续练习</strong><br><small>比较多次作答，观察理解是否稳定。</small></div></div>`)}</div></div>`;
  }
  function trend(attempts) {
    const sorted=[...attempts].sort((a,b)=>a.submittedAt.localeCompare(b.submittedAt));if(!sorted.length)return '<p class="muted">暂无已评分测试记录。</p>';
    const daily=new Map();sorted.forEach(a=>{const key=localDate(a.submittedAt),v=daily.get(key)||{total:0,hit:0};v.total+=answered(a);v.hit+=correct(a);daily.set(key,v);});
    const data=[...daily].map(([date,x])=>({date,value:Math.round(x.hit/x.total*100)})),x=i=>65+(data.length===1?190:i/(data.length-1)*380),y=v=>135-v;
    return `<svg class="chart" viewBox="0 0 500 175" role="img" aria-label="每日测试正确率，${data.map(x=>`${fmt(x.date)} ${x.value}%`).join('，')}">${[0,50,100].map(v=>`<line x1="55" x2="460" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="16" y="${y(v)+4}">${v}%</text>`).join('')}<polyline points="${data.map((d,i)=>`${x(i)},${y(d.value)}`).join(' ')}" fill="none" stroke="var(--purple-mid)" stroke-width="2"/>${data.map((d,i)=>`<circle cx="${x(i)}" cy="${y(d.value)}" r="4.5" fill="var(--purple-mid)"/><text x="${x(i)}" y="${y(d.value)-11}" text-anchor="middle">${d.value}%</text><text x="${x(i)}" y="158" text-anchor="middle">${fmt(d.date)}</text>`).join('')}</svg>`;
  }
  function calendar() {
    const [year,month]=ui.calendarMonth.split('-').map(Number),first=new Date(year,month-1,1),offset=(first.getDay()+6)%7;
    let start=new Date(year,month-1,1-offset),days=42;
    if(ui.calendarView==='week'){start=new Date(ui.calendarDate+'T12:00:00');start.setDate(start.getDate()-((start.getDay()+6)%7));days=7;}
    const key=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const selected=db.events.filter(e=>localDate(e.startsAt)===ui.calendarDate).sort((a,b)=>a.startsAt.localeCompare(b.startsAt));
    return head('复习日历','合理安排每周的学习任务，保持稳定的复习节奏。',button('添加安排','new-event','btn primary','','plus'))+`<div class="calendar-layout"><section class="panel"><div class="calendar-controls"><div class="row">${button('‹','month-prev','btn small','aria-label="上一时段"')}<h2>${year}年${month}月</h2>${button('›','month-next','btn small','aria-label="下一时段"')}${button('今天','calendar-today','btn small')}</div>${tabs('calendarView',[['month','月'],['week','周']],ui.calendarView)}</div><div class="month-grid ${ui.calendarView==='week'?'week':''}">${['周一','周二','周三','周四','周五','周六','周日'].map(t=>`<div class="weekday">${t}</div>`).join('')}${Array.from({length:days},(_,i)=>{const d=new Date(start);d.setDate(start.getDate()+i);const date=key(d),events=db.events.filter(e=>localDate(e.startsAt)===date);return `<button class="day ${d.getMonth()!==month-1?'other':''} ${date===ui.calendarDate?'selected':''} ${date==='2026-10-25'?'today':''}" data-action="date" data-date="${date}" aria-label="${date}，${events.length}项安排" aria-pressed="${date===ui.calendarDate}"><span class="date-number">${d.getDate()}</span>${events.slice(0,2).map(e=>`<span class="event-chip ${setBy(e.studySetId)?.color||''}">${esc(setBy(e.studySetId)?.name)}<br>${esc(e.title)}</span>`).join('')}${events.length>2?`<small>另 ${events.length-2} 项</small>`:''}</button>`;}).join('')}</div></section><aside class="stack">${panel(`${fmt(ui.calendarDate)} · ${new Date(ui.calendarDate+'T12:00:00').toLocaleDateString('zh-CN',{weekday:'long'})}`,`<p>${selected.length} 项复习安排</p>${selected.length?selected.map(e=>`<article class="day-agenda"><div class="row between"><small>${time(e.startsAt)}–${time(e.endsAt)}</small>${button('打开','event-open','btn small',`data-id="${e.id}"`)}</div><strong>${esc(setBy(e.studySetId)?.name)}</strong><p>${esc(e.title)}</p></article>`).join(''):'<p class="muted" style="margin-top:20px">这一天还没有安排。</p>'}`)}${panel('下一场考试',`<div class="exam-box"><h3>计算机网络期末考试</h3><p>2026年11月6日 · 周五</p><div>还有 <span class="countdown">${daysUntil(db.exams[0].startsAt)}</span> 天</div></div>`)}${panel('近期安排',db.events.filter(e=>e.startsAt>=db.now).sort((a,b)=>a.startsAt.localeCompare(b.startsAt)).slice(0,4).map(e=>`<div class="list-row"><small>${fmt(e.startsAt)}</small> ${button(e.title,'event-open','link',`data-id="${e.id}"`)}</div>`).join(''))}</aside></div>`;
  }
  function showDialog(title,body,actions='') {
    if(dialog.open)dialog.close();
    dialog.innerHTML=`<div class="dialog-head"><h2 id="dialog-title">${esc(title)}</h2><button class="icon-button" data-close aria-label="关闭">${icon('close')}</button></div>${body}${actions?`<div class="dialog-actions">${actions}</div>`:''}`;
    dialog.showModal();
  }
  const closeButton = '<button class="btn" data-close>取消</button>';
  function materialDialog(id,refId) {
    const m=find(db.materials,id),ref=refId&&find(db.sourceRefs,refId);if(!m)return;
    showDialog(m.title,`<div class="row"><span class="file-icon ${m.fileType}">${m.fileType}</span><span>${esc(m.sourceType)} · ${esc(m.chapter)}</span>${badge(m.status==='ready'?'已就绪':m.status==='failed'?'处理失败':'处理中',m.status==='ready'?'green':'orange')}</div><div class="tip" style="margin-top:20px"><h3>${ref?`第 ${ref.pageStart}–${ref.pageEnd} 页`:'资料内容示例'}</h3><p>${esc(ref?.quote || db.sourceRefs.find(s=>s.materialId===m.id)?.quote || '此文件尚未处理完成，暂时没有可使用的文本。')}</p></div><p>原型展示引用摘录。原文件逐页预览将在正式界面接入。</p>`,button('完成','close','btn primary'));
  }
  function generation(kind,scopeOnly=false) {
    const r=route(),id=r.setId||ui.currentSetId||'network',all=inSet(db.materials,id),chosen=ui.selectedMaterials.size?[...ui.selectedMaterials]:(ui.tutorScope||all.filter(m=>m.status==='ready').map(m=>m.id));
    const ready=all.filter(m=>m.status==='ready');
    if(!ready.length){showDialog('需要先上传资料','<p>这个学习集还没有已就绪资料。上传并完成处理后，就能生成内容。</p>',link('前往资料页',url(id,'materials'),'btn primary'));return;}
    const title=scopeOnly?'选择资料范围':kind==='note'?'生成笔记':'生成测试';
    showDialog(title,`<form id="generation-form" data-kind="${scopeOnly?'scope':kind}" data-set="${id}"><p>明确本次使用的资料。处理中或失败的资料需先处理。</p><div class="scope-options">${all.map(m=>`<label><input name="materials" type="checkbox" value="${m.id}" ${chosen.includes(m.id)&&m.status==='ready'?'checked':''} ${m.status!=='ready'?'disabled':''}><span>${esc(m.title)}</span>${badge(m.status==='ready'?'已就绪':m.status==='processing'?'处理中':'处理失败',m.status==='ready'?'green':'orange')}</label>`).join('')}</div>${scopeOnly?'':`<label class="field">标题<input name="title" required maxlength="80" value="${kind==='note'?'传输层重点整理':'传输层巩固练习'}"></label>${kind==='note'?'<label class="field">笔记类型<select name="type"><option>考前冲刺</option><option>知识整理</option><option>考点清单</option></select></label>':'<label class="field">题量<select name="count"><option value="5">5 题</option><option value="10">10 题</option></select></label><small>原型使用预置传输层题库演示，不进行真实 AI 生成。</small>'}`}<p id="generation-error" class="danger" role="alert"></p><div class="dialog-actions">${closeButton}<button class="btn primary" type="submit">${scopeOnly?'应用范围':'模拟生成'}</button></div></form>`);
  }
  function createAttempt(quizId) {
    const quiz=find(db.quizzes,quizId);if(!quiz)return;
    let a=ongoing(quiz);
    if(!a){a={id:`attempt-${Date.now()}`,studySetId:quiz.studySetId,quizId,quizRevision:quiz.revision,questionIds:[...quiz.questionIds],status:'in_progress',answers:{},markedQuestionIds:[],currentIndex:0,elapsedSeconds:0,startedAt:db.now};db.attempts.push(a);}
    persist();go(attemptUrl(a));
  }
  function handle(action,el) {
    const r=route(),id=el.dataset.id;
    if(action==='close'){dialog.close();return;}
    if(action==='mobile-menu'){ui.menu=!ui.menu;refreshSidebar();document.querySelector('.sidebar-toggle')?.focus({preventScroll:true});return;}
    if(action==='toggle-sidebar'){
      if(matchMedia('(max-width:760px)').matches){ui.menu=false;refreshSidebar();document.querySelector('[data-action="mobile-menu"]')?.focus({preventScroll:true});}
      else{ui.sidebarCollapsed=!ui.sidebarCollapsed;persist();refreshSidebar();document.querySelector('.sidebar-toggle')?.focus({preventScroll:true});}
      return;
    }
    if(action==='tab'){ui[el.dataset.group]=el.dataset.value;render();return;}
    if(action==='new-set'){showDialog('新建学习集','<form id="set-form"><label class="field">学习集名称<input name="name" required maxlength="60" placeholder="例如：操作系统" autofocus></label><label class="field">简介（可选）<input name="description" maxlength="120" placeholder="准备怎样复习这门课程？"></label><div class="dialog-actions">'+closeButton+'<button class="btn primary">创建学习集</button></div></form>');return;}
    if(action==='switch-set'){showDialog('切换学习集',db.studySets.map(s=>`<div class="list-row">${link(s.name,url(s.id,'materials'),'btn wide')}</div>`).join(''));return;}
    if(action==='set-info'){const s=setBy(id);showDialog(s.name,`<p>${esc(s.description)}</p><p>状态：${s.status==='archived'?'已归档（演示只读内容）':'进行中'}</p><p>正式版在此补充重命名、归档与考试信息。</p>`,link('进入学习集',url(id,'materials'),'btn primary'));return;}
    if(action==='material'){materialDialog(id);return;}
    if(action==='source'){const ref=find(db.sourceRefs,id);if(ref)materialDialog(ref.materialId,id);return;}
    if(action==='upload'){showDialog('上传资料','<p>本轮用于确认页面和跳转。真实文件选择、上传、解析和取消流程尚未接入。</p><p>计算机网络示例已展示已就绪、处理中和失败三种状态，可选择资料体验生成流程。</p>',button('知道了','close','btn primary'));return;}
    if(action==='retry'){const m=find(db.materials,id);m.status='ready';delete m.error;persist();render();toast('模拟重试完成，资料已就绪');return;}
    if(action==='generate-note'||action==='generate-quiz'){generation(action==='generate-note'?'note':'quiz');return;}
    if(action==='scope'){generation('note',true);return;}
    if(action==='ask-selected'){ui.tutorScope=[...ui.selectedMaterials];go(url(r.setId,'tutor'));return;}
    if(action==='ask-note'){const n=find(db.notes,id);ui.tutorScope=n.sourceRefIds.map(s=>find(db.sourceRefs,s).materialId);go(url(n.studySetId,'tutor'));return;}
    if(action==='edit-note'||action==='export'){showDialog(action==='edit-note'?'编辑笔记':'导出与打印',`<p>${action==='edit-note'?'此处后续接入笔记编辑、来源修改、版本复核与确认。':'此处后续提供笔记导出，以及测试的仅题目 / 题目加答案解析两种版本。'}</p><p>当前为导航原型，尚未接入此操作。</p>`,button('知道了','close','btn primary'));return;}
    if(action==='start'){const q=find(db.quizzes,id);showDialog('开始测试',`<p>${esc(q.title)} · ${q.questionIds.length} 题 · 建议 ${q.durationMinutes} 分钟</p><p>开始后创建独立的作答记录。返回列表可继续本次作答。</p>`,closeButton+button('开始作答','start-confirm','btn primary',`data-id="${id}"`));return;}
    if(action==='start-confirm'){dialog.close();createAttempt(id);return;}
    if(action==='preview-quiz'){const q=find(db.quizzes,id);showDialog(q.title,`<p>试卷预览 · 不展示答案，不开始计时。</p>${q.questionIds.map((qid,i)=>{const q=find(db.questions,qid);return `<div class="review-row"><strong>${i+1}. ${esc(q.stem)}</strong><p>${q.options.map((o,j)=>`${letter(j)}. ${esc(o)}`).join('<br>')}</p></div>`;}).join('')}`,button('开始测试','start','btn primary',`data-id="${id}"`));return;}
    if(action==='question'){const a=find(db.attempts,id),index=Number(el.dataset.index);if(a&&index>=0&&index<a.questionIds.length){a.currentIndex=index;persist();render();}return;}
    if(action==='mark'){const a=find(db.attempts,id),qid=a.questionIds[a.currentIndex];a.markedQuestionIds=a.markedQuestionIds.includes(qid)?a.markedQuestionIds.filter(x=>x!==qid):[...a.markedQuestionIds,qid];persist();render();return;}
    if(action==='submit-check'){const a=find(db.attempts,id);if(!a||a.status!=='in_progress')return;const remain=a.questionIds.length-answered(a);showDialog('确认交卷',`<p>${remain?`还有 ${remain} 题未作答，未答题将按 0 分计算。`:'已完成全部题目。'}</p><p>交卷后本次作答不可修改，可查看解析或重新练习。</p>`,closeButton+button('确认交卷','submit','btn primary',`data-id="${id}"`));return;}
    if(action==='submit'){const a=find(db.attempts,id);if(!a||a.status!=='in_progress')return;a.status='graded';a.submittedAt=db.now;persist();dialog.close();go(attemptUrl(a));return;}
    if(action==='show-mistakes'||action==='knowledge-mistakes'){ui.quizTab='mistakes';ui.knowledgeFilter=action==='knowledge-mistakes'?id:null;go(url(r.setId,'quizzes'));return;}
    if(action==='clear-knowledge'){ui.knowledgeFilter=null;render();return;}
    if(action==='targeted-quiz'){ui.knowledgeFilter=id;showDialog('针对性练习',`<p>目标知识点：${esc(find(db.knowledgePoints,id)?.title)}</p><p>正式版会把知识点作为生成约束。当前原型提供既有错题入口，避免把无关题库标为针对性结果。</p>`,button('查看相关错题','knowledge-mistakes','btn primary',`data-id="${id}"`));return;}
    if(action==='summary'){addChat('请总结传输层的核心知识点。','先抓住进程通信、端口、TCP/UDP、可靠传输、流量控制与拥塞控制六个主题，再按笔记目录逐项复习。');return;}
    if(action==='date'){ui.calendarDate=el.dataset.date;render();return;}
    if(action==='calendar-today'){ui.calendarDate='2026-10-25';ui.calendarMonth='2026-10';render();return;}
    if(action==='month-prev'||action==='month-next'){const delta=action==='month-next'?1:-1;if(ui.calendarView==='week'){const d=new Date(ui.calendarDate+'T12:00:00');d.setDate(d.getDate()+delta*7);ui.calendarDate=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;ui.calendarMonth=ui.calendarDate.slice(0,7);}else{const [y,m]=ui.calendarMonth.split('-').map(Number),d=new Date(y,m-1+delta,1);ui.calendarMonth=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;}render();return;}
    if(action==='new-event'){showDialog('添加复习安排',`<form id="event-form"><label class="field">学习集<select name="setId">${db.studySets.filter(s=>s.status!=='archived').map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></label><label class="field">安排标题<input name="title" required maxlength="80" placeholder="例如：复习第三章"></label><label class="field">日期<input name="date" type="date" value="${ui.calendarDate}" required></label><div class="two"><label class="field">开始时间<input name="start" type="time" value="09:00" required></label><label class="field">结束时间<input name="end" type="time" value="10:00" required></label></div><p id="event-error" role="alert" class="danger"></p><div class="dialog-actions">${closeButton}<button class="btn primary">保存安排</button></div></form>`);return;}
    if(action==='event-open'){const e=find(db.events,id);if(e.target.type==='note')go(`${url(e.studySetId,'notes')}/${e.target.id}`);else if(e.target.type==='quiz')go(url(e.studySetId,'quizzes'));else if(e.target.type==='studySet')go(url(e.studySetId,'materials'));else{const exam=find(db.exams,e.target.id);showDialog(exam.title,`<p>考试时间：${fmt(exam.startsAt)} ${time(exam.startsAt)}</p><p>所属学习集：${esc(setBy(exam.studySetId).name)}</p>`,link('进入学习集',url(exam.studySetId,'materials'),'btn primary'));}return;}
    if(action==='reset'){showDialog('重置演示','<p>清除本原型中新建的学习集、生成内容、作答修改和日历安排，恢复初始示例。</p>',closeButton+button('重置示例','reset-confirm','btn primary'));return;}
    if(action==='reset-confirm'){db=structuredClone(original);try{localStorage.removeItem(storageKey);}catch(_){}ui.currentSetId=null;ui.expanded=false;ui.selectedMaterials.clear();ui.setSearch='';ui.setTab='all';ui.quizTab='all';ui.quizSearch='';ui.tutorScope=null;ui.materialSearch='';ui.materialChapter='all';ui.materialSource='all';ui.noteSearch='';ui.noteType='all';ui.knowledgeFilter=null;ui.calendarMonth='2026-10';ui.calendarDate='2026-10-25';ui.calendarView='month';dialog.close();go('/home');return;}
    if(action==='help'||action==='settings'||action==='notifications'){showDialog(({help:'帮助中心',settings:'个人设置',notifications:'通知'})[action],`<p>${({help:'推荐体验路径：主页 → 我的学习集 → 计算机网络 → 资料 → 笔记 → 测试 → 答题 → 结果 → 学习报告。日历可独立进入。',settings:'林同学 · 个人学习工作台。头像、账户与学习偏好将在正式设计中补充。',notifications:'当前为本地设计示例，尚未接入真实通知。'})[action]}</p>`,button('知道了','close','btn primary'));return;}
  }
  function addChat(question,reply) {
    const r=route(),scope=ui.tutorScope || inSet(db.materials,r.setId).filter(m=>m.status==='ready').map(m=>m.id);
    const refs=db.sourceRefs.filter(s=>scope.includes(s.materialId)).map(s=>s.id);
    db.messages.push({role:'user',studySetId:r.setId,content:question},{role:'assistant',studySetId:r.setId,content:refs.length?reply:'当前学习集没有可用资料。请先上传并完成资料处理，再围绕资料提问。',sourceRefIds:refs});persist();render();document.getElementById('messages')?.lastElementChild?.scrollIntoView({block:'nearest'});
  }
  document.addEventListener('click',event=>{
    const section=event.target.closest('[data-section]');if(section){event.preventDefault();document.getElementById('section-'+section.dataset.section)?.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});document.querySelectorAll('[data-section]').forEach(x=>x.classList.toggle('current',x===section));return;}
    const close=event.target.closest('[data-close]');if(close){event.preventDefault();dialog.close();return;}
    const action=event.target.closest('[data-action]');if(action){event.preventDefault();handle(action.dataset.action,action);return;}
    if(event.target.closest('a[href^="#/"]')){if(dialog.open)dialog.close();ui.menu=false;}
  });
  document.addEventListener('input',event=>{
    const name=event.target.dataset.input;if(!name)return;ui[name]=event.target.value;
    const r=route(),targets={setSearch:['set-cards',setCards],noteSearch:['note-cards',()=>noteCards(r.setId)],quizSearch:['quiz-cards',()=>quizCards(r.setId)],materialSearch:['material-table',()=>materialTable(r.setId)]},target=targets[name];
    if(target)document.getElementById(target[0]).innerHTML=target[1]();
  });
  document.addEventListener('change',event=>{
    const el=event.target;
    if(el.dataset.select){ui[el.dataset.select]=el.dataset.select==='reportDays'?Number(el.value):el.value;render();}
    if(el.dataset.material){el.checked?ui.selectedMaterials.add(el.dataset.material):ui.selectedMaterials.delete(el.dataset.material);document.getElementById('material-table').innerHTML=materialTable(route().setId);document.getElementById('batch').innerHTML=batchActions(route().setId);}
    if(el.dataset.attempt){const a=find(db.attempts,el.dataset.attempt);if(a?.status==='in_progress'){a.answers[el.dataset.question]=Number(el.value);persist();render();}}
  });
  document.addEventListener('submit',event=>{
    event.preventDefault();const form=event.target,values=new FormData(form),r=route();
    if(form.id==='set-form'){const name=values.get('name').trim();if(!name)return;const s={id:`set-${Date.now()}`,name,description:values.get('description').trim()||'新的学习空间',icon:'book',color:'purple',status:'active',lastVisitedAt:db.now};db.studySets.push(s);persist();dialog.close();go(url(s.id,'materials'));toast('学习集已创建');}
    if(form.id==='generation-form'){
      const selected=values.getAll('materials');if(!selected.length){document.getElementById('generation-error').textContent='请至少选择一份已就绪资料。';return;}
      const kind=form.dataset.kind,setId=form.dataset.set;
      if(kind==='scope'){ui.tutorScope=selected;dialog.close();render();return;}
      const title=String(values.get('title')||'').trim();if(!title)return;
      let artifact;
      if(kind==='note'){const n={...structuredClone(original.notes[0]),id:`note-${Date.now()}`,studySetId:setId,title,type:values.get('type'),status:'draft',sourceRefIds:db.sourceRefs.filter(s=>selected.includes(s.materialId)).map(s=>s.id),updatedAt:db.now};db.notes.push(n);artifact={type:'note',title,route:`${url(setId,'notes')}/${n.id}`};}
      else{const q={...structuredClone(original.quizzes[0]),id:`quiz-${Date.now()}`,studySetId:setId,title,questionIds:original.quizzes[0].questionIds.slice(0,Number(values.get('count'))),materialIds:selected};db.quizzes.push(q);artifact={type:'quiz',title,route:url(setId,'quizzes')};}
      db.messages.push({role:'assistant',studySetId:setId,content:'已在本地模拟生成，并保存到相应列表。',artifact});persist();dialog.close();ui.quizTab='all';ui.quizSearch='';ui.noteSearch='';ui.noteType='all';
      if(r.page==='tutor'){render();toast('已模拟生成，可从成果卡打开');}else{go(artifact.route);toast('已模拟生成，使用预置示例内容');}
    }
    if(form.id==='chat-form'){const question=String(values.get('message')||'').trim();if(!question)return;if(/生成.*笔记|整理.*笔记/.test(question)){generation('note');return;}if(/生成.*测试|出.*题/.test(question)){generation('quiz');return;}addChat(question,/TCP|握手|UDP/i.test(question)?original.messages[1].content:'可以从“复习框架 → 核心概念 → 典型考法 → 考前自查”整理这部分内容。下面的资料入口可以回看来源。此回复为设计原型中的预设示例。');}
    if(form.id==='event-form'){const start=String(values.get('start')),end=String(values.get('end'));if(end<=start){document.getElementById('event-error').textContent='结束时间需要晚于开始时间。';return;}const title=String(values.get('title')).trim();if(!title)return;const setId=String(values.get('setId')),date=String(values.get('date'));db.events.push({id:`event-${Date.now()}`,studySetId:setId,title,kind:'review',startsAt:`${date}T${start}:00+08:00`,endsAt:`${date}T${end}:00+08:00`,target:{type:'studySet',id:setId}});ui.calendarDate=date;ui.calendarMonth=date.slice(0,7);persist();dialog.close();render();toast('安排已保存');}
  });
  dialog.addEventListener('click',e=>{if(e.target===dialog){const rect=dialog.getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)dialog.close();}});
  window.addEventListener('hashchange',()=>{if(dialog.open)dialog.close();render();document.getElementById('main-content')?.scrollTo(0,0);});
  window.addEventListener('beforeunload',persist);
  matchMedia('(max-width:760px)').addEventListener('change',()=>{ui.menu=false;refreshSidebar();});
  setInterval(()=>{const r=route(),a=find(db.attempts,r.attemptId);if(a?.status==='in_progress'&&!r.result){a.elapsedSeconds++;const el=document.getElementById('elapsed');if(el)el.textContent=elapsed(a);if(a.elapsedSeconds%10===0)persist();}},1000);
  render();
})();
