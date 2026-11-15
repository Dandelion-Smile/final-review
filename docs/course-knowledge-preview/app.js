(() => {
  'use strict';
  const data = window.COURSE_PREVIEW;
  const $ = selector => document.querySelector(selector);
  const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  const icon = (name, cls = '') => `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const total = data.chapters.reduce((sum, chapter) => sum + chapter.points.length, 0);
  const expanded = new Set([0, 1]);
  const tree = $('#knowledge-tree');
  const search = $('#knowledge-search');
  const dialog = $('#preview-dialog');
  const labels = { notes: '课程笔记', materials: '课程资料', tests: '课程测试' };
  let toastTimer;
  $('#material-count').textContent = data.materials.length;
  $('#tree-total').textContent = `${data.chapters.length} 章 · ${total} 个知识点`;
  for (const kind of Object.keys(labels)) $(`#${kind}-count`).textContent = data[kind].length;

  function highlight(text, query) {
    if (!query) return escape(text);
    const index = text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
    if (index < 0) return escape(text);
    return `${escape(text.slice(0, index))}<mark>${escape(text.slice(index, index + query.length))}</mark>${escape(text.slice(index + query.length))}`;
  }

  function renderTree() {
    const query = search.value.trim();
    let shownPoints = 0;
    let shownChapters = 0;
    tree.innerHTML = data.chapters.map((chapter, chapterIndex) => {
      const chapterMatches = chapter.title.toLocaleLowerCase().includes(query.toLocaleLowerCase());
      const points = chapter.points.map((point, pointIndex) => ({ ...point, pointIndex }))
        .filter(point => !query || chapterMatches || point.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
      if (!points.length) return '';
      shownChapters++;
      shownPoints += points.length;
      return `<details class="chapter" data-chapter="${chapterIndex}" ${query || expanded.has(chapterIndex) ? 'open' : ''}>
        <summary>${icon('chevron', 'chapter-chevron')}<span class="chapter-number">${String(chapterIndex + 1).padStart(2, '0')}</span><span class="chapter-name">${highlight(chapter.title, query)}</span><span class="chapter-count">${query ? points.length : chapter.points.length} 个知识点</span></summary>
        <div class="chapter-body">${points.map(point => `<button class="knowledge-row" data-point="${chapterIndex}:${point.pointIndex}" aria-label="${escape(point.title)}，查看知识点来源"><span class="knowledge-name">${highlight(point.title, query)}</span>${point.important ? '<span class="point-tag">重点</span>' : ''}<span class="source-label">课件 · p.${escape(point.page)}</span>${icon('chevron', 'row-arrow')}</button>`).join('')}</div>
      </details>`;
    }).join('');
    if (!shownChapters) tree.innerHTML = '<div class="empty-search">没有找到相关章节或知识点<br>试试搜索「HTTP」「Servlet」或「会话」</div>';
    $('#search-status').hidden = !query;
    $('#search-status').textContent = `找到 ${shownChapters} 个章节中的 ${shownPoints} 个知识点`;
    tree.querySelectorAll('details').forEach(details => details.addEventListener('toggle', () => {
      if (search.value.trim()) return;
      const index = Number(details.dataset.chapter);
      if (details.open) expanded.add(index); else expanded.delete(index);
    }));
  }

  function resourceRow(kind, item, index) {
    const fileClass = item.type ? `file-icon ${item.type.toLowerCase()}` : '';
    const resourceIcon = kind === 'materials' ? escape(item.type) : icon(kind === 'notes' ? 'note' : 'pen');
    const status = kind === 'materials' ? '<span class="status">已解析</span>' : kind === 'tests' ? `<span class="status ${item.status === '待继续' ? 'pending' : ''}">${escape(item.status)}</span>` : '';
    const end = kind === 'tests' ? (item.score !== undefined ? `<div class="test-score">${item.score}<span>分</span></div>` : '<span class="test-continue">继续作答 ›</span>') : '';
    return `<button class="resource-item" data-resource="${kind}:${index}"><span class="resource-icon ${fileClass}">${resourceIcon}</span><span class="resource-info"><span class="resource-title">${escape(item.title)}</span><span class="resource-meta">${status}<span>${escape(item.meta)}</span></span></span>${end}</button>`;
  }

  function showDialog(eyebrow, title, body) {
    $('#dialog-eyebrow').textContent = eyebrow;
    $('#dialog-title').textContent = title;
    $('#dialog-body').innerHTML = body;
    if (!dialog.open) dialog.showModal();
    dialog.scrollTop = 0;
  }

  function showPoint(chapterIndex, pointIndex) {
    const chapter = data.chapters[chapterIndex];
    const point = chapter.points[pointIndex];
    const material = data.materials.find(item => item.id === chapter.material);
    showDialog('知识点预览', point.title, `<p class="detail-chapter">第 ${chapterIndex + 1} 章 · ${escape(chapter.title)}</p><div class="detail-label">知识点概述</div><p class="detail-description">${escape(point.description)}</p><div class="detail-label">来源资料</div><div class="detail-source">${icon('folder')}<div>${escape(material.title)}<small>第 ${escape(point.page)} 页 · 已解析入库</small></div></div>`);
  }

  function showModule(kind) {
    showDialog('Web服务端技术原理及应用', `${labels[kind]} · ${data[kind].length}`, data[kind].map((item, index) => resourceRow(kind, item, index)).join(''));
  }

  function showResource(kind, index) {
    const item = data[kind][index];
    showDialog(labels[kind], item.title, `<p class="detail-chapter">${escape(item.meta)}${item.status ? ` · ${escape(item.status)}` : ''}</p><div class="detail-label">${kind === 'notes' ? '笔记摘要' : kind === 'materials' ? '资料内容' : '测试概况'}</div><p class="detail-description">${escape(item.description)}</p>${kind === 'tests' && item.score !== undefined ? `<div class="detail-source">本次得分 <strong>${item.score} 分</strong></div>` : ''}`);
  }

  function toast(message) {
    clearTimeout(toastTimer);
    $('#toast').textContent = message;
    $('#toast').hidden = false;
    toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4000);
  }

  function closeMenu() {
    document.body.classList.remove('menu-open');
    $('#menu-backdrop').hidden = true;
    $('#mobile-menu').setAttribute('aria-expanded', 'false');
  }

  document.addEventListener('click', event => {
    const point = event.target.closest('[data-point]');
    if (point) return showPoint(...point.dataset.point.split(':').map(Number));
    const resource = event.target.closest('[data-resource]');
    if (resource) {
      const [kind, index] = resource.dataset.resource.split(':');
      return showResource(kind, Number(index));
    }
    const moduleButton = event.target.closest('[data-module]');
    if (moduleButton) { closeMenu(); return showModule(moduleButton.dataset.module); }
    const globalButton = event.target.closest('[data-global]');
    if (globalButton) { closeMenu(); toast(`当前为课程总览设计预览，「${globalButton.dataset.global}」将在正式接入时跳转。`); }
  });
  search.addEventListener('input', renderTree);
  $('#expand-all').addEventListener('click', () => { data.chapters.forEach((_, index) => expanded.add(index)); tree.querySelectorAll('details').forEach(item => { item.open = true; }); });
  $('#collapse-all').addEventListener('click', () => { expanded.clear(); tree.querySelectorAll('details').forEach(item => { item.open = false; }); });
  $('#close-dialog').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
  });
  $('#sidebar-toggle').addEventListener('click', () => {
    const collapsed = document.body.classList.toggle('collapsed');
    $('#sidebar-toggle').setAttribute('aria-label', collapsed ? '展开导航栏' : '收起导航栏');
  });
  $('#mobile-menu').setAttribute('aria-expanded', 'false');
  $('#mobile-menu').addEventListener('click', () => {
    const open = document.body.classList.toggle('menu-open');
    $('#menu-backdrop').hidden = !open;
    $('#mobile-menu').setAttribute('aria-expanded', String(open));
  });
  $('#menu-backdrop').addEventListener('click', closeMenu);
  $('#course-link').addEventListener('click', () => {
    closeMenu();
    if (dialog.open) dialog.close();
    search.value = '';
    renderTree();
    window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeMenu();
    if (event.key === '/' && !dialog.open && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) { event.preventDefault(); search.focus(); }
  });
  for (const kind of Object.keys(labels)) $(`#${kind}-list`).innerHTML = data[kind].slice(0, 2).map((item, index) => resourceRow(kind, item, index)).join('');
  renderTree();
})();
