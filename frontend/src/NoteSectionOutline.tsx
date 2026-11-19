type Section = { id: string; title: string; level?: number };

export default function NoteSectionOutline({ sections, onSelect }: {
  sections: Section[];
  onSelect: (id: string) => void;
}) {
  return <>
    <h2>目录</h2><p>按章节组织</p>
    <nav aria-label="章节目录">{sections.map(section =>
      <button key={section.id} type="button" className={(section.level ?? 1) > 1 ? "is-subsection" : ""}
        onClick={() => onSelect(section.id)}><strong>{section.title}</strong></button>
    )}</nav>
  </>;
}
