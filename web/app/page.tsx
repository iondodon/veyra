'use client';

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';

type Section = 'Overview' | 'Conversations' | 'Evolution' | 'Memory' | 'System';

const navItems: { label: Section; number: string; symbol: string }[] = [
  { label: 'Overview', number: '01', symbol: '⌂' },
  { label: 'Conversations', number: '02', symbol: '◫' },
  { label: 'Evolution', number: '03', symbol: '↗' },
  { label: 'Memory', number: '04', symbol: '◇' },
  { label: 'System', number: '05', symbol: '⌁' },
];

type RuntimeStatus = {
  schema_version: number;
  updated_at: string;
  agent: { state: 'running' | 'restarting' | 'stopped'; pid: number | null };
  revision: {
    commit: string;
    short: string;
    branch: string;
    message: string;
    committed_at: string | null;
  };
  runtime: {
    provider: string | null;
    model: string | null;
    recent_message_count: number;
  };
  versions: { commit: string; message: string; committed_at: string }[];
};

function titleCase(value: string | null | undefined, fallback = 'Not selected') {
  if (!value) return fallback;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function relativeTime(value: string | null | undefined) {
  if (!value) return 'Unknown time';
  const elapsed = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(elapsed)) return 'Unknown time';
  const minutes = Math.max(0, Math.floor(elapsed / 60_000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

const sectionIntro: Record<Section, { eyebrow: string; title: string; copy: string }> = {
  Overview: {
    eyebrow: 'Local intelligence / Overview',
    title: 'Your agent, at a glance.',
    copy: 'See the current version, runtime state, model, and the local knowledge Veyra carries forward.',
  },
  Conversations: {
    eyebrow: 'Communication / Conversations',
    title: 'The thread stays close.',
    copy: 'Veyra uses Telegram as its owner-only interface and keeps a bounded local context for continuity.',
  },
  Evolution: {
    eyebrow: 'Versions / Evolution',
    title: 'Every change has a history.',
    copy: 'Each durable version is an ordinary Git commit, tested before the supervisor activates it.',
  },
  Memory: {
    eyebrow: 'Knowledge / Memory',
    title: 'Small, durable, local.',
    copy: 'Preferences and runtime choices live beside the agent without becoming part of its source history.',
  },
  System: {
    eyebrow: 'Operations / System',
    title: 'A clear local boundary.',
    copy: 'Review the supervisor state, provider readiness, and private-network access from one place.',
  },
};

function copyText(value: string, onDone: () => void) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(value).then(onDone).catch(() => fallbackCopy(value, onDone));
    return;
  }
  fallbackCopy(value, onDone);
}

function fallbackCopy(value: string, onDone: () => void) {
  const field = document.createElement('textarea');
  field.value = value;
  field.style.position = 'fixed';
  field.style.opacity = '0';
  document.body.appendChild(field);
  field.select();
  document.execCommand('copy');
  field.remove();
  onDone();
}

export default function Home() {
  const [section, setSection] = useState<Section>('Overview');
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [mobileOpen, setMobileOpen] = useState(false);
  const [toast, setToast] = useState('');
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [statusError, setStatusError] = useState(false);
  const intro = sectionIntro[section];

  const refreshStatus = useCallback(async () => {
    try {
      const response = await fetch(`/veyra-status.json?t=${Date.now()}`, {
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`status ${response.status}`);
      const value = (await response.json()) as RuntimeStatus;
      if (value.schema_version !== 1) throw new Error('unsupported status');
      setRuntime(value);
      setStatusError(false);
    } catch {
      setStatusError(true);
    }
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearchOpen((open) => !open);
      }
      if (event.key === 'Escape') {
        setSearchOpen(false);
        setMobileOpen(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refreshStatus(), 0);
    const interval = window.setInterval(() => void refreshStatus(), 3000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(interval);
    };
  }, [refreshStatus]);

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(''), 2300);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const filteredNav = useMemo(
    () => navItems.filter(({ label }) => label.toLowerCase().includes(query.toLowerCase())),
    [query],
  );

  const navigate = (next: Section) => {
    setSection(next);
    setSearchOpen(false);
    setMobileOpen(false);
    setQuery('');
  };

  const copy = (value: string, message: string) => copyText(value, () => setToast(message));
  const agentRunning = runtime?.agent.state === 'running';
  const agentState = runtime?.agent.state ?? 'stopped';

  return (
    <main className="app-shell">
      <aside className={mobileOpen ? 'sidebar mobile-open' : 'sidebar'}>
        <div className="brand-lockup">
          <span className="brand-mark">v</span>
          <span>veyra</span>
          <button className="sidebar-close" onClick={() => setMobileOpen(false)} aria-label="Close menu">×</button>
        </div>

        <nav aria-label="Main navigation" className="main-nav">
          <p className="nav-label">Workspace</p>
          {navItems.map(({ label, number, symbol }) => (
            <button
              className={section === label ? 'nav-item active' : 'nav-item'}
              key={label}
              onClick={() => navigate(label)}
            >
              <span className="nav-main"><i aria-hidden="true">{symbol}</i>{label}</span>
              <span className="nav-number">{number}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-meta">
          <p className="nav-label">Local instance</p>
          <button className="mini-row" onClick={() => navigate('System')}>
            <span className={`status-dot ${agentRunning ? '' : agentState}`} />
            <span>
              <b>{runtime ? `Agent ${agentState}` : 'Reading agent state'}</b>
              <small>{agentRunning ? `PID ${runtime.agent.pid}` : statusError ? 'Live status unavailable' : 'Waiting for supervisor'}</small>
            </span>
            <span>→</span>
          </button>
        </div>

        <div className="agent-card">
          <span className="card-glyph">v/</span>
          <p>Private by design.</p>
          <small>This dashboard listens on your local network and has not been published.</small>
        </div>
      </aside>

      {mobileOpen && <button className="sidebar-backdrop" onClick={() => setMobileOpen(false)} aria-label="Close menu" />}

      <section className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            <button className="menu-trigger" onClick={() => setMobileOpen(true)} aria-label="Open menu">☰</button>
            <button className="search-trigger" onClick={() => setSearchOpen(true)} aria-label="Open search">
              <span className="search-icon">⌕</span>
              <span>Search Veyra</span>
              <kbd>⌘ K</kbd>
            </button>
          </div>
          <div className="topbar-actions">
            <button className="sync-button" onClick={() => void refreshStatus()}><span>↻</span> Refresh live data</button>
            <button className="quiet-button" aria-label="About this dashboard" title="Local dashboard">i</button>
            <div className="owner-avatar" aria-label="Owner profile">ID</div>
          </div>
        </header>

        <div className="content">
          <div className="eyebrow-row">
            <p className="eyebrow">{intro.eyebrow}</p>
            <p className={`snapshot-label ${statusError ? 'status-error' : ''}`}>
              <span /> {statusError ? 'Live data unavailable' : runtime ? `Updated ${relativeTime(runtime.updated_at)}` : 'Connecting…'}
            </p>
          </div>

          <section className="hero-row">
            <div>
              <h1>{intro.title}</h1>
              <p>{intro.copy}</p>
            </div>
            {section === 'System' ? (
              <button className="primary-button" onClick={() => copy('./supervisor/supervisor', 'Startup command copied')}><span>↗</span> Copy start command</button>
            ) : (
              <button className="primary-button" onClick={() => navigate('System')}><span>+</span> Open local status</button>
            )}
          </section>

          {section === 'Overview' && <Overview runtime={runtime} onNavigate={navigate} onCopy={copy} />}
          {section === 'Conversations' && <Conversations runtime={runtime} onCopy={copy} />}
          {section === 'Evolution' && <Evolution runtime={runtime} />}
          {section === 'Memory' && <Memory runtime={runtime} />}
          {section === 'System' && <System runtime={runtime} onCopy={copy} />}
        </div>
      </section>

      {searchOpen && (
        <div className="command-layer" role="presentation" onMouseDown={() => setSearchOpen(false)}>
          <section className="command-palette" role="dialog" aria-modal="true" aria-label="Search Veyra" onMouseDown={(e) => e.stopPropagation()}>
            <form onSubmit={(event: FormEvent) => event.preventDefault()}>
              <span>⌕</span>
              <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Where do you want to go?" aria-label="Search sections" />
              <kbd>esc</kbd>
            </form>
            <p className="command-label">Navigate</p>
            <div className="command-results">
              {filteredNav.map(({ label, symbol }) => (
                <button key={label} onClick={() => navigate(label)}><span className="result-icon">{symbol}</span><span>{label}<small>{sectionIntro[label].eyebrow.split(' / ')[0]}</small></span><i>↵</i></button>
              ))}
              {!filteredNav.length && <p className="empty-result">No matching section.</p>}
            </div>
            <footer><span>Veyra local dashboard</span><span><kbd>↑</kbd><kbd>↓</kbd> to move</span></footer>
          </section>
        </div>
      )}

      {toast && <div className="toast" role="status"><span>✓</span>{toast}</div>}
    </main>
  );
}

function Overview({ runtime, onNavigate, onCopy }: { runtime: RuntimeStatus | null; onNavigate: (section: Section) => void; onCopy: (value: string, message: string) => void }) {
  const running = runtime?.agent.state === 'running';
  const state = runtime?.agent.state ?? 'loading';
  const versions = runtime?.versions ?? [];
  const provider = titleCase(runtime?.runtime.provider);
  const model = runtime?.runtime.model ?? 'Not selected';

  return (
    <>
      <section className="metric-grid" aria-label="Agent metrics">
        <article className="metric-card dark-card">
          <div className="metric-head"><span>Agent state</span><span className="metric-icon">⌁</span></div>
          <strong>{titleCase(state, 'Loading…')}</strong>
          <p><span className={`status-dot ${running ? '' : state}`} /> {running ? `Supervisor process ${runtime.agent.pid}` : runtime ? 'Supervisor is restarting the agent' : 'Reading live supervisor state'}</p>
        </article>
        <article className="metric-card">
          <div className="metric-head"><span>Recent context</span><span className="metric-icon">◫</span></div>
          <strong>{runtime?.runtime.recent_message_count ?? '—'}</strong>
          <p>messages retained locally</p>
        </article>
        <article className="metric-card">
          <div className="metric-head"><span>Current version</span><span className="metric-icon">↗</span></div>
          <strong className="mono-value">{runtime?.revision.short ?? '—'}</strong>
          <p>latest checked-out commit</p>
        </article>
        <article className="metric-card accent-card">
          <div className="metric-head"><span>Selected model</span><span className="metric-icon">✦</span></div>
          <strong>{model}</strong>
          <p>{provider} · {runtime?.runtime.model ? 'configured' : 'not configured'}</p>
        </article>
      </section>

      <section className="overview-grid">
        <article className="panel readiness-panel">
          <div className="panel-head">
            <div><p className="panel-kicker">Readiness</p><h2>Local runtime</h2></div>
            <button className="text-button" onClick={() => onNavigate('System')}>Full status <span>→</span></button>
          </div>
          <div className="readiness-list">
            <StatusRow label="Provider selected" detail={provider} state={runtime?.runtime.provider ? 'ready' : 'attention'} />
            <StatusRow label="Model configured" detail={model} state={runtime?.runtime.model ? 'ready' : 'attention'} />
            <StatusRow label="Agent process" detail={running ? `Running as PID ${runtime.agent.pid}` : titleCase(state)} state={running ? 'ready' : 'attention'} />
            <StatusRow label="Dashboard exposure" detail="Private LAN only" state="ready" />
          </div>
          <button className="command-strip" onClick={() => onCopy('./supervisor/supervisor', 'Startup command copied')}>
            <code>./supervisor/supervisor</code><span>Copy start command</span>
          </button>
        </article>

        <article className="panel version-preview">
          <div className="panel-head">
            <div><p className="panel-kicker">Evolution</p><h2>Current version</h2></div>
            <span className="safe-badge">Live HEAD</span>
          </div>
          <div className="version-number">{runtime?.revision.short ?? 'Loading…'}</div>
          <p className="commit-message">{runtime?.revision.message || 'Reading the current commit message…'}</p>
          <div className="version-meta"><span>{runtime ? `Committed ${relativeTime(runtime.revision.committed_at)}` : 'Loading time'}</span><span>{runtime?.revision.branch ?? '—'}</span></div>
          <button className="version-button" onClick={() => onNavigate('Evolution')}>View evolution history <span>→</span></button>
        </article>
      </section>

      <section className="panel recent-panel">
        <div className="panel-head">
          <div><p className="panel-kicker">Recent changes</p><h2>How Veyra has evolved</h2></div>
          <button className="text-button" onClick={() => onNavigate('Evolution')}>See all versions <span>→</span></button>
        </div>
        <div className="recent-list">
          {versions.slice(0, 3).map((version, index) => (
            <div className="recent-row" key={version.commit}>
              <span className={index === 0 ? 'timeline-dot newest' : 'timeline-dot'} />
              <code>{version.commit}</code><p>{version.message}</p><time>{relativeTime(version.committed_at)}</time>
            </div>
          ))}
          {!versions.length && <p className="loading-copy">Reading version history…</p>}
        </div>
      </section>
    </>
  );
}

function Conversations({ runtime, onCopy }: { runtime: RuntimeStatus | null; onCopy: (value: string, message: string) => void }) {
  const messageCount = runtime?.runtime.recent_message_count;

  return (
    <section className="two-column-page">
      <article className="panel feature-panel conversation-feature">
        <div className="feature-glyph">tg/</div>
        <p className="panel-kicker">Primary interface</p>
        <h2>Telegram keeps Veyra within reach.</h2>
        <p className="feature-copy">The owner can talk to the agent, switch providers or models, and approve staged desktop actions without opening this dashboard.</p>
        <div className="detail-pills"><span>Owner-only</span><span>Long polling</span><span>{messageCount ?? '—'} messages retained</span></div>
        <button className="primary-button compact" onClick={() => onCopy('/provider', 'Provider command copied')}><span>+</span> Copy provider command</button>
      </article>
      <div className="stacked-panels">
        <article className="panel compact-panel">
          <div className="panel-head"><div><p className="panel-kicker">Local context</p><h2>Recent messages</h2></div><strong className="big-number">{messageCount ?? '—'}</strong></div>
          <div className="progress-track"><span style={{ width: messageCount == null ? '0%' : `${Math.min(100, messageCount * 5)}%` }} /></div>
          <p className="support-copy">Veyra keeps a bounded recent history locally for conversational continuity.</p>
        </article>
        <article className="panel compact-panel">
          <p className="panel-kicker">Available commands</p>
          <div className="command-list">
            {[['/provider', 'Choose OpenAI or Anthropic'], ['/model', 'Change the active model'], ['/codex', 'Stage a Codex prompt'], ['/claude', 'Stage a Claude prompt']].map(([command, detail]) => (
              <button key={command} onClick={() => onCopy(command, `${command} copied`)}><code>{command}</code><span>{detail}</span><i>+</i></button>
            ))}
          </div>
        </article>
      </div>
    </section>
  );
}

function Evolution({ runtime }: { runtime: RuntimeStatus | null }) {
  const versions = runtime?.versions ?? [];

  return (
    <section className="evolution-layout">
      <article className="panel evolution-list-panel">
        <div className="panel-head"><div><p className="panel-kicker">Version history</p><h2>Recent commits</h2></div><span className="branch-badge">{runtime?.revision.branch ?? '—'}</span></div>
        <div className="commit-timeline">
          {versions.map((version, index) => (
            <div className="commit-row" key={version.commit}>
              <div className="commit-rail"><span className={runtime?.revision.commit.startsWith(version.commit) ? 'commit-dot current' : 'commit-dot'} /></div>
              <div className="commit-body"><div><p>{version.message}</p>{runtime?.revision.commit.startsWith(version.commit) && <span>Active</span>}{index === 0 && !runtime?.revision.commit.startsWith(version.commit) && <span>HEAD</span>}</div><code>{version.commit}</code><time>{relativeTime(version.committed_at)}</time></div>
            </div>
          ))}
          {!versions.length && <p className="loading-copy">Reading Git history…</p>}
        </div>
      </article>
      <aside className="evolution-aside">
        <article className="panel principle-card dark-card">
          <span className="feature-glyph lime">git/</span>
          <h2>Version means commit.</h2>
          <p>Veyra never invents version identifiers. The checked-out commit is the version, and HEAD is the version pointer.</p>
        </article>
        <article className="panel compact-panel">
          <p className="panel-kicker">Activation path</p>
          <ol className="activation-list"><li><span>01</span>Commit detected</li><li><span>02</span>Self-test runs</li><li><span>03</span>Supervisor switches</li></ol>
        </article>
      </aside>
    </section>
  );
}

function Memory({ runtime }: { runtime: RuntimeStatus | null }) {
  return (
    <>
      <section className="memory-grid">
        <article className="panel memory-card"><span className="memory-icon">◎</span><p className="panel-kicker">Provider</p><h2>{titleCase(runtime?.runtime.provider)}</h2><p>Selected provider for the local instance.</p><code>provider.json</code></article>
        <article className="panel memory-card accent-memory"><span className="memory-icon">✦</span><p className="panel-kicker">Model</p><h2>{runtime?.runtime.model ?? 'Not selected'}</h2><p>Remembered separately for each provider.</p><code>models.json</code></article>
        <article className="panel memory-card"><span className="memory-icon">◫</span><p className="panel-kicker">Recent context</p><h2>{runtime ? `${runtime.runtime.recent_message_count} messages` : 'Loading…'}</h2><p>Bounded conversation history stored locally.</p><code>recent_messages.json</code></article>
      </section>
      <section className="panel boundary-panel">
        <div><p className="panel-kicker">A useful boundary</p><h2>Memory is state, not source.</h2></div>
        <p>Runtime choices and conversation context remain under <code>state/</code>. They survive restarts, but they are ignored by Git and never become part of Veyra’s initial version.</p>
        <div className="boundary-mark">local<br />only</div>
      </section>
    </>
  );
}

function System({ runtime, onCopy }: { runtime: RuntimeStatus | null; onCopy: (value: string, message: string) => void }) {
  const running = runtime?.agent.state === 'running';

  return (
    <section className="system-grid">
      <article className="panel system-status-card dark-card">
        <div className="system-orbit"><span>v</span></div>
        <p className="panel-kicker">Supervisor</p>
        <h2>{runtime ? `Veyra is ${runtime.agent.state}.` : 'Reading Veyra state…'}</h2>
        <p>{running ? `The supervisor owns agent process ${runtime.agent.pid} and will restart it if needed.` : runtime ? 'The supervisor is transitioning the agent process.' : 'Waiting for the supervisor runtime snapshot.'}</p>
        <button className="light-button" onClick={() => onCopy('./supervisor/supervisor', 'Startup command copied')}>Copy startup command <span>→</span></button>
      </article>
      <div className="system-details">
        <article className="panel compact-panel">
          <div className="panel-head"><div><p className="panel-kicker">Network</p><h2>Private access</h2></div><span className="safe-badge">LAN only</span></div>
          <dl className="detail-list"><div><dt>Listen address</dt><dd>0.0.0.0</dd></div><div><dt>Dashboard port</dt><dd>3000</dd></div><div><dt>Public deployment</dt><dd>None</dd></div></dl>
          <button className="version-button" onClick={() => onCopy(window.location.origin, 'Dashboard address copied')}>Copy this dashboard address <span>↗</span></button>
        </article>
        <article className="panel compact-panel">
          <p className="panel-kicker">Safety checks</p>
          <div className="readiness-list small">
            <StatusRow label="Single-instance lock" detail="Enforced" state="ready" />
            <StatusRow label="Agent self-test" detail="Required" state="ready" />
            <StatusRow label="Checked-out version" detail={runtime?.revision.short ?? 'Reading…'} state={runtime ? 'ready' : 'attention'} />
          </div>
        </article>
      </div>
    </section>
  );
}

function StatusRow({ label, detail, state }: { label: string; detail: string; state: 'ready' | 'attention' }) {
  return <div className="status-row"><span className={`check-dot ${state}`}>{state === 'ready' ? '✓' : '!'}</span><p><b>{label}</b><small>{detail}</small></p><span className="row-arrow">→</span></div>;
}
