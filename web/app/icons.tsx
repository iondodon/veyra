import type { SVGProps, ReactNode } from 'react';

export type IconName = 'chat' | 'screen' | 'branch' | 'memory' | 'runtime' | 'copy' | 'telegram' | 'refresh' | 'check' | 'close' | 'play' | 'pause' | 'expand' | 'code' | 'terminal';
const paths: Record<IconName, ReactNode> = {
  chat: <><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8z" /><path d="M8 10h8M8 14h5" /></>,
  screen: <><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8m-4-4v4" /></>,
  branch: <><circle cx="6" cy="5" r="3" /><circle cx="18" cy="6" r="3" /><circle cx="6" cy="19" r="3" /><path d="M6 8v8m0-4h6a6 6 0 0 0 6-6" /></>,
  memory: <><path d="m12 3 9 5-9 5-9-5 9-5zm-9 9 9 5 9-5M3 16l9 5 9-5" /></>,
  runtime: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h4" /></>,
  telegram: <path d="m21 3-6 18-4-7-8-4 18-7zm0 0L11 14" />,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.1 6.1a8 8 0 0 1 13.2 3.4M4.7 14.5a8 8 0 0 0 13.2 3.4" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  play: <path d="m8 4 12 8-12 8V4z" />,
  pause: <path d="M8 4v16M16 4v16" />,
  expand: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />,
  code: <path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18" />,
  terminal: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 8 4 4-4 4m6 0h4" /></>,
};
export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
export function Mark({ small = false }: { small?: boolean }) {
  return <span className={`veyra-mark${small ? ' small' : ''}`} aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="m7 8 9 17L25 8M16 8h9v9" stroke="currentColor" strokeWidth="4" /></svg></span>;
}
