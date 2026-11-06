import { useRef, useState } from "react";
import { Modal } from "antd";
import type { Course } from "./workbench-api";
import { mastery, profileFor, reportProfiles, sessions, type ReportProfile, type ReportSession } from "./report-data";
import reportCss from "./report.css?inline";
import "./report.css";

type IconName = "arrow" | "clock" | "check" | "book" | "target" | "download" | "trend";
function Icon({ name }: { name: IconName }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === "arrow" && <path d="M5 12h14m-5-5 5 5-5 5" />}
    {name === "clock" && <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3 2" /></>}
    {name === "check" && <><circle cx="12" cy="12" r="8.5" /><path d="m8 12 3 3 5-6" /></>}
    {name === "book" && <><path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14" /></>}
    {name === "target" && <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></>}
    {name === "download" && <><path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" /></>}
    {name === "trend" && <><path d="m3 17 6-6 4 3 8-9m-6 0h6v6" /></>}
  </svg>;
}
const score = (s: ReportSession) => Math.round(s.correct / s.questions * 100);
const sum = (rows: ReportSession[], field: "questions" | "correct" | "minutes") => rows.reduce((n, s) => n + s[field], 0);
const accuracy = (rows: ReportSession[]) => Math.round(sum(rows, "correct") / Math.max(1, sum(rows, "questions")) * 100);
const sessionTitle = (profile: ReportProfile, row: ReportSession) => `${profile.chapters[sessions.indexOf(row) % profile.chapters.length]} · 专项测验`;

export default function LearningReport({ courses, course, onCourse }: { courses: Course[]; course: Course | null; onCourse: (course: Course) => void }) {
  const [previewCourse, setPreviewCourse] = useState(reportProfiles[0].name);
  const available = courses.filter(c => c.status !== "deleted");
  const selected = available.find(c => c.course_id === course?.course_id) ?? available[0];
  const name = selected?.name ?? previewCourse;
  return <ReportView key={name} profile={profileFor(name)} courseName={name} picker={
    <label className="lr-course-picker"><Icon name="book" /><span className="lr-sr-only">报告课程</span>
      <select aria-label="报告课程" value={selected?.course_id ?? previewCourse} onChange={event => {
        const next = available.find(c => c.course_id === event.target.value);
        if (next) onCourse(next); else setPreviewCourse(event.target.value);
      }}>
        {available.length ? available.map(c => <option key={c.course_id} value={c.course_id}>{c.name}</option>) : reportProfiles.map(p => <option key={p.name}>{p.name}</option>)}
      </select>
    </label>
  } />;
}

function ReportView({ profile, courseName, picker }: { profile: ReportProfile; courseName: string; picker: React.ReactNode }) {
  const [period, setPeriod] = useState<7 | 30>(7);
  const [activePoint, setActivePoint] = useState<number | null>(null);
  const [detail, setDetail] = useState<ReportSession | null>(null);
  const [practice, setPractice] = useState<number | null>(null);
  const [answer, setAnswer] = useState<number | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [completed, setCompleted] = useState<number[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [saved, setSaved] = useState(false);
  const reportRef = useRef<HTMLDivElement>(null);
  const rows = sessions.filter(s => period === 30 || s.day >= 22);
  const previous = sessions.filter(s => s.day >= 15 && s.day <= 21);
  const rate = accuracy(rows);
  const gain = rate - accuracy(previous);
  const hours = (sum(rows, "minutes") / 60).toFixed(1);
  const visibleRecords = [...rows].reverse().slice(0, showAll ? rows.length : 3);
  const point = rows[Math.min(activePoint ?? rows.length - 1, rows.length - 1)];
  const question = practice === null ? null : profile.weak[practice];
  const startPractice = (index: number) => { setDetail(null); setPractice(index); setAnswer(null); setSubmitted(false); };

  const download = () => {
    if (!reportRef.current) return;
    const copy = reportRef.current.cloneNode(true) as HTMLElement;
    copy.querySelectorAll(".lr-controls, .lr-actions, .lr-row-action, .lr-view-all, .lr-chart-hint").forEach(el => el.remove());
    copy.querySelectorAll("button").forEach(button => {
      const replacement = document.createElement("div");
      replacement.className = button.className;
      replacement.innerHTML = button.innerHTML;
      button.replaceWith(replacement);
    });
    const title = document.createElement("span"); title.textContent = `${courseName} · 学习报告`;
    const blob = new Blob([`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title.innerHTML}</title><style>body{margin:0;padding:32px;background:#f8f7f3;font-family:"Microsoft YaHei UI",sans-serif;color:#193650}*{box-sizing:border-box}${reportCss}</style><body>${copy.outerHTML}</body></html>`], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = `${courseName.replace(/[\\/:*?"<>|]/g, "-")}-学习报告-20261028.html`;
    document.body.appendChild(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setSaved(true);
  };

  return <div className="learning-report" ref={reportRef}>
    <header className="lr-header">
      <div><div className="lr-eyebrow"><span />学习报告<span className="lr-eyebrow-divider">/</span><span className="lr-course-label">{courseName}</span></div>
        <h1>每一步积累，都看得见。</h1><p>回顾学习轨迹，找到下一次进步的方向。</p></div>
      <div className="lr-actions"><button className="lr-export" onClick={download}><Icon name="download" />{saved ? "再次下载报告" : "下载报告"}</button></div>
    </header>
    <div className="lr-toolbar"><div className="lr-controls">{picker}<div className="lr-period" role="group" aria-label="统计时间范围">{([7, 30] as const).map(days => <button key={days} aria-pressed={period === days} onClick={() => { setPeriod(days); setActivePoint(null); setShowAll(false); }}>{`近 ${days} 天`}</button>)}</div></div><span className="lr-date">{period === 7 ? "2026.10.22" : "2026.09.29"} — 2026.10.28</span></div>

    <section className="lr-insight" aria-label="学习概览"><div className="lr-insight-icon"><Icon name="trend" /></div><div><strong>学习正在进入佳境</strong><p>{profile.summary}</p></div><span className="lr-insight-badge"><span />本周已学习 5 天</span></section>

    <section className="lr-metrics" aria-label="关键学习指标">
      <Metric label="完成练习" value={sum(rows, "questions")} unit="题" icon="book" foot={<><b>{rows.length} 次</b> 专项测验 · 已答对 {sum(rows, "correct")} 题</>} />
      <Metric label="练习正确率" value={rate} unit="%" icon="target" foot={<><b>↗ {gain} 个百分点</b><span> 较 10.15—10.21</span></>} />
      <Metric label="学习投入" value={hours} unit="小时" icon="clock" foot={<><b>{rows.length} 天</b> 有学习记录 · 共 {sum(rows, "minutes")} 分钟</>} />
      <Metric label="已掌握模块" value="3" unit="/ 6" icon="check" foot={<>继续巩固 <b>3 个</b> 进阶模块</>} />
    </section>

    <div className="lr-chart-grid">
      <section className="lr-panel lr-trend"><PanelTitle title="练习得分趋势" sub="把努力连成一条向上的曲线" extra={<span className="lr-legend"><i />测验得分<span className="lr-target-key" />目标 85 分</span>} />
        <div className="lr-trend-summary"><strong>{score(rows[rows.length - 1])}<small>分</small></strong><span>最近一次测验<br /><b>较本期首次 +{score(rows[rows.length - 1]) - score(rows[0])} 分</b></span><div className="lr-point-value" aria-live="polite">10.{point.day} <b>{score(point)} 分</b></div></div>
        <svg className="lr-line-chart" viewBox="0 0 640 244" aria-label="练习得分趋势图，按日期查看各次测验成绩">
          <defs><linearGradient id="lr-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#418574" stopOpacity=".22" /><stop offset="100%" stopColor="#418574" stopOpacity="0" /></linearGradient></defs>
          {[0, 25, 50, 75, 100].map(n => <g key={n}><line x1="38" x2="614" y1={202 - n * 1.7} y2={202 - n * 1.7} stroke="#e9ece7" strokeDasharray="3 5" /><text x="24" y={206 - n * 1.7} textAnchor="end">{n}</text></g>)}
          <line x1="38" x2="614" y1="57.5" y2="57.5" stroke="#bd9c64" strokeDasharray="5 5" />
          <path d={`M38,202 ${rows.map((r, i) => `L${38 + i * 576 / (rows.length - 1)},${202 - score(r) * 1.7}`).join(" ")} L614,202 Z`} fill="url(#lr-area)" />
          <polyline points={rows.map((r, i) => `${38 + i * 576 / (rows.length - 1)},${202 - score(r) * 1.7}`).join(" ")} fill="none" stroke="#418574" strokeWidth="3" strokeLinejoin="round" />
          {rows.map((r, i) => <g key={r.day}><g tabIndex={0} role="button" aria-label={`10月${r.day}日，${score(r)}分，查看测验详情`} onMouseEnter={() => setActivePoint(i)} onFocus={() => setActivePoint(i)} onClick={() => setDetail(r)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setDetail(r); } }}>
            <circle cx={38 + i * 576 / (rows.length - 1)} cy={202 - score(r) * 1.7} r="15" fill="transparent" />
            <circle cx={38 + i * 576 / (rows.length - 1)} cy={202 - score(r) * 1.7} r={r === point ? 6 : 4} fill="#fffefa" stroke="#418574" strokeWidth="2.5" />
            </g>{(period === 7 || i % 2 === 0 || i === rows.length - 1) && <text x={38 + i * 576 / (rows.length - 1)} y="232" textAnchor="middle">10.{r.day.toString().padStart(2, "0")}</text>}
          </g>)}
        </svg><div className="lr-chart-foot"><span><Icon name="trend" />从 {score(rows[0])} 分到 {score(rows[rows.length - 1])} 分，进步正在发生</span><small className="lr-chart-hint">点击节点查看详情</small></div>
      </section>

      <section className="lr-panel lr-mastery"><PanelTitle title="知识掌握分布" sub="截至 10 月 28 日 · 共 6 个知识模块" />
        <div className="lr-donut" role="img" aria-label="综合掌握度75%，3个已掌握模块，1个巩固中模块，2个待加强模块"><svg viewBox="0 0 210 210"><circle cx="105" cy="105" r="78" fill="none" stroke="#eef0ea" strokeWidth="19" /><g transform="rotate(-90 105 105)" fill="none" strokeWidth="19"><circle cx="105" cy="105" r="78" stroke="#418574" strokeDasharray="237 490" /><circle cx="105" cy="105" r="78" stroke="#e0bc76" strokeDasharray="73 490" strokeDashoffset="-245" /><circle cx="105" cy="105" r="78" stroke="#ca806a" strokeDasharray="155 490" strokeDashoffset="-327" /></g></svg><div><strong>75<small>%</small></strong><span>综合掌握度</span></div></div>
        <div className="lr-distribution">{[["已掌握", "3", "≥ 80%"], ["巩固中", "1", "65–79%"], ["待加强", "2", "< 65%"]].map((r, i) => <div key={r[0]}><i className={`lr-dot tone-${i}`} /><span>{r[0]}</span><b>{r[1]}<small> 个模块</small></b><em>{r[2]}</em></div>)}</div>
      </section>
    </div>

    <div className="lr-detail-grid">
      <section className="lr-panel lr-knowledge"><PanelTitle title="知识掌握地图" sub="先补薄弱环节，再连成完整知识体系" extra={<span className="lr-muted-label">累计掌握情况</span>} />
        <div className="lr-chapters">{profile.chapters.map((chapter, i) => <div key={chapter} className="lr-chapter"><span className="lr-chapter-number">{String(i + 1).padStart(2, "0")}</span><span className="lr-chapter-name">{i >= 3 ? <button onClick={() => startPractice(5 - i)} aria-label={`${chapter}，掌握度${mastery[i]}%，查看巩固练习`}>{chapter}</button> : chapter}</span><span className={`lr-progress tone-${mastery[i] >= 80 ? 0 : mastery[i] >= 65 ? 1 : 2}`}><i style={{ width: `${mastery[i]}%` }} /></span><b>{mastery[i]}<small>%</small></b><span className={`lr-status tone-${mastery[i] >= 80 ? 0 : mastery[i] >= 65 ? 1 : 2}`}>{mastery[i] >= 80 ? "已掌握" : mastery[i] >= 65 ? "巩固中" : "待加强"}</span></div>)}</div>
      </section>
      <section className="lr-panel lr-habits"><PanelTitle title="让学习成为习惯" sub="2026 年 10 月 · 每一格都是你的积累" /><div className="lr-habit-total"><strong>12<small>天</small></strong><span>本月有学习记录<br /><b>最近连续学习 2 天</b></span></div>
        <div className="lr-calendar" aria-label="10月学习日历"><div className="lr-weekdays">{["一", "二", "三", "四", "五", "六", "日"].map(d => <span key={d}>{d}</span>)}</div><div className="lr-calendar-days">{Array.from({ length: 3 }, (_, i) => <span className="lr-day-blank" key={`blank-${i}`} />)}{Array.from({ length: 31 }, (_, i) => {
          const day = i + 1; const session = sessions.find(s => s.day === day);
          return <button key={day} className={`lr-day ${session ? session.minutes >= 28 ? "level-3" : session.minutes >= 24 ? "level-2" : "level-1" : ""} ${day === 28 ? "today" : ""}`} disabled={!session} title={`10月${day}日${session ? ` · 学习${session.minutes}分钟` : day > 28 ? " · 尚未开始" : " · 无学习记录"}`} aria-label={`10月${day}日${session ? `，学习${session.minutes}分钟，查看记录` : day > 28 ? "，尚未开始" : "，无学习记录"}`} onClick={() => session && setDetail(session)}>{day}</button>;
        })}</div></div><div className="lr-calendar-key"><span>学习时长</span><i /><i /><i /><i /><span>少 → 多</span></div>
      </section>
    </div>

    <section className="lr-focus-section"><PanelTitle title="下一步，优先攻克这三个知识点" sub="从一次小练习开始，把薄弱点变成得分点" extra={<span className="lr-focus-note">建议用时 15 分钟</span>} />
      <div className="lr-focus-grid">{profile.weak.map((item, i) => <article className="lr-focus-card" key={item.name}><div className="lr-focus-card-top"><span className={`lr-priority priority-${i}`}>{i === 0 ? "优先复习" : i === 1 ? "重点巩固" : "继续提升"}</span><span>掌握度 <b>{mastery[5 - i]}%</b></span></div><h3>{item.name}</h3><p>{item.tip}</p><button onClick={() => startPractice(i)}>{completed.includes(i) ? <><Icon name="check" />已完成 · 再练一次</> : <>开始巩固<Icon name="arrow" /></>}</button></article>)}</div>
    </section>

    <section className="lr-panel lr-records"><PanelTitle title="每一次练习，都算数" sub={`本期共 ${rows.length} 次测验，累计答对 ${sum(rows, "correct")} 题`} extra={<button className="lr-view-all" onClick={() => setShowAll(!showAll)}>{showAll ? "收起记录" : "展开全部记录"}<span aria-hidden="true">{showAll ? "↑" : "↓"}</span></button>} />
      <div className="lr-table-scroll"><table><thead><tr><th>测验名称</th><th>完成时间</th><th>练习题数</th><th>正确率 / 得分</th><th>用时</th><th><span className="lr-sr-only">操作</span></th></tr></thead><tbody>{visibleRecords.map(row => <tr key={row.day}><td><span className="lr-record-icon"><Icon name="book" /></span>{sessionTitle(profile, row)}</td><td>10 月 {row.day} 日</td><td>{row.questions} 题</td><td><span className={`lr-score ${score(row) >= 80 ? "good" : ""}`}>{score(row)}<small> 分</small></span></td><td>{row.minutes} 分钟</td><td><button className="lr-row-action" onClick={() => setDetail(row)}>查看详情 <span aria-hidden="true">↗</span></button></td></tr>)}</tbody></table></div>
    </section>
    <footer className="lr-footer"><span>考前笔记 <i />让每一次复习，更有方向。</span><span>统计截至 2026 年 10 月 28 日</span></footer>

    <Modal title="测验回顾" open={detail !== null} onCancel={() => setDetail(null)} footer={null} width={620} destroyOnHidden>
      {detail && <div className="lr-modal"><span className="lr-modal-eyebrow">{courseName} · 2026.10.{detail.day}</span><h2>{sessionTitle(profile, detail)}</h2><div className="lr-result-stats"><div><strong>{score(detail)}<small> 分</small></strong><span>本次得分 / 满分 100</span></div><div><strong>{detail.correct}<small> / {detail.questions}</small></strong><span>答对题数</span></div><div><strong>{detail.minutes}<small> 分钟</small></strong><span>练习用时</span></div></div><div className="lr-result-note"><b>本次复习建议</b><p>{profile.summary}</p><p>优先回顾「{profile.weak[0].name}」，理解概念后再用一道练习检验。</p></div><button className="lr-modal-primary" onClick={() => startPractice(0)}>开始薄弱点巩固 <Icon name="arrow" /></button></div>}
    </Modal>
    <Modal title="知识点巩固" open={practice !== null} onCancel={() => setPractice(null)} footer={null} width={620} destroyOnHidden>
      {question && <div className="lr-modal"><span className="lr-modal-eyebrow">{courseName} · {question.name}</span><h2>{question.question}</h2><fieldset className="lr-answer-options" disabled={submitted}><legend className="lr-sr-only">选择一个答案</legend>{question.options.map((option, i) => <label key={option} className={`${answer === i ? "selected" : ""} ${submitted && i === question.answer ? "correct" : ""} ${submitted && answer === i && i !== question.answer ? "incorrect" : ""}`}><input type="radio" name="report-answer" checked={answer === i} onChange={() => setAnswer(i)} /><span>{String.fromCharCode(65 + i)}</span>{option}</label>)}</fieldset>
        {submitted ? <><div className={`lr-answer-explanation ${answer === question.answer ? "correct" : ""}`} role="status"><b>{answer === question.answer ? "回答正确，继续保持！" : `再理解一步 · 正确答案 ${String.fromCharCode(65 + question.answer)}`}</b><p>{question.explanation}</p></div><button className="lr-modal-primary" onClick={() => { setCompleted(items => [...new Set([...items, practice!])]); setPractice(null); }}>完成巩固 <Icon name="check" /></button></> : <button className="lr-modal-primary" disabled={answer === null} onClick={() => setSubmitted(true)}>提交答案，查看解析 <Icon name="arrow" /></button>}
      </div>}
    </Modal>
  </div>;
}

function Metric({ label, value, unit, icon, foot }: { label: string; value: string | number; unit: string; icon: IconName; foot: React.ReactNode }) {
  return <article className="lr-metric"><div className="lr-metric-label"><span>{label}</span><Icon name={icon} /></div><div className="lr-metric-value">{value}<span>{unit}</span></div><p>{foot}</p></article>;
}
function PanelTitle({ title, sub, extra }: { title: string; sub: string; extra?: React.ReactNode }) {
  return <div className="lr-panel-title"><div><h2>{title}</h2><p>{sub}</p></div>{extra}</div>;
}
