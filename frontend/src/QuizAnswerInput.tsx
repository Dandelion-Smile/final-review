export type QuizAnswer = string | number | boolean | number[] | string[] | null;
export type AnswerQuestion = {
  question_type: string;
  options: string[];
  blank_count?: number;
};

export function cleanAnswer(value: QuizAnswer | undefined): QuizAnswer {
  if (typeof value === "string") return value.trim() || null;
  if (Array.isArray(value)) {
    if (!value.length) return null;
    if (typeof value[0] === "number") return [...value as number[]].sort((a, b) => a - b);
    const blanks = (value as string[]).map(item => item.trim());
    return blanks.some(Boolean) ? blanks : null;
  }
  return value ?? null;
}

export function answerText(question: AnswerQuestion, value: QuizAnswer): string {
  if (value == null) return "未作答";
  const optionText = (index: number) => {
    const option = question.options[index];
    return option == null ? "无效选项" : `${String.fromCharCode(65 + index)}. ${option.replace(/^[A-H][.．、:：]\s*/, "")}`;
  };
  if (typeof value === "number") return optionText(value);
  if (typeof value === "boolean") return value ? "正确" : "错误";
  if (Array.isArray(value)) return value.map((item, index) => typeof item === "number"
    ? optionText(item) : `第 ${index + 1} 空 ${item || "未填"}`).join("；");
  return value;
}

export default function QuizAnswerInput({ question, name, value, disabled, onChange, textLabel = "文字作答" }: {
  question: AnswerQuestion;
  name: string;
  value: QuizAnswer | undefined;
  disabled: boolean;
  onChange: (value: QuizAnswer) => void;
  textLabel?: string;
}) {
  const kind = question.question_type;
  if (kind === "choice" || kind === "multiple_choice") {
    const multiple = kind === "multiple_choice";
    const selected = Array.isArray(value) ? value as number[] : [];
    return <div className="formal-options">
      {multiple && <p>可选择多个答案</p>}
      {question.options.map((_, index) => <label key={index}>
        <input type={multiple ? "checkbox" : "radio"} name={name} disabled={disabled}
          checked={multiple ? selected.includes(index) : value === index}
          onChange={event => onChange(multiple
            ? event.target.checked ? [...selected, index] : selected.filter(item => item !== index)
            : index)} />
        {answerText(question, index)}
      </label>)}
    </div>;
  }
  if (kind === "true_false") return <div className="formal-options">
    {[true, false].map(item => <label key={String(item)}>
      <input type="radio" name={name} disabled={disabled} checked={value === item} onChange={() => onChange(item)} />
      {answerText(question, item)}
    </label>)}
  </div>;
  if (kind === "fill_blank") {
    const count = question.blank_count ?? 0;
    if (count > 0) {
      const blanks = Array.from({ length: count }, (_, index) => Array.isArray(value) ? String(value[index] ?? "") : "");
      return <>{blanks.map((blank, index) => <label key={index}>第 {index + 1} 空
        <input disabled={disabled} value={blank} onChange={event => {
          const next = [...blanks];
          next[index] = event.target.value;
          onChange(next);
        }} />
      </label>)}</>;
    }
    return <label>填写答案<input disabled={disabled} value={typeof value === "string" ? value : ""}
      onChange={event => onChange(event.target.value)} /></label>;
  }
  return <label>{textLabel}<textarea disabled={disabled} value={typeof value === "string" ? value : ""}
    onChange={event => onChange(event.target.value)} /></label>;
}
