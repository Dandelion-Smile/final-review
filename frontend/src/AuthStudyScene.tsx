import studyScene from "./assets/auth-study-scene.webp";

export default function AuthStudyScene() {
  return <div className="auth-scene" aria-hidden="true">
    <div className="auth-scene-art">
      <img src={studyScene} alt="" width="1713" height="918" fetchPriority="high" />
      <img className="auth-scene-breeze" src={studyScene} alt="" width="1713" height="918" />
      <svg className="auth-scene-motion" viewBox="0 0 1713 918" focusable="false">
        <defs>
          <filter id="auth-steam-softness" x="-45%" y="-25%" width="190%" height="150%">
            <feGaussianBlur stdDeviation="3.6" />
          </filter>
          <linearGradient id="auth-steam-light" x1="0" y1="1" x2="0" y2="0">
            <stop stopColor="#f3e8d5" stopOpacity=".7" />
            <stop offset=".55" stopColor="#d2d5ca" stopOpacity=".52" />
            <stop offset="1" stopColor="#c8d4d7" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="auth-lifted-paper" x1="1" y1="1" x2=".17" y2="0">
            <stop stopColor="#a4937c" />
            <stop offset=".35" stopColor="#d2c0a4" />
            <stop offset=".79" stopColor="#ede0c9" />
            <stop offset="1" stopColor="#f3e6d3" />
          </linearGradient>
        </defs>
        <g fill="none" stroke="url(#auth-steam-light)" strokeLinecap="round" filter="url(#auth-steam-softness)">
          <path className="auth-steam" strokeWidth="10" d="M382 694 C401 677 369 666 377 648 S411 626 390 602 C378 589 383 578 390 573" />
          <path className="auth-steam auth-steam-left" strokeWidth="8" d="M375 695 C385 680 354 669 360 655 S382 636 369 620 C359 608 366 597 373 591" />
          <path className="auth-steam auth-steam-right" strokeWidth="6" d="M391 696 C411 678 392 672 394 659 S420 638 406 617" />
        </g>
        <g className="auth-book-motion">
          <path className="auth-book-shadow" fill="#685743" d="M738 800 C689 784 617 787 580 801 L519 857 C588 846 683 837 726 862 Z" />
          <path className="auth-book-leaf" fill="url(#auth-lifted-paper)" stroke="#e7d8bf" strokeWidth=".8" />
        </g>
      </svg>
    </div>
  </div>;
}
