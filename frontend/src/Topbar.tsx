import AccountMenu from "./AccountMenu";

export default function Topbar() {
  return (
    <header className="top">
      <span>{new Date().toLocaleDateString("zh-CN", {
        year: "numeric", month: "long", day: "numeric", weekday: "long",
      })}</span>
      <div className="top-account"><span>我的复习工作台</span><AccountMenu /></div>
    </header>
  );
}
