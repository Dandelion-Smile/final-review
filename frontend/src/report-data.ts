// Fixed, presentation-only report snapshots. These never write attempts or grades
// to the API. Course names and Web modules match the existing course catalogue.
export type ReportProfile = {
  name: string;
  short: string;
  summary: string;
  chapters: string[];
  weak: { name: string; tip: string; question: string; options: string[]; answer: number; explanation: string }[];
};

export const reportProfiles: ReportProfile[] = [
  {
    name: "Web服务端技术原理及应用", short: "Web 服务端", summary: "前端基础已经扎实，接下来把 HTTP、Servlet 与 JSP 串成完整的请求链路。",
    chapters: ["HTML 与 CSS 基础", "JavaScript 与 jQuery", "HTTP 协议与开发环境", "Servlet 基础与生命周期", "会话管理与状态保持", "JSP 原理与基本语法"],
    weak: [
      { name: "JSP 隐式对象", tip: "区分 request、session 与 application 的作用范围。", question: "需要在同一用户的多次请求之间保存登录状态，最合适的 JSP 隐式对象是？", options: ["pageContext", "request", "session", "application"], answer: 2, explanation: "session 的作用范围是同一用户的会话，可跨多个请求保存状态。request 仅用于单次请求；application 在整个 Web 应用中共享，不能作为个人登录状态的存储范围。" },
      { name: "Cookie 与 Session", tip: "重点梳理状态存储位置与会话标识的传递方式。", question: "关于 Cookie 与 Session，下列说法正确的是？", options: ["Session 数据一定保存在浏览器中", "Cookie 与 Session 都不能跨请求使用", "Session 通常在服务端保存，客户端携带会话标识", "关闭页面一定会立即清除服务端 Session"], answer: 2, explanation: "Cookie 保存在客户端，Session 数据通常保存在服务端。客户端可以通过 Cookie 携带会话标识；禁用 Cookie 时也可采用 URL 重写等方式。关闭页面不等于服务端会话立即失效。" },
      { name: "Servlet 生命周期", tip: "把初始化、请求处理与销毁三个阶段对应起来。", question: "同一个 Servlet 实例处理多个请求时，通常只在初始化阶段调用一次的方法是？", options: ["service()", "doGet()", "doPost()", "init()"], answer: 3, explanation: "容器创建 Servlet 实例后调用 init() 进行初始化。service() 在处理请求时调用，并分派到 doGet()、doPost() 等方法；实例销毁前调用 destroy()。" },
    ],
  },
  {
    name: "软件工程基础", short: "软件工程", summary: "Java 基础与需求分析稳步提升，继续打通设计、实现和验证的完整过程。",
    chapters: ["软件过程与开发模型", "Java 基础语法", "需求获取与分析", "面向对象与模块设计", "软件测试与质量", "AI Agent 应用开发"],
    weak: [
      { name: "Agent 工具调用", tip: "梳理模型决策、工具执行与结果回传的职责。", question: "AI Agent 选择一个工具后，通常由哪个环节实际执行工具调用？", options: ["模型生成的文本自动执行", "应用运行时校验参数并执行工具", "用户浏览器直接执行所有指令", "向量数据库执行所有外部操作"], answer: 1, explanation: "模型生成工具名称和参数，应用运行时负责校验、授权与实际执行，再将结果返回模型。工具选择和工具执行是不同环节。" },
      { name: "测试用例设计", tip: "结合等价类和边界值，补齐容易遗漏的输入。", question: "输入范围为 1～100 的整数，采用边界值分析时应优先覆盖哪一组？", options: ["20、40、60", "1、50、100", "0、1、2、99、100、101", "只测试 1 和 100"], answer: 2, explanation: "边界值分析需要检查边界及其相邻值。0 和 101 覆盖无效边界，1 和 100 覆盖有效边界，2 和 99 覆盖边界内侧。" },
      { name: "模块职责划分", tip: "从高内聚、低耦合判断设计是否合理。", question: "下列哪一种模块划分更符合高内聚、低耦合的目标？", options: ["每个模块承担明确职责，通过清晰接口协作", "所有模块直接修改同一全局变量", "按代码行数平均分配模块", "将所有功能放在一个类中"], answer: 0, explanation: "模块内部功能应围绕同一职责组织，模块之间通过稳定接口协作。避免共享可变全局状态，可以降低修改传播和测试成本。" },
    ],
  },
  {
    name: "高等数学", short: "高等数学", summary: "基础计算的熟练度持续提升，把更多时间留给积分方法与综合应用。",
    chapters: ["函数与极限", "连续性与间断点", "导数与微分", "中值定理与导数应用", "不定积分", "定积分及其应用"],
    weak: [
      { name: "定积分的几何意义", tip: "区分有向面积与图形的实际面积。", question: "若连续函数 f(x) 在区间 [a,b] 上恒小于零，则其定积分与 x 轴围成的几何面积有什么关系？", options: ["定积分等于几何面积", "定积分等于几何面积的相反数", "定积分一定为零", "两者没有关系"], answer: 1, explanation: "定积分表示有向面积。函数在 x 轴下方时积分为负，而几何面积为正，因此几何面积等于该定积分的相反数。" },
      { name: "分部积分法", tip: "根据求导后是否变简单来选择 u。", question: "计算 ∫x·eˣ dx，采用分部积分法时较合适的选择是？", options: ["u=x，dv=eˣ dx", "u=1，dv=x·eˣ dx", "u=x·eˣ，dv=dx", "无法使用分部积分法"], answer: 0, explanation: "令 u=x、dv=eˣ dx，则 du=dx、v=eˣ。由 ∫u dv=uv−∫v du，得到 x·eˣ−eˣ+C。" },
      { name: "极值判定", tip: "驻点只是候选点，还需要检查导数符号。", question: "若 f′(x₀)=0，则一定可以推出什么？", options: ["x₀ 是极大值点", "x₀ 是极小值点", "x₀ 是驻点，未必是极值点", "函数在 x₀ 不连续"], answer: 2, explanation: "导数为零说明该点是驻点，不能单独保证极值。例如 f(x)=x³ 在 0 处导数为零，但两侧单调性没有改变，不是极值点。" },
    ],
  },
  {
    name: "Typora学习", short: "知识整理", summary: "从 Markdown 组织到知识表达，逐步建立清晰、可检索的学习笔记。",
    chapters: ["Markdown 基础语法", "标题与文档结构", "图片与链接", "代码块与语法高亮", "知识笔记整理", "文档导出与发布"],
    weak: [
      { name: "导出与资源路径", tip: "导出前检查图片路径与附件的可访问性。", question: "希望 Markdown 文件移动到其他电脑后仍能显示图片，较可靠的做法是？", options: ["仅保留本机绝对路径", "随文档打包图片并使用相对路径", "删除所有图片说明", "将路径换成任意文件名"], answer: 1, explanation: "使用相对路径并将图片和文档一起打包，能减少对原电脑目录结构的依赖。导出后还应检查图片是否完整显示。" },
      { name: "笔记层级组织", tip: "用标题表达内容关系，避免只靠字号区分。", question: "组织章节与小节时，哪种方式最有利于生成目录？", options: ["统一使用加粗正文", "连续插入空行", "按逻辑使用不同级别标题", "将所有文字放入表格"], answer: 2, explanation: "标题层级提供明确的结构信息，支持目录、折叠和导航。加粗只改变显示样式，不代替标题的结构语义。" },
      { name: "代码块语言标记", tip: "为代码块注明语言，提升阅读和检索效率。", question: "Markdown 围栏代码块的起始反引号后标注 java，主要作用是？", options: ["自动运行 Java 代码", "提供语言提示与语法高亮", "将代码转换为图片", "隐藏代码内容"], answer: 1, explanation: "语言标记用于语法高亮和编辑提示，不会自动执行代码。代码运行需要对应的运行环境。" },
    ],
  },
  {
    name: "测试课程", short: "网络基础", summary: "结合已有 HTTP 与 TCP 资料，巩固从连接建立到请求响应的关键环节。",
    chapters: ["网络协议分层", "HTTP 请求与响应", "TCP 可靠传输", "TCP 连接建立", "HTTP 状态码", "Web 服务端环境"],
    weak: [
      { name: "服务端运行环境", tip: "分清浏览器、Web 服务器和应用程序的职责。", question: "动态 Web 应用中，Servlet 代码通常运行在哪里？", options: ["浏览器 CSS 引擎中", "服务端 Servlet 容器中", "客户端 Cookie 中", "DNS 缓存中"], answer: 1, explanation: "Servlet 运行在服务端容器中，由容器管理生命周期并分派请求。浏览器接收服务端返回的响应，不直接运行 Servlet。" },
      { name: "HTTP 状态码", tip: "按 2xx、3xx、4xx、5xx 理解响应类别。", question: "HTTP 404 状态码通常表示什么？", options: ["请求成功", "资源未找到", "服务器内部错误", "永久重定向"], answer: 1, explanation: "404 表示目标资源未找到。200 表示成功，500 表示服务端内部错误，301 表示永久重定向。" },
      { name: "TCP 三次握手", tip: "理解同步序列号与确认双方收发能力。", question: "TCP 建立连接时三次握手的重要作用是？", options: ["保证永不丢包", "同步初始序列号并确认双方收发能力", "直接加密全部数据", "替代 HTTP 请求"], answer: 1, explanation: "三次握手同步双方初始序列号并确认通信双方的收发能力。TCP 可靠传输还依赖确认、重传等机制，握手本身不提供加密。" },
    ],
  },
];

export function profileFor(name: string): ReportProfile {
  return reportProfiles.find(p => p.name === name)
    ?? (/Web|HTTP|Servlet/i.test(name) ? reportProfiles[0]
      : /数学/.test(name) ? reportProfiles[2]
      : /TCP|网络|验收/.test(name) ? reportProfiles[4]
      : { ...reportProfiles[3], name, short: name });
}

export const mastery = [94, 88, 82, 72, 62, 54];
export const sessions = [
  { day: 2, questions: 10, correct: 5, minutes: 20 },
  { day: 5, questions: 12, correct: 7, minutes: 24 },
  { day: 8, questions: 15, correct: 9, minutes: 30 },
  { day: 11, questions: 10, correct: 6, minutes: 18 },
  { day: 14, questions: 15, correct: 9, minutes: 28 },
  { day: 17, questions: 12, correct: 7, minutes: 24 },
  { day: 20, questions: 15, correct: 10, minutes: 30 },
  { day: 22, questions: 12, correct: 9, minutes: 22 },
  { day: 24, questions: 15, correct: 11, minutes: 28 },
  { day: 25, questions: 15, correct: 12, minutes: 25 },
  { day: 27, questions: 15, correct: 13, minutes: 32 },
  { day: 28, questions: 15, correct: 13, minutes: 26 },
];
export type ReportSession = typeof sessions[number];
