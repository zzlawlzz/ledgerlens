// Inline SVG icon set (T-042) — no icon library. Stroke uses currentColor so
// CSS drives colour; size via the `.icon` class (1em) unless overridden.
import type { ReactNode } from "react";

import type { ToolKind } from "./narration";

type IconProps = { className?: string };

function Svg({
  children,
  className,
  fill = "none",
}: {
  children: ReactNode;
  className?: string;
  fill?: string;
}) {
  return (
    <svg
      className={className ? `icon ${className}` : "icon"}
      viewBox="0 0 24 24"
      fill={fill}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export const IconCheck = ({ className }: IconProps) => (
  <Svg className={className}>
    <polyline points="20 6 9 17 4 12" />
  </Svg>
);

export const IconX = ({ className }: IconProps) => (
  <Svg className={className}>
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </Svg>
);

export const IconClock = ({ className }: IconProps) => (
  <Svg className={className}>
    <circle cx="12" cy="12" r="9" />
    <polyline points="12 7 12 12 15 14" />
  </Svg>
);

export const IconWarn = ({ className }: IconProps) => (
  <Svg className={className}>
    <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </Svg>
);

export const IconSpinner = ({ className }: IconProps) => (
  <Svg className={className ? `spin ${className}` : "spin"}>
    <path d="M21 12a9 9 0 1 1-6.22-8.56" />
  </Svg>
);

export const IconDatabase = ({ className }: IconProps) => (
  <Svg className={className}>
    <ellipse cx="12" cy="5" rx="8" ry="3" />
    <path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
    <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
  </Svg>
);

export const IconDoc = ({ className }: IconProps) => (
  <Svg className={className}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="8" y1="13" x2="16" y2="13" />
    <line x1="8" y1="17" x2="13" y2="17" />
  </Svg>
);

export const IconChart = ({ className }: IconProps) => (
  <Svg className={className}>
    <polyline points="3 17 9 11 13 15 21 7" />
    <polyline points="16 7 21 7 21 12" />
  </Svg>
);

export const IconGlobe = ({ className }: IconProps) => (
  <Svg className={className}>
    <circle cx="12" cy="12" r="9" />
    <line x1="3" y1="12" x2="21" y2="12" />
    <path d="M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18z" />
  </Svg>
);

export const IconWrench = ({ className }: IconProps) => (
  <Svg className={className}>
    <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2-2 2.6-2.6z" />
  </Svg>
);

export const IconSun = ({ className }: IconProps) => (
  <Svg className={className}>
    <circle cx="12" cy="12" r="4" />
    <line x1="12" y1="2" x2="12" y2="5" />
    <line x1="12" y1="19" x2="12" y2="22" />
    <line x1="2" y1="12" x2="5" y2="12" />
    <line x1="19" y1="12" x2="22" y2="12" />
    <line x1="4.9" y1="4.9" x2="7" y2="7" />
    <line x1="17" y1="17" x2="19.1" y2="19.1" />
    <line x1="4.9" y1="19.1" x2="7" y2="17" />
    <line x1="17" y1="7" x2="19.1" y2="4.9" />
  </Svg>
);

export const IconMoon = ({ className }: IconProps) => (
  <Svg className={className} fill="currentColor">
    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" stroke="none" />
  </Svg>
);

export const IconSend = ({ className }: IconProps) => (
  <Svg className={className}>
    <line x1="22" y1="2" x2="11" y2="13" />
    <polygon points="22 2 15 22 11 13 2 9 22 2" />
  </Svg>
);

export const IconRetry = ({ className }: IconProps) => (
  <Svg className={className}>
    <polyline points="23 4 23 10 17 10" />
    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
  </Svg>
);

export const IconBulb = ({ className }: IconProps) => (
  <Svg className={className}>
    <path d="M9 18h6" />
    <path d="M10 22h4" />
    <path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5.76.76 1.23 1.52 1.41 2.5" />
  </Svg>
);

export const IconSparkle = ({ className }: IconProps) => (
  <Svg className={className} fill="currentColor">
    <path
      d="M12 2.5l1.9 5.1a2 2 0 0 0 1.2 1.2l5.1 1.9-5.1 1.9a2 2 0 0 0-1.2 1.2L12 18.9l-1.9-5.1a2 2 0 0 0-1.2-1.2L3.8 10.7l5.1-1.9a2 2 0 0 0 1.2-1.2z"
      stroke="none"
    />
  </Svg>
);

export const IconUser = ({ className }: IconProps) => (
  <Svg className={className}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1" />
  </Svg>
);

export const IconExternal = ({ className }: IconProps) => (
  <Svg className={className}>
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    <polyline points="15 3 21 3 21 9" />
    <line x1="10" y1="14" x2="21" y2="3" />
  </Svg>
);

/** Tool icon keyed by display kind. */
export function ToolIcon({ kind, className }: { kind: ToolKind; className?: string }) {
  switch (kind) {
    case "sql":
      return <IconDatabase className={className} />;
    case "rag":
      return <IconDoc className={className} />;
    case "enrich":
      return <IconChart className={className} />;
    case "web":
      return <IconGlobe className={className} />;
    default:
      return <IconWrench className={className} />;
  }
}

/** Plan-step status icon. */
export function StatusIcon({ status, className }: { status: string; className?: string }) {
  if (status === "done" || status === "succeeded") return <IconCheck className={className} />;
  if (status === "failed") return <IconX className={className} />;
  if (status === "no_data") return <IconWarn className={className} />;
  if (status === "running") return <IconSpinner className={className} />;
  return <IconClock className={className} />;
}
