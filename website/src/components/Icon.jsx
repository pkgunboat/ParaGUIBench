import React from "react";

const paths = {
  arrow: <path d="m9 18 6-6-6-6M4 12h11" />,
  box: <path d="m12 2 8 4.5v9L12 20l-8-4.5v-9L12 2Zm0 9 8-4.5M12 11 4 6.5M12 11v9" />,
  browser: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 8h18M7 6h.01M10 6h.01" />
    </>
  ),
  check: <path d="m5 12 4 4L19 6" />,
  chevron: <path d="m9 18 6-6-6-6" />,
  close: <path d="M5 5l14 14M19 5 5 19" />,
  code: <path d="m8 9-3 3 3 3m8-6 3 3-3 3m-3-9-2 12" />,
  copy: (
    <>
      <rect x="8" y="8" width="11" height="12" rx="2" />
      <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h2" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="5" rx="7" ry="3" />
      <path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
    </>
  ),
  desktop: (
    <>
      <rect x="3" y="3" width="18" height="13" rx="2" />
      <path d="M8 21h8M12 16v5" />
    </>
  ),
  folder: <path d="M3 6h7l2 2h9v11H3V6Z" />,
  github: (
    <path
      d="M12 2.7a9.3 9.3 0 0 0-2.94 18.12c.47.08.64-.2.64-.45v-1.8c-2.62.57-3.17-1.11-3.17-1.11-.43-1.09-1.05-1.38-1.05-1.38-.86-.59.06-.58.06-.58.95.07 1.45.98 1.45.98.85 1.45 2.22 1.03 2.76.79.09-.61.33-1.03.6-1.27-2.09-.24-4.29-1.05-4.29-4.65 0-1.03.37-1.87.98-2.53-.1-.24-.43-1.2.09-2.49 0 0 .8-.26 2.56.97A8.9 8.9 0 0 1 12 6.99a8.9 8.9 0 0 1 2.33.31c1.77-1.2 2.56-.97 2.56-.97.52 1.29.19 2.25.09 2.49.61.66.98 1.5.98 2.53 0 3.61-2.2 4.4-4.3 4.64.34.29.64.86.64 1.74v2.64c0 .25.17.54.65.45A9.3 9.3 0 0 0 12 2.7Z"
      fill="currentColor"
      stroke="none"
    />
  ),
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  planner: (
    <>
      <circle cx="12" cy="5" r="2" />
      <circle cx="5" cy="18" r="2" />
      <circle cx="12" cy="18" r="2" />
      <circle cx="19" cy="18" r="2" />
      <path d="M12 7v5M5 16v-4h14v4M12 12v4" />
    </>
  ),
  scales: <path d="M12 3v18M6 6h12M5 6l-3 6h6L5 6Zm14 0-3 6h6l-3-6ZM8 21h8" />,
  search: <path d="m21 21-4.4-4.4M10.8 18a7.2 7.2 0 1 1 0-14.4 7.2 7.2 0 0 1 0 14.4Z" />,
  warning: <path d="M12 3 2.8 20h18.4L12 3Zm0 6v5m0 3h.01" />,
};

/**
 * 渲染站点内统一的线性图标。
 *
 * @param {{name: keyof typeof paths, size?: number, className?: string, title?: string}} props - 图标名称、尺寸和可选的无障碍标题。
 * @returns {React.ReactElement} 使用 currentColor 的内联 SVG；装饰性图标默认对读屏器隐藏。
 */
export function Icon({ name, size = 20, className = "", title }) {
  return (
    <svg
      aria-hidden={title ? undefined : "true"}
      aria-label={title}
      className={className}
      fill="none"
      height={size}
      role={title ? "img" : undefined}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.7"
      viewBox="0 0 24 24"
      width={size}
    >
      {title ? <title>{title}</title> : null}
      {paths[name]}
    </svg>
  );
}
