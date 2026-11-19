import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./workbench-api";
import NoteMaterialPicker, { materialLabel, type NoteMaterial } from "./NoteMaterialPicker";

const questionTypes = [
  ["choice", "单选题"], ["multiple_choice", "多选题"], ["true_false", "判断题"], ["fill_blank", "填空题"],
  ["short_answer", "简答题"], ["calculation", "计算题"], ["proof", "证明题"],
] as const;
type QuestionType = typeof questionTypes[number][0];
export type QuizInput = {
  exam_id?: string | null; scope_mode?: string | null; chapter?: string;
  knowledge_points?: string[]; blueprint?: { question_type: QuestionType; question_count: number }[] | null;
  duration_mode?: string | null; duration_minutes?: number | null; difficulty?: string | null;
  emphasis?: string[]; excluded_topics?: string[]; include_imported_questions?: boolean | null;
  source_document_ids?: string[] | null; source_types?: string[]; allow_ai_supplement?: boolean | null;
  total_score?: number | null;
};
export type PendingQuiz = { quiz_input: QuizInput; scope?: { catalog_title?: string; source_document_ids?: string[] | null; selectable_document_ids?: string[] | null; chapter?: string } };
export type QuizConfiguration = { status: string; quiz_input: QuizInput; conflicts: string[]; missing: string[]; prompt?: { message: string } | null };

// Shares the note dialog's custom dropdown classes, including the popup itself.
function QuizSelect({ label, value, options, onChange, disabled }: {
  label: string; value: string; options: [string, string][]; onChange: (value: string) => void; disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]')?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  return <div className="note-type-field" ref={root} onKeyDown={event => {
    if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
    if (!open || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const buttons = [...(root.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [])];
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    buttons[event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
  }}><span id={id} className="note-field-label">{label}</span><button ref={trigger} type="button" className="note-type-trigger" aria-labelledby={id} aria-haspopup="listbox" aria-expanded={open} disabled={disabled} onClick={() => setOpen(!open)} onKeyDown={event => {
    if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setOpen(true); }
  }}><span>{options.find(item => item[0] === value)?.[1] || "请选择"}</span><span aria-hidden="true">⌄</span></button>{open && <div className="note-type-menu" role="listbox" aria-label={label}>{[["", "请选择"], ...options].map(([key, text]) => <button type="button" role="option" aria-selected={value === key} key={key} onClick={() => { onChange(key); setOpen(false); trigger.current?.focus(); }}><strong>{text}</strong>{value === key && <span aria-hidden="true">✓</span>}</button>)}</div>}</div>;
}

const lines = (value: string) => [...new Set(value.split(/[\n、，,]/).map(item => item.trim()).filter(Boolean))];
const sourceNames: Record<string, string> = { past_exam: "历年真题", teacher_ppt: "老师 PPT", homework: "平时作业", other_practice: "其他练习", crash_course: "速成课", external_upload: "外部上传" };

export default function QuizConfigDialog({
  courseId, pending, busy, error, pickerOpen, onPickerOpenChange, onConfirm, onCancel, standalone = false,
}: {
  courseId: string;
  pending: PendingQuiz;
  busy: boolean;
  error: string;
  pickerOpen: boolean;
  onPickerOpenChange: (open: boolean) => void;
  onConfirm: (input: QuizInput) => void;
  onCancel: () => void;
  standalone?: boolean;
}) {
  const [step, setStep] = useState<1 | 2>(1);
  const [input, setInput] = useState<QuizInput>(pending.quiz_input);
  const [counts, setCounts] = useState<Record<QuestionType, string>>(() => Object.fromEntries(
    questionTypes.map(([type]) => [
      type, String(pending.quiz_input.blueprint?.find(item => item.question_type === type)?.question_count ?? 0),
    ]),
  ) as Record<QuestionType, string>);
  const [points, setPoints] = useState((input.knowledge_points ?? []).join("\n"));
  const [emphasis, setEmphasis] = useState((input.emphasis ?? []).join("\n"));
  const [excluded, setExcluded] = useState((input.excluded_topics ?? []).join("\n"));
  const [minutes, setMinutes] = useState(String(input.duration_minutes ?? ""));
  const [score, setScore] = useState(String(input.total_score ?? ""));
  const [materials, setMaterials] = useState<NoteMaterial[]>([]);
  const [materialError, setMaterialError] = useState("");
  const stepTitle = useRef<HTMLParagraphElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const chooseMaterials = useRef<HTMLButtonElement>(null);
  const id = useId();
  const patch = (changes: Partial<QuizInput>) => setInput(current => ({ ...current, ...changes }));
  const knowledgePoints = input.scope_mode === "knowledge_points" ? lines(points) : [];
  const emphasisItems = lines(emphasis);
  const excludedTopics = lines(excluded);
  const total = Object.values(counts).reduce((sum, count) => sum + Number(count), 0);
  const selectedIds = input.source_document_ids;
  const sourcesReady = selectedIds != null && selectedIds.every(documentId =>
    materials.some(item => item.document_id === documentId && item.parse_status === "ready"),
  );
  const scopeValid = Boolean(input.scope_mode)
    && (input.scope_mode !== "chapter" || Boolean(input.chapter?.trim()))
    && (input.scope_mode !== "knowledge_points" || knowledgePoints.length > 0)
    && knowledgePoints.length <= 100 && knowledgePoints.every(item => item.length <= 200)
    && sourcesReady && typeof input.allow_ai_supplement === "boolean" && input.include_imported_questions === false;
  const quizValid = Object.values(counts).every(count => count !== "" && Number.isInteger(Number(count)) && Number(count) >= 0)
    && total >= 1 && total <= 100 && Boolean(input.duration_mode && input.difficulty)
    && (input.duration_mode !== "timed" || (Number.isInteger(Number(minutes)) && Number(minutes) >= 1 && Number(minutes) <= 240))
    && emphasisItems.length <= 30 && excludedTopics.length <= 30
    && [...emphasisItems, ...excludedTopics].every(item => item.length <= 200)
    && (!score || (Number.isFinite(Number(score)) && Number(score) > 0 && Number(score) <= 10000));

  useEffect(() => {
    if (!selectedIds?.length) {
      setMaterials([]);
      setMaterialError("");
      return;
    }
    let active = true;
    setMaterialError("");
    api<{ items: NoteMaterial[] }>(`/api/courses/${encodeURIComponent(courseId)}/documents`).then(data => {
      if (active) setMaterials(data.items.filter(item => selectedIds.includes(item.document_id)));
    }).catch(reason => {
      if (active) setMaterialError(reason instanceof Error ? reason.message : "资料读取失败");
    });
    return () => { active = false; };
  }, [courseId, selectedIds]);

  useEffect(() => {
    if (body.current) body.current.scrollTop = 0;
    stepTitle.current?.focus({ preventScroll: true });
  }, [step]);

  function closePicker() {
    onPickerOpenChange(false);
    requestAnimationFrame(() => chooseMaterials.current?.focus({ preventScroll: true }));
  }

  function submit() {
    onConfirm({
      ...input,
      chapter: input.scope_mode === "course" ? "" : input.chapter?.trim() || "",
      knowledge_points: knowledgePoints,
      blueprint: questionTypes.filter(([type]) => Number(counts[type]) > 0).map(([type]) => ({
        question_type: type, question_count: Number(counts[type]),
      })),
      duration_minutes: input.duration_mode === "timed" ? Number(minutes) : null,
      total_score: score ? Number(score) : null,
      emphasis: emphasisItems,
      excluded_topics: excludedTopics,
    });
  }

  return <>
    <section className="note-dialog chat-task-card quiz-config-dialog" role="dialog"
      aria-labelledby={`${id}-title`} aria-describedby={`${id}-step`}>
      <header className="note-dialog-head">
        <span className="note-question-mark" aria-hidden="true">✦</span>
        <div>
          <small>{standalone ? "从课程资料出题" : "助手追问 · 生成试题"}</small>
          <h2 id={`${id}-title`}>{standalone ? "生成测试" : "补充试题要求"}</h2>
          <p id={`${id}-step`} ref={stepTitle} tabIndex={-1}>第 {step} / 2 步 · {step === 1 ? "考什么" : "怎么考"}</p>
        </div>
      </header>
      <div className="note-dialog-body" ref={body}>
        {step === 1 ? <>
          <div className="note-dialog-grid">
            <QuizSelect label="出题范围" value={input.scope_mode ?? ""}
              options={[["course", "全课程"], ["chapter", "指定章节"], ["knowledge_points", "指定知识点"]]}
              disabled={busy || Boolean(pending.scope?.catalog_title)} onChange={value => patch({
                scope_mode: value || null, chapter: pending.scope?.chapter || input.chapter, knowledge_points: [],
              })} />
            {(input.scope_mode === "chapter" || (input.scope_mode === "knowledge_points" && pending.scope?.chapter)) &&
              <label>章节<input maxLength={200} value={input.chapter ?? ""} disabled={busy || Boolean(pending.scope?.catalog_title)}
                onChange={event => patch({ chapter: event.target.value })} placeholder="例如：第三章" /></label>}
          </div>
          {input.scope_mode === "knowledge_points" && <label>知识点<textarea value={points} disabled={busy || Boolean(pending.scope?.catalog_title)}
            onChange={event => setPoints(event.target.value)} placeholder="每行一个知识点，每个知识点最多 6 题" /></label>}
          <div className="note-dialog-source">
            <div className="note-dialog-source-head">
              <strong>生成依据 · 必选</strong>
              <button ref={chooseMaterials} type="button" disabled={busy} onClick={() => onPickerOpenChange(true)}>选择资料</button>
            </div>
            <QuizSelect label="资料范围" value={selectedIds == null ? "" : selectedIds.length ? "selected" : "all"}
              options={[["all", "当前允许范围的全部可用资料"], ["selected", "指定资料"]]} disabled={busy}
              onChange={value => {
                if (value === "selected") onPickerOpenChange(true);
                else patch({ source_document_ids: value === "all" ? [] : null });
              }} />
            {materials.length > 0 && <div className="note-config-files">{materials.map(item => (
              <span key={item.document_id}>{materialLabel(item)}{item.parse_status !== "ready" ? " · 未就绪" : ""}</span>
            ))}</div>}
            {pending.scope?.catalog_title && <small>正式知识目录 · {pending.scope.catalog_title}</small>}
            {pending.scope?.chapter && <small>当前对话限定章节：{pending.scope.chapter}</small>}
            {pending.scope?.source_document_ids && <small>资料选择受当前对话指定的文件范围限制。</small>}
            {Boolean(input.source_types?.length) && <small>已识别的来源类别：{input.source_types?.map(type => sourceNames[type] ?? type).join("、")}
              <button type="button" disabled={busy} onClick={() => patch({ source_types: [] })}>清除类别限制</button>
            </small>}
          </div>
          <div className="note-dialog-grid">
            <QuizSelect label="AI 补充题" value={input.allow_ai_supplement == null ? "" : String(input.allow_ai_supplement)}
              options={[["false", "不允许"], ["true", "允许（资料不足时补充）"]]} disabled={busy}
              onChange={value => patch({ allow_ai_supplement: value === "" ? null : value === "true" })} />
            <QuizSelect label="纳入导入题" value={input.include_imported_questions == null ? "" : String(input.include_imported_questions)}
              options={[["false", "不纳入"]]} disabled={busy}
              onChange={value => patch({ include_imported_questions: value === "" ? null : false })} />
          </div>
        </> : <>
          <div className="note-dialog-source">
            <div className="note-dialog-source-head">
              <strong>题型与数量</strong>
              <span className={total > 100 ? "quiz-count-error" : ""}>共 {total || 0} / 100 题</span>
            </div>
            <div className="note-dialog-grid quiz-counts">{questionTypes.map(([type, label]) => (
              <label key={type}>{label}<input type="number" min={0} max={100} step={1} value={counts[type]} disabled={busy}
                onChange={event => setCounts(current => ({ ...current, [type]: event.target.value }))} /></label>
            ))}</div>
            <small>不需要的题型填 0，总计 1～100 题。</small>
          </div>
          <div className="note-dialog-grid">
            <QuizSelect label="难度" value={input.difficulty ?? ""}
              options={[["basic", "基础"], ["standard", "标准"], ["advanced", "进阶"], ["mixed", "混合"]]}
              disabled={busy} onChange={value => patch({ difficulty: value || null })} />
            <QuizSelect label="计时方式" value={input.duration_mode ?? ""}
              options={[["untimed", "不限时"], ["timed", "限时"]]} disabled={busy}
              onChange={value => patch({ duration_mode: value || null })} />
            {input.duration_mode === "timed" && <label>限时（分钟）<input type="number" min={1} max={240} step={1}
              value={minutes} disabled={busy} onChange={event => setMinutes(event.target.value)} /></label>}
            <label>总分 · 可选<input type="number" min={0.01} max={10000} step="any" value={score}
              disabled={busy} onChange={event => setScore(event.target.value)} /></label>
          </div>
          <div className="note-dialog-grid">
            <label>重点 · 可选<textarea value={emphasis} disabled={busy} onChange={event => setEmphasis(event.target.value)} placeholder="每行一项" /></label>
            <label>排除内容 · 可选<textarea value={excluded} disabled={busy} onChange={event => setExcluded(event.target.value)} placeholder="每行一项" /></label>
          </div>
        </>}
      </div>
      {(error || (step === 1 && materialError)) && <p className="note-dialog-error quiz-config-error" role="alert">{error || materialError}</p>}
      <footer className="note-dialog-footer">
        <p>{step === 1 ? "选好范围和资料后继续。" : "提交后将在后台生成试卷。"}</p>
        <div>
          <button type="button" className="note-dialog-cancel" disabled={busy} onClick={onCancel}>取消</button>
          {step === 1
            ? <button type="button" className="note-dialog-submit" disabled={busy || !scopeValid} onClick={() => setStep(2)}>下一步</button>
            : <>
              <button type="button" className="note-dialog-cancel" disabled={busy} onClick={() => setStep(1)}>上一步</button>
              <button type="button" className="note-dialog-submit" disabled={busy || !scopeValid || !quizValid}
                onClick={submit}>{busy ? "正在提交…" : "生成试卷"}</button>
            </>}
        </div>
      </footer>
    </section>
    {pickerOpen && <NoteMaterialPicker courseId={courseId} selected={materials} maxSelection={100} purpose="试题"
      allowedIds={pending.scope?.selectable_document_ids ?? pending.scope?.source_document_ids} onUploaded={() => {}}
      onConfirm={items => {
        setMaterials(items);
        patch({ source_document_ids: items.map(item => item.document_id) });
        closePicker();
      }} onClose={closePicker} />}
  </>;
}
