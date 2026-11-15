/* Independent review data. No API calls, user data, or persistent writes. */
window.COURSE_PREVIEW = {
  materials: [
    { id: 'm1', title: '第1章 Web开发基础.pptx', type: 'PPT', meta: '32 页 · 11月10日上传', description: 'Web应用的基本架构、HTTP协议、开发环境与项目结构。' },
    { id: 'm2', title: '第2章 Servlet技术.pptx', type: 'PPT', meta: '48 页 · 11月10日上传', description: 'Servlet生命周期、请求处理、响应输出与URL映射。' },
    { id: 'm3', title: '第3章 JSP与表达式语言.pptx', type: 'PPT', meta: '42 页 · 11月11日上传', description: 'JSP语法、内置对象、EL表达式与JSTL标签库。' },
    { id: 'm4', title: '第4章 会话管理.pptx', type: 'PPT', meta: '36 页 · 11月11日上传', description: 'Cookie、Session与会话跟踪机制。' },
    { id: 'm5', title: '第5章 数据库访问技术.pptx', type: 'PPT', meta: '52 页 · 11月12日上传', description: 'JDBC、预编译语句、事务、连接池与DAO模式。' },
    { id: 'm6', title: '第6章 MVC与Web应用实践.pptx', type: 'PPT', meta: '40 页 · 11月12日上传', description: 'MVC、过滤器、监听器与Web应用的综合实践。' },
    { id: 'm7', title: 'Web服务端技术复习提纲.pdf', type: 'PDF', meta: '18 页 · 11月13日上传', description: '六个章节的复习提纲与核心知识清单。' },
    { id: 'm8', title: '课程实验指导书.docx', type: 'DOC', meta: '26 页 · 11月14日上传', description: '从Servlet请求处理到数据库访问的课程实验步骤。' }
  ],
  chapters: [
    { title: 'Web开发基础', material: 'm1', points: [
      { title: 'Web应用架构与请求响应流程', page: '6–10', description: '浏览器发送HTTP请求，Web服务器接收并交由应用处理，应用返回响应，由浏览器解析与呈现。B/S架构将界面交互与服务端业务处理分开。' },
      { title: 'HTTP协议与常见请求方法', page: '12–18', important: true, description: 'HTTP请求由请求行、请求头和可选的请求体组成。GET通常用于获取资源，POST通常用于提交数据；理解请求方法的语义有助于设计清晰的接口。' },
      { title: 'HTTP状态码与响应结构', page: '19–24', description: '响应包括状态行、响应头和响应体。2xx表示请求成功，3xx表示重定向，4xx表示客户端请求存在问题，5xx表示服务端处理失败。' },
      { title: 'Tomcat环境与Web项目结构', page: '25–30', description: 'Tomcat提供Servlet运行环境。Web应用通过部署描述、资源目录与类路径组织静态资源和服务端组件。' }
    ] },
    { title: 'Servlet技术', material: 'm2', points: [
      { title: 'Servlet生命周期', page: '8–14', important: true, description: 'Servlet容器负责实例化、初始化、请求处理与销毁。init()执行初始化，service()处理请求，destroy()完成销毁前的资源清理。' },
      { title: 'HttpServletRequest与请求参数', page: '15–24', description: 'HttpServletRequest封装当前HTTP请求，可以读取请求参数、请求头与请求属性。参数通常来自查询字符串或表单。' },
      { title: 'HttpServletResponse与响应输出', page: '25–33', description: 'HttpServletResponse用于设置状态码、响应头、内容类型和响应体，帮助服务端向浏览器返回文本、页面或结构化数据。' },
      { title: '请求转发与重定向', page: '34–42', important: true, description: '请求转发在服务端将同一次请求交给另一个资源处理；重定向通过响应通知浏览器发起新的请求。二者的地址栏表现、请求次数与属性共享方式不同。' }
    ] },
    { title: 'JSP与表达式语言', material: 'm3', points: [
      { title: 'JSP运行原理与基本语法', page: '5–11', description: 'JSP页面在容器中被转换为Servlet并执行，适合在服务端生成动态页面。' },
      { title: 'JSP内置对象与作用域', page: '12–19', important: true, description: 'page、request、session与application具有不同的可见范围和生命周期，应根据数据共享范围选择。' },
      { title: 'JSP指令与动作标签', page: '20–25', description: '页面指令配置JSP的基本行为，动作标签在请求处理期间执行包含页面、转发请求等操作。' },
      { title: 'EL表达式与数据访问', page: '26–33', description: 'EL表达式用于读取作用域属性、访问对象属性和集合元素，减少页面中的Java脚本代码。' },
      { title: 'JSTL核心标签库', page: '34–40', description: 'JSTL通过条件、循环与输出等标签表达常见页面逻辑，配合EL提高视图可读性。' }
    ] },
    { title: '会话管理', material: 'm4', points: [
      { title: 'HTTP无状态与会话跟踪', page: '4–8', description: 'HTTP请求本身不自动保留业务会话状态，应用通常通过会话标识关联同一用户的多次请求。' },
      { title: 'Cookie机制与常用属性', page: '9–15', description: 'Cookie由浏览器保存，并根据域、路径、有效期等规则发送。HttpOnly、Secure与SameSite影响其使用和安全性。' },
      { title: 'HttpSession的创建与销毁', page: '16–23', important: true, description: 'Session通常在服务端保存会话数据，通过会话标识关联客户端。注销和超时等情况需要合理结束会话。' },
      { title: 'Cookie与Session的区别', page: '24–29', important: true, description: 'Cookie主要在客户端保存数据；Session主要在服务端保存数据，通常依靠Cookie中的会话标识识别用户。' },
      { title: '登录状态与会话安全', page: '30–35', description: '登录成功后应建立清晰的身份状态，保护会话标识，并在退出登录时使会话失效。' }
    ] },
    { title: '数据库访问技术', material: 'm5', points: [
      { title: 'JDBC连接与操作流程', page: '6–14', description: 'JDBC通常包含获取连接、准备语句、执行SQL、处理结果与释放资源等步骤。' },
      { title: 'PreparedStatement与参数绑定', page: '15–23', important: true, description: 'PreparedStatement通过参数占位符和绑定值构建查询，有助于避免将输入拼接进SQL结构。' },
      { title: 'ResultSet与结果集处理', page: '24–30', description: 'ResultSet用于遍历查询返回的数据，读取列值，并将记录转换为业务对象。' },
      { title: '事务控制与异常处理', page: '31–40', description: '事务将一组相关操作组织为完整单元，通过提交或回滚处理成功与失败，确保数据符合业务约束。' },
      { title: '数据库连接池与DAO模式', page: '41–49', description: '连接池复用数据库连接，DAO将数据库操作集中封装，为业务层提供明确的数据访问接口。' }
    ] },
    { title: 'MVC与Web应用实践', material: 'm6', points: [
      { title: 'MVC设计模式与职责划分', page: '5–12', important: true, description: '模型承载业务数据与规则，视图负责呈现，控制器接收请求并协调处理，帮助分离各层职责。' },
      { title: 'Filter过滤器与过滤器链', page: '13–20', description: '过滤器在目标资源处理前后执行通用逻辑，可用于字符编码、访问控制与请求日志等场景。' },
      { title: 'Listener监听器与应用事件', page: '21–27', description: '监听器响应应用、会话或请求等生命周期事件，适合处理初始化和资源清理等工作。' },
      { title: '分层架构与业务逻辑组织', page: '28–33', description: '表示层、业务层与数据访问层各自承担明确职责，降低不同部分之间的直接耦合。' },
      { title: 'Web应用部署与综合实践', page: '34–39', description: '将请求处理、会话管理、数据库访问与页面展示串联，并检查部署配置和资源访问路径。' }
    ] }
  ],
  notes: [
    { title: 'Web开发基础考点精要', meta: '覆盖第1章 · 11月14日更新', description: '梳理Web应用架构、HTTP请求与响应、状态码及Tomcat项目结构。复习时可按一次完整的浏览器请求串起这些知识点。' },
    { title: 'Servlet生命周期与请求处理', meta: '覆盖第2章 · 11月13日更新', description: '围绕init、service和destroy三个阶段整理Servlet生命周期，并对比请求转发与重定向。' },
    { title: 'JSP与EL表达式速查', meta: '覆盖第3章 · 11月12日更新', description: '整理JSP常用对象、四种作用域、EL访问方式和JSTL核心标签。' },
    { title: 'Cookie与Session对比笔记', meta: '覆盖第4章 · 11月12日更新', description: '从数据存储位置、有效期、会话识别方式和安全性比较Cookie与Session。' },
    { title: 'JDBC操作与MVC分层梳理', meta: '覆盖第5–6章 · 11月11日更新', description: '整理数据库访问流程，以及控制器、业务层、DAO和页面视图之间的职责。' }
  ],
  tests: [
    { title: 'HTTP协议与Web开发基础', meta: '10 道题 · 11月14日', status: '已完成', score: 80, description: 'Web开发基础章节练习，共10道选择题。示例作答得分80分，8题答对。' },
    { title: 'Servlet核心知识专项练习', meta: '15 道题 · 已完成 6 题', status: '待继续', description: 'Servlet章节练习，共15道题，示例进度为已完成6题。正式接入后可在测试模块继续作答。' },
    { title: 'Web服务端技术综合测试', meta: '30 道题 · 11月12日', status: '已完成', score: 87, description: '覆盖六个章节的综合练习，共30道题，示例得分87分。' }
  ]
};
