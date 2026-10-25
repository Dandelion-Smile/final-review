/* Local design fixtures only. The fixed clock makes counts and screenshots reproducible.
   Production adapters must keep question keys off the unanswered attempt payload. */
window.FRMock = (() => {
  const now = '2026-10-25T16:24:00+08:00';
  const studySets = [
    { id: 'network', name: '计算机网络', description: '期末复习资料、章节笔记与随堂测试', icon: 'monitor', color: 'purple', status: 'active', lastVisitedAt: '2026-10-25T14:32:00+08:00' },
    { id: 'math', name: '高等数学', description: '公式整理、典型题与错题回顾', icon: 'sigma', color: 'green', status: 'active', lastVisitedAt: '2026-10-24T20:10:00+08:00' },
    { id: 'english', name: '大学英语', description: '阅读、翻译与写作复习内容', icon: 'book', color: 'orange', status: 'active', lastVisitedAt: '2026-10-22T18:20:00+08:00' },
    { id: 'structures', name: '数据结构', description: '核心概念、算法题与实验复盘', icon: 'layers', color: 'blue', status: 'archived', lastVisitedAt: '2026-10-20T18:20:00+08:00' },
  ];
  const materials = [
    { id: 'm-slides', studySetId: 'network', title: '第三章 传输层.pptx', fileType: 'PPT', chapter: '第三章', sourceType: '老师课件', status: 'ready', pageCount: 32, uploadedAt: '2026-10-25T14:20:00+08:00' },
    { id: 'm-outline', studySetId: 'network', title: '计算机网络复习提纲.pdf', fileType: 'PDF', chapter: '综合复习', sourceType: '复习提纲', status: 'ready', pageCount: 16, uploadedAt: '2026-10-24T20:15:00+08:00' },
    { id: 'm-exercises', studySetId: 'network', title: '第三章课后习题.docx', fileType: 'DOC', chapter: '第三章', sourceType: '平时作业', status: 'ready', pageCount: 6, uploadedAt: '2026-10-22T12:00:00+08:00' },
    { id: 'm-image', studySetId: 'network', title: '期末重点截图.png', fileType: 'IMG', chapter: '综合复习', sourceType: '课堂重点', status: 'processing', progress: 60, pageCount: 1, uploadedAt: '2026-10-22T10:00:00+08:00' },
    { id: 'm-past', studySetId: 'network', title: '往年试卷扫描件.pdf', fileType: 'PDF', chapter: '历年真题', sourceType: '历年真题', status: 'failed', error: '部分页面识别失败', pageCount: 8, uploadedAt: '2026-10-20T10:00:00+08:00' },
  ];
  const sourceRefs = [
    { id: 's-slides', materialId: 'm-slides', pageStart: 12, pageEnd: 18, quote: '传输层提供进程之间的逻辑通信服务。TCP 提供可靠传输，UDP 提供开销较低的数据报服务。' },
    { id: 's-outline', materialId: 'm-outline', pageStart: 3, pageEnd: 5, quote: 'TCP 三次握手：客户端发送 SYN，服务端返回 SYN + ACK，客户端发送 ACK。' },
    { id: 's-exercises', materialId: 'm-exercises', pageStart: 1, pageEnd: 2, quote: '简述 TCP 与 UDP 的主要区别，并分别举例说明其适用场景。' },
  ];
  const sections = [
    { id: 'framework', title: '复习框架', html: '<p>传输层位于网络层之上，向上为应用层提供端到端的逻辑通信服务。复习时先抓住 <strong>进程之间的通信、复用与分用、可靠传输、流量控制与拥塞控制</strong> 这条主线。</p><p>本节先比较 TCP 与 UDP，再理解 TCP 的连接管理和控制机制。</p>' },
    { id: 'concepts', title: '核心概念', html: '<h3>传输层的功能</h3><ul><li><strong>端到端通信：</strong>为不同主机上的进程提供逻辑通信。</li><li><strong>复用与分用：</strong>通过端口标识不同应用进程。</li><li><strong>差错控制：</strong>TCP 通过确认、重传等机制保证可靠传输。</li><li><strong>流量控制：</strong>避免发送速度超过接收方处理能力。</li><li><strong>拥塞控制：</strong>根据网络负载调整发送速率。</li></ul>' },
    { id: 'compare', title: 'TCP 与 UDP 的对比', html: '<table><thead><tr><th>对比项</th><th>TCP</th><th>UDP</th></tr></thead><tbody><tr><td>连接方式</td><td>面向连接</td><td>无连接</td></tr><tr><td>可靠性</td><td>可靠传输</td><td>不保证可靠性</td></tr><tr><td>数据形式</td><td>字节流</td><td>数据报</td></tr><tr><td>流量/拥塞控制</td><td>有</td><td>协议本身不提供</td></tr><tr><td>常见应用</td><td>文件传输等</td><td>实时音视频等</td></tr></tbody></table>' },
    { id: 'examples', title: '典型考法', html: '<p><strong>选择题：</strong>识别连接方式、可靠性及端口的作用。</p><p><strong>简答题：</strong>说明三次握手的过程，比较流量控制与拥塞控制。</p><div class="tip">答题要点：比较题按“连接、可靠性、数据形式、控制机制、应用场景”组织答案。</div>' },
    { id: 'check', title: '考前自查', html: '<ul><li>能否解释端口与进程的关系？</li><li>能否画出三次握手的三个报文段？</li><li>能否区分接收方限制与网络拥塞？</li></ul>' },
  ];
  const notes = [
    { id: 'note-sprint', studySetId: 'network', title: '传输层考前冲刺笔记', type: '考前冲刺', status: 'confirmed', revision: 1, sections, sourceRefIds: sourceRefs.map(x => x.id), updatedAt: now },
    { id: 'note-tcp', studySetId: 'network', title: 'TCP 连接管理 · 核心概念', type: '知识整理', status: 'confirmed', revision: 1, sections: [sections[0], { id: 'handshake', title: '三次握手', html: '<p>客户端发送 SYN；服务端返回 SYN + ACK；客户端发送 ACK。连接建立过程同步双方初始序列号，并确认通信能力。</p>' }, sections[3]], sourceRefIds: ['s-outline', 's-slides'], updatedAt: '2026-10-24T16:22:00+08:00' },
    { id: 'note-draft', studySetId: 'network', title: '传输层习题得分要点', type: '考点清单', status: 'draft', revision: 1, sections: [sections[3], sections[4]], sourceRefIds: ['s-exercises'], updatedAt: '2026-10-23T15:30:00+08:00' },
  ];
  const transport = [
    ['传输层主要为哪类对象提供逻辑通信？', ['路由器端口', '应用进程', '物理链路', '交换机接口'], 1, '传输层提供进程之间的逻辑通信。', 'transport'],
    ['TCP 建立连接通常使用几次握手？', ['一次', '两次', '三次', '四次'], 2, 'TCP 通常使用三次握手建立连接。', 'tcp'],
    ['UDP 的数据传输形式是什么？', ['字节流', '数据报', '电信号', '固定长度帧'], 1, 'UDP 面向数据报，保留消息边界。', 'transport'],
    ['下列关于 TCP 与 UDP 的描述，哪一项正确？', ['两者都面向连接且可靠', 'UDP 通过三次握手建立连接', 'TCP 提供可靠传输及流量与拥塞控制，UDP 不保证可靠性', 'UDP 通过滑动窗口保证可靠传输'], 2, 'TCP 面向连接并提供可靠传输；UDP 本身不保证可靠性。', 'tcp'],
    ['端口号的主要作用是什么？', ['标识主机上的应用进程', '标识网卡制造商', '计算传播时延', '分配物理线路'], 0, '传输层通过端口区分不同应用进程。', 'transport'],
    ['TCP 流量控制主要避免什么问题？', ['接收方来不及处理', '光纤传播过慢', '路由表过长', 'IP 地址不足'], 0, '流量控制限制发送速度，避免超过接收方的处理能力。', 'tcp'],
    ['TCP 拥塞控制主要关注哪一方面？', ['用户界面布局', '网络中的负载状况', '文件的扩展名', '进程名称'], 1, '拥塞控制根据网络负载调整发送速率。', 'tcp'],
    ['TCP 提供哪一种数据服务？', ['无连接的数据报', '可靠的字节流', '固定大小消息', '广播帧'], 1, 'TCP 提供面向连接的可靠字节流服务。', 'tcp'],
    ['UDP 首部长度是多少字节？', ['4', '8', '20', '40'], 1, 'UDP 固定首部由源端口、目的端口、长度和校验和组成，共 8 字节。', 'transport'],
    ['三次握手中服务端通常对 SYN 返回什么？', ['FIN', 'RST', 'SYN + ACK', '只有数据'], 2, '服务端通常以 SYN + ACK 响应客户端的连接请求。', 'tcp'],
  ];
  const network = [
    ['IPv4 地址的长度是多少位？', ['16', '32', '64', '128'], 1, 'IPv4 地址长 32 位。', 'ip'],
    ['子网掩码用于区分地址中的哪两个部分？', ['源和目的', '网络和主机', '发送和接收', '文件和目录'], 1, '子网掩码区分网络部分与主机部分。', 'ip'],
    ['IPv4 的 /24 前缀包含多少位主机位？', ['8', '16', '24', '32'], 0, '32 - 24 = 8 位主机位。', 'ip'],
    ['路由器转发 IP 分组主要依据什么？', ['目的 IP 地址', '源文件名', '应用标题', '用户昵称'], 0, '路由器根据目的 IP 地址查询路由表。', 'routing'],
    ['多个路由前缀同时匹配时通常使用什么规则？', ['最短前缀', '最长前缀', '随机选择', '按接口名称'], 1, 'IP 路由查找通常采用最长前缀匹配。', 'routing'],
    ['IPv4 的 TTL 主要用于什么？', ['限制分组在网络中的存活跳数', '标识应用类型', '控制接收窗口', '加密内容'], 0, 'TTL 在转发时递减，防止分组无限循环。', 'routing'],
    ['ICMP 主要用于什么？', ['网络差错报告与控制信息', '文件压缩', '网页排版', '密码存储'], 0, 'ICMP 承载 IP 网络的差错报告和控制信息。', 'ip'],
    ['路由表中默认路由的前缀通常是什么？', ['0.0.0.0/0', '127.0.0.1/32', '255.255.255.255/32', '224.0.0.0/4'], 0, '0.0.0.0/0 用于没有更具体匹配时的默认转发。', 'routing'],
  ];
  const questions = [...transport.map((x, i) => ({ id: `t${i+1}`, data: x })), ...network.map((x, i) => ({ id: `n${i+1}`, data: x }))].map(({ id, data: [stem, options, correct, explanation, knowledgePointId] }) => ({ id, revision: 1, type: 'single_choice', stem, options, correct, explanation, knowledgePointId, points: 1, sourceRefIds: id.startsWith('t') ? ['s-slides'] : [] }));
  const quizzes = [
    { id: 'quiz-basic', studySetId: 'network', title: '传输层基础测试', type: '章节测试', status: 'confirmed', revision: 1, questionIds: transport.map((_, i) => `t${i+1}`), durationMinutes: 15, chapter: '第三章 传输层', materialIds: ['m-slides', 'm-outline'] },
    { id: 'quiz-chapter', studySetId: 'network', title: '第三章综合练习', type: '章节测试', status: 'confirmed', revision: 1, questionIds: transport.map((_, i) => `t${i+1}`), durationMinutes: 20, chapter: '第三章 传输层', materialIds: ['m-slides', 'm-exercises'] },
    { id: 'quiz-network', studySetId: 'network', title: '网络层模拟测试', type: '模拟测试', status: 'confirmed', revision: 1, questionIds: network.map((_, i) => `n${i+1}`), durationMinutes: 20, chapter: '第四章 网络层', materialIds: ['m-outline'] },
  ];
  function historical(id, date, wrongIndices) {
    const quiz = quizzes[2], answers = {};
    quiz.questionIds.forEach((qid, i) => { const q = questions.find(x => x.id === qid); answers[qid] = wrongIndices.includes(i) ? (q.correct + 1) % 4 : q.correct; });
    return { id, studySetId: 'network', quizId: quiz.id, quizRevision: 1, questionIds: [...quiz.questionIds], status: 'graded', answers, markedQuestionIds: [], currentIndex: 0, elapsedSeconds: 620, startedAt: `${date}T14:00:00+08:00`, submittedAt: `${date}T14:10:20+08:00` };
  }
  const attempts = [
    historical('attempt-old-1', '2026-10-20', [3,4]), historical('attempt-old-2', '2026-10-22', [3,4,5]), historical('attempt-old-3', '2026-10-24', [4]),
    { id: 'attempt-chapter', studySetId: 'network', quizId: 'quiz-chapter', quizRevision: 1, questionIds: [...quizzes[1].questionIds], status: 'in_progress', answers: { t1: 1, t2: 2, t3: 1, t4: 2, t5: 0, t6: 0 }, markedQuestionIds: ['t9'], currentIndex: 3, elapsedSeconds: 512, startedAt: '2026-10-25T14:10:00+08:00' },
  ];
  const exams = [{ id: 'exam-network', studySetId: 'network', title: '计算机网络期末考试', startsAt: '2026-11-06T09:00:00+08:00' }];
  const events = [
    { id: 'event-1', studySetId: 'network', title: '阅读传输层笔记', kind: 'reading', startsAt: '2026-10-25T10:00:00+08:00', endsAt: '2026-10-25T12:00:00+08:00', target: { type: 'note', id: 'note-sprint' } },
    { id: 'event-2', studySetId: 'english', title: '复习高频词汇', kind: 'review', startsAt: '2026-10-25T14:00:00+08:00', endsAt: '2026-10-25T15:00:00+08:00', target: { type: 'studySet', id: 'english' } },
    { id: 'event-3', studySetId: 'network', title: '完成第三章综合练习', kind: 'quiz', startsAt: '2026-10-29T14:00:00+08:00', endsAt: '2026-10-29T15:00:00+08:00', target: { type: 'quiz', id: 'quiz-chapter' } },
    { id: 'event-4', studySetId: 'network', title: '期末考试', kind: 'exam', startsAt: exams[0].startsAt, endsAt: '2026-11-06T11:00:00+08:00', target: { type: 'exam', id: 'exam-network' } },
  ];
  return { now, user: { id: 'demo-user', name: '林同学', timezone: 'Asia/Shanghai' }, studySets, materials, sourceRefs, notes, questions, quizzes, attempts, exams, events,
    knowledgePoints: [{ id: 'tcp', title: 'TCP 连接管理' }, { id: 'transport', title: '传输层基础' }, { id: 'ip', title: 'IP 地址与子网划分' }, { id: 'routing', title: '路由基础' }, { id: 'application', title: '应用层协议' }],
    reading: { noteId: 'note-sprint', sectionId: 'concepts', percent: 36 },
    messages: [{ role: 'user', content: 'TCP 三次握手为什么不能只进行两次？' }, { role: 'assistant', content: '建立连接需要同步双方的初始序列号，并让双方确认发送与接收能力。三次握手还能避免已失效的连接请求导致服务端错误建立连接。\n可以把过程记为：SYN → SYN + ACK → ACK。', sourceRefIds: ['s-outline', 's-slides'] }],
  };
})();
