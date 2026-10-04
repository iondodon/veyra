'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { jpegFrames } from '../lib/mjpeg';
import { Icon, Mark, type IconName } from './icons';
import { isConversationContext, isRuntimeStatus, isScreenStatus, isRuntimeStale, type ConversationContext, type RuntimeStatus, type ScreenStatus } from '../lib/veyra';

type View = 'Conversation' | 'Desktop' | 'Evolution' | 'Memory' | 'Runtime';
type DataState = 'loading' | 'ready' | 'error';
type Copy = (value: string, message?: string) => Promise<void>;
const views: { label: View; icon: IconName }[] = [
  { label: 'Conversation', icon: 'chat' }, { label: 'Desktop', icon: 'screen' },
  { label: 'Evolution', icon: 'branch' }, { label: 'Memory', icon: 'memory' }, { label: 'Runtime', icon: 'runtime' },
];
const botUsername = (process.env.NEXT_PUBLIC_TELEGRAM_BOT_USERNAME ?? '').replace(/^@/, '');
const telegramUrl = /^[a-zA-Z0-9_]{5,32}$/.test(botUsername) ? `https://t.me/${botUsername}` : 'https://web.telegram.org/';
const IDLE_PIXEL = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

function relativeTime(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Time unavailable';
  const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 60_000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
}
function formatDate(value: string | null | undefined) {
  return value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Time unavailable';
}
function providerName(value: string | null | undefined) {
  return value === 'openai' ? 'OpenAI' : value === 'anthropic' ? 'Anthropic' : value || 'Not selected';
}

function useLocalState() {
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [context, setContext] = useState<ConversationContext | null>(null);
  const [runtimeState, setRuntimeState] = useState<DataState>('loading');
  const [contextState, setContextState] = useState<DataState>('loading');
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    if (controller.current) return;
    const request = new AbortController();
    controller.current = request;
    setRefreshing(true);
    const get = async (path: string) => {
      const response = await fetch(path, { cache: 'no-store', signal: AbortSignal.any([request.signal, AbortSignal.timeout(8000)]) });
      if (!response.ok) throw new Error('Local state unavailable');
      return response.json();
    };
    const [status, memory] = await Promise.allSettled([get('/veyra-status.json'), get('/veyra-context.json')]);
    if (!request.signal.aborted) {
      if (status.status === 'fulfilled' && isRuntimeStatus(status.value)) { setRuntime(status.value); setRuntimeState('ready'); }
      else setRuntimeState('error');
      if (memory.status === 'fulfilled' && isConversationContext(memory.value)) { setContext(memory.value); setContextState('ready'); }
      else setContextState('error');
      setNow(Date.now());
      setRefreshing(false);
    }
    if (controller.current === request) controller.current = null;
  }, []);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { await refresh(); if (alive) timer = setTimeout(poll, 3000); };
    void poll();
    return () => { alive = false; clearTimeout(timer); controller.current?.abort(); controller.current = null; };
  }, [refresh]);
  const stale = !!runtime && isRuntimeStale(runtime, now);
  return { runtime, context, runtimeState, contextState, refresh, refreshing, stale, connected: runtimeState === 'ready' && !stale };
}

export default function Home() {
  const [view, setView] = useState<View>('Conversation');
  const [toast, setToast] = useState('');
  const data = useLocalState();
  const { runtime, context, connected } = data;
  const running = connected && runtime?.agent.state === 'running';
  const stateLabel = data.runtimeState === 'loading' ? 'Connecting' : !connected ? 'Disconnected' : running ? 'Agent online' : runtime?.agent.state === 'restarting' ? 'Agent restarting' : 'Agent stopped';
  useEffect(() => {
    const onHash = () => {
      const next = views.find((item) => item.label.toLowerCase() === window.location.hash.slice(1).toLowerCase());
      if (next) setView(next.label);
      else if (!window.location.hash) setView('Conversation');
    };
    onHash();
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(''), 3500); return () => clearTimeout(timer); }, [toast]);
  const navigate = (next: View) => { setView(next); window.location.hash = next.toLowerCase(); };
  const copy: Copy = async (value, message = 'Copied to clipboard') => {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
      else {
        const field = document.createElement('textarea');
        const previous = document.activeElement as HTMLElement | null;
        field.value = value;
        field.style.position = 'fixed'; field.style.opacity = '0';
        document.body.appendChild(field); field.select();
        const copied = document.execCommand('copy'); field.remove(); previous?.focus();
        if (!copied) throw new Error('Clipboard unavailable');
      }
      setToast(message);
    } catch { setToast('Could not copy. Select the text and copy it manually.'); }
  };
  return <div className="app-shell">
    <a className="skip-link" href="#workspace">Skip to workspace</a>
    <header className="app-header">
      <a className="brand" href="#conversation" aria-label="Veyra conversation"><Mark /><span>veyra<span className="brand-caption">local workspace</span></span></a>
      <div className="header-actions"><span className={`agent-status ${running ? 'online' : ''}`}><span className="status-dot" />{stateLabel}</span><button className={`icon-button ${data.refreshing ? 'refreshing' : ''}`} aria-label="Refresh local state" title="Refresh local state" onClick={() => void data.refresh()} disabled={data.refreshing}><Icon name="refresh" /></button><a className="button telegram-button" href={telegramUrl} target="_blank" rel="noreferrer"><Icon name="telegram" /><span>Open Telegram</span></a></div>
    </header>
    <div className="workspace-heading"><div><p className="eyebrow">YOUR INSTANCE</p><h1>Agent workspace<span className="heading-dot">.</span></h1></div><div className="revision-pill"><Icon name="branch" /><span>{runtime?.revision.branch ?? 'Local repository'}</span><code>{runtime?.revision.short ?? '—'}</code></div></div>
    <nav className="view-nav" aria-label="Workspace views">{views.map(({ label, icon }) => <a key={label} href={`#${label.toLowerCase()}`} className={view === label ? 'view-link active' : 'view-link'} aria-current={view === label ? 'page' : undefined} onClick={() => setView(label)}><Icon name={icon} />{label}{label === 'Conversation' && context && <span className="nav-count">{context.messages.length}</span>}</a>)}<span className="nav-note">{connected ? 'Updates every 3 seconds' : 'Local state'}</span></nav>
    <main id="workspace" className="workspace" tabIndex={-1}>
      {data.runtimeState !== 'loading' && !connected && <div className="connection-notice" role="status"><Icon name="runtime" /><p>{data.stale && data.runtimeState === 'ready' ? 'The supervisor snapshot is out of date.' : 'Live runtime status is unavailable.'}<span> {runtime ? 'Showing the last known version and settings.' : 'Start Veyra to see its active version and settings.'}</span></p><button className="text-button" onClick={() => navigate('Runtime')}>View runtime</button></div>}
      <div className="workspace-grid"><div className="main-column">
        {view === 'Conversation' && <Conversation context={context} state={data.contextState} onCopy={copy} onRefresh={data.refresh} />}
        {view === 'Desktop' && <Desktop onCopy={copy} />}
        {view === 'Evolution' && <Evolution runtime={runtime} state={data.runtimeState} connected={connected} onCopy={copy} />}
        {view === 'Memory' && <Memory runtime={runtime} context={context} contextState={data.contextState} onCopy={copy} />}
        {view === 'Runtime' && <Runtime runtime={runtime} stateLabel={stateLabel} connected={connected} onCopy={copy} />}
      </div><aside className="inspector" aria-label="Agent details">
        <section className="panel version-card"><div className="panel-heading"><h2><Icon name="branch" />{runtime ? connected ? 'Active version' : 'Last known version' : 'Agent version'}</h2><span className="small-label">{runtime ? 'GIT COMMIT' : 'AWAITING STATUS'}</span></div><code className="version-hash">{runtime?.revision.short ?? '— — —'}</code><p className="version-message">{runtime?.revision.message ?? 'The supervisor reports the active commit here.'}</p><div className="version-meta"><span title={formatDate(runtime?.revision.committed_at)}>{runtime ? relativeTime(runtime.revision.committed_at) : 'No snapshot yet'}</span>{runtime && <button className="icon-button" aria-label="Copy version commit" title="Copy commit" onClick={() => void copy(runtime.revision.commit)}><Icon name="copy" width="15" height="15" /></button>}</div><button className="panel-link" onClick={() => navigate('Evolution')}>View version history<Icon name="branch" width="16" height="16" /></button></section>
        <section className="panel settings-card"><div className="panel-heading"><h2>Model & context</h2><Icon name="memory" /></div><dl className="details"><div><dt>Provider</dt><dd>{runtime ? providerName(runtime.runtime.provider) : 'Unavailable'}</dd></div><div><dt>Model</dt><dd className="model-value">{runtime ? runtime.runtime.model ?? 'Not selected' : 'Unavailable'}</dd></div><div><dt>Retained messages</dt><dd>{context ? `${context.messages.length} / ${context.limit}` : 'Unavailable'}</dd></div></dl><button className="panel-link" onClick={() => navigate('Memory')}>Inspect memory<Icon name="memory" width="16" height="16" /></button></section>
        <section className="evolution-note"><span className="eyebrow">SHAPED BY YOU</span><h2>A conversation.<br />A better next version.</h2><p>Describe a change in Telegram. Veyra builds and tests it, then commits its next version.</p><ol className="evolution-steps"><li><span>01</span>Ask</li><li><span>02</span>Test</li><li><span>03</span>Commit</li></ol><span className="note-footer">The supervisor activates tested commits.</span></section>
        {view !== 'Desktop' && <button className="desktop-shortcut" onClick={() => navigate('Desktop')}><Icon name="screen" /><span>See the local desktop<small>Capture starts when you choose to watch.</small></span><Icon name="play" width="16" height="16" /></button>}
      </aside></div>
    </main>
    <footer className="app-footer"><span><Mark small />Local agent. Ordinary Git history.</span><span>{runtime ? `Snapshot ${relativeTime(runtime.updated_at)}` : 'Waiting for supervisor'}<span className="footer-divider">/</span>Telegram is your chat interface</span></footer>
    {toast && <div className="toast" role="status"><Icon name="copy" />{toast}<button className="icon-button" onClick={() => setToast('')} aria-label="Dismiss notification"><Icon name="close" width="16" height="16" /></button></div>}
  </div>;
}

function EmptyState({ icon, title, children }: { icon: IconName; title: string; children: React.ReactNode }) {
  return <div className="empty-state"><span className="empty-icon"><Icon name={icon} width="28" height="28" /></span><h3>{title}</h3><div>{children}</div></div>;
}

function Conversation({ context, state, onCopy, onRefresh }: { context: ConversationContext | null; state: DataState; onCopy: Copy; onRefresh: () => Promise<void> }) {
  const [draft, setDraft] = useState('');
  const [draftLoaded, setDraftLoaded] = useState(false);
  const feed = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const messages = context?.messages ?? [];
  const transcript = JSON.stringify(messages);
  // Browser-only draft restoration must happen after hydration, not during SSR.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { try { setDraft(sessionStorage.getItem('veyra-message-draft') ?? ''); } catch { /* Storage is optional. */ } setDraftLoaded(true); }, []);
  useEffect(() => { if (draftLoaded) { try { sessionStorage.setItem('veyra-message-draft', draft); } catch { /* Keep draft in memory. */ } } }, [draft, draftLoaded]);
  useEffect(() => { if (feed.current && followLatest.current) feed.current.scrollTop = feed.current.scrollHeight; }, [transcript]);
  const prepare = (value: string) => { setDraft(value); textarea.current?.focus(); };
  const starters = [['Improve a capability', 'I want you to improve a capability: '], ['Remember a preference', 'Remember this preference for future conversations: '], ['Review your setup', 'Review your current setup and suggest the most useful next improvement.']];
  return <section className="panel conversation-panel" aria-labelledby="conversation-title">
    <div className="surface-heading"><div><p className="eyebrow">CONTINUITY ACROSS VERSIONS</p><h2 id="conversation-title">Recent conversation</h2></div><span className="subtle-badge"><Icon name="telegram" width="14" height="14" />Telegram</span></div>
    <div className="context-caption"><span>Context Veyra carries into its next version</span><span>{context ? `${messages.length} of ${context.limit} messages` : 'Reading memory'}</span></div>
    {state === 'error' && <div className="inline-notice" role="status">{context ? 'Conversation could not refresh. Showing the last loaded context.' : 'Conversation memory could not be read.'}<button className="text-button" onClick={() => void onRefresh()}>Retry</button></div>}
    <div className="conversation-feed" ref={feed} role="region" aria-label="Retained conversation" tabIndex={0} onScroll={() => { const element = feed.current; if (element) followLatest.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; }}>
      {state === 'loading' && <EmptyState icon="memory" title="Reading local conversation…"><p>Loading the context retained by your agent.</p></EmptyState>}
      {state !== 'loading' && !messages.length && <EmptyState icon="chat" title={state === 'error' ? 'Context is unavailable' : 'The next version starts with a conversation.'}><p>{state === 'error' ? 'Use Retry to read local memory again.' : 'Talk to Veyra in Telegram. Your recent exchange will appear here and stay with the agent across restarts.'}</p></EmptyState>}
      {messages.map((message, index) => <article className={`message ${message.role}`} key={`${index}-${message.role}`}><div className="message-avatar">{message.role === 'assistant' ? <Mark small /> : 'Y'}</div><div className="message-body"><div className="message-heading"><strong>{message.role === 'user' ? 'You' : 'Veyra'}</strong>{message.commit && <code title={`Version ${message.commit}`}>{message.commit.slice(0, 12)}</code>}<button className="message-copy icon-button" aria-label={`Copy ${message.role === 'user' ? 'your' : 'Veyra’s'} message ${index + 1}`} title="Copy message" onClick={() => void onCopy(message.content)}><Icon name="copy" width="14" height="14" /></button></div><div className="message-text">{message.content}</div></div></article>)}
      {!!messages.length && <div className="feed-end"><span />End of retained context<span /></div>}
    </div>
    <form className="draft-composer" onSubmit={(event) => { event.preventDefault(); if (draft.trim()) void onCopy(draft.trim(), 'Message copied. Paste it into your Veyra chat in Telegram.'); }}><div className="composer-heading"><label htmlFor="message-draft">Prepare your next message</label><span>Draft only · saved in this tab</span></div><div className="draft-input"><textarea id="message-draft" ref={textarea} value={draft} onChange={(event) => setDraft(event.target.value)} rows={3} placeholder="What would you like Veyra to do or become?" /><div className="composer-actions"><span>Copy, then send in Telegram.</span><button className="button primary-button" type="submit" disabled={!draft.trim()}><Icon name="copy" width="16" height="16" />Copy message</button></div></div><div className="prompt-starters">{starters.map(([label, value]) => <button key={label} type="button" onClick={() => prepare(value)}>{label}</button>)}</div></form>
  </section>;
}

function Desktop({ onCopy }: { onCopy: Copy }) {
  const [watching, setWatching] = useState(false);
  const [attempt, setAttempt] = useState(0);
  return <section className="panel desktop-panel"><div className="surface-heading"><div><p className="eyebrow">YOUR COMPUTER</p><h2>Live desktop</h2></div>{watching && <button className="button small-button" onClick={() => setWatching(false)}><Icon name="pause" width="16" height="16" />Stop watching</button>}</div>
    {watching ? <LiveDesktop key={attempt} onRetry={() => setAttempt((value) => value + 1)} /> : <div className="desktop-idle"><div className="monitor-outline"><Icon name="screen" width="56" height="56" /></div><h3>A view into Veyra’s workspace.</h3><p>Watch the local desktop while the agent works.<br />Screen capture runs only while someone is watching.</p><button className="button primary-button" onClick={() => setWatching(true)}><Icon name="play" width="16" height="16" />Start watching</button></div>}
    <div className="desktop-footnote"><Icon name="screen" width="16" height="16" /><div><p>View only. Direct the agent and approve actions in Telegram. Frames are streamed without being saved to disk.</p><button className="text-button" onClick={() => void onCopy(`${window.location.origin}/veyra-screen.mjpeg`, 'Stream address copied')}>Copy stream address</button></div></div>
  </section>;
}

function LiveDesktop({ onRetry }: { onRetry: () => void }) {
  const image = useRef<HTMLImageElement>(null);
  const viewerElement = useRef<HTMLDivElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [stage, setStage] = useState<'connecting' | 'live' | 'stopped'>('connecting');
  const [status, setStatus] = useState<ScreenStatus | null>(null);
  const [error, setError] = useState('');
  const [resolution, setResolution] = useState('');
  const started = useRef(0);
  const lastDisplayed = useRef(0);
  const closeExpanded = useCallback(() => {
    setExpanded(false);
    if (document.fullscreenElement === viewerElement.current) void document.exitFullscreen().catch(() => {});
    expandButton.current?.focus();
  }, []);
  useEffect(() => {
    const syncFullscreen = () => setExpanded(document.fullscreenElement === viewerElement.current);
    document.addEventListener('fullscreenchange', syncFullscreen);
    return () => document.removeEventListener('fullscreenchange', syncFullscreen);
  }, []);
  useEffect(() => {
    if (!expanded) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') closeExpanded(); };
    document.addEventListener('keydown', onKey);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener('keydown', onKey); };
  }, [expanded, closeExpanded]);
  const toggleExpanded = async () => {
    if (expanded) { closeExpanded(); return; }
    // Embedded browsers and mobile browsers may omit or deny the Fullscreen API.
    // Always provide an in-page maximize mode rather than a silent no-op.
    setExpanded(true);
    try { await viewerElement.current?.requestFullscreen?.(); }
    catch { /* The fixed-position viewer remains usable without native fullscreen. */ }
  };
  const stopped = stage === 'stopped';
  useEffect(() => {
    const element = image.current;
    if (!element || stopped) return;
    const request = new AbortController();
    let displayedUrl = '';
    started.current = Date.now();
    const watch = async () => {
      try {
        const response = await fetch(`/veyra-screen.mjpeg?t=${started.current}`, { cache: 'no-store', signal: request.signal });
        if (!response.ok || !response.body) throw new Error('The desktop stream could not be reached.');
        for await (const frame of jpegFrames(response.body)) {
          if (request.signal.aborted) break;
          const url = URL.createObjectURL(new Blob([frame as Uint8Array<ArrayBuffer>], { type: 'image/jpeg' }));
          try {
            // Decode before swapping: no blank flashes, and only fully received
            // frames count as live. Maximizing never restarts this connection.
            const decoded = new Image();
            decoded.src = url;
            await decoded.decode();
            if (request.signal.aborted) break;
            element.src = url;
            if (displayedUrl) URL.revokeObjectURL(displayedUrl);
            displayedUrl = url;
            lastDisplayed.current = Date.now();
            setResolution(`${decoded.naturalWidth} × ${decoded.naturalHeight}`);
            setStage('live');
          } finally {
            if (displayedUrl !== url) URL.revokeObjectURL(url);
          }
        }
      } catch (error) {
        if (!request.signal.aborted) {
          setError(error instanceof Error ? error.message : 'The desktop stream closed.');
          setStage('stopped');
        }
      }
    };
    void watch();
    return () => { request.abort(); element.src = IDLE_PIXEL; if (displayedUrl) URL.revokeObjectURL(displayedUrl); };
  }, [stopped]);
  useEffect(() => {
    if (stage === 'stopped') { if (image.current) image.current.src = IDLE_PIXEL; return; }
    const request = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const response = await fetch('/veyra-screen.json', { cache: 'no-store', signal: AbortSignal.any([request.signal, AbortSignal.timeout(8000)]) });
        if (!response.ok) throw new Error('Screen status unavailable');
        const value = await response.json();
        if (!isScreenStatus(value)) throw new Error('Unsupported screen status');
        if (request.signal.aborted) return;
        setStatus(value);
        const lastFrame = lastDisplayed.current;
        if (!value.available || (Date.now() - started.current > 12_000 && Date.now() - lastFrame > 12_000)) { setError(value.error || 'No recent desktop frames were received.'); setStage('stopped'); return; }
      } catch { if (request.signal.aborted) return; setError('The screen stream could not be reached.'); setStage('stopped'); return; }
      if (!request.signal.aborted) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => { request.abort(); clearTimeout(timer); };
  }, [stage]);
  return <><div className={`desktop-viewer${expanded ? ' expanded' : ''}`} ref={viewerElement} role="region" aria-label="Desktop viewer">
    <div className="stream-toolbar"><span className={`stream-status ${stage}`}><span className="status-dot" />{stage === 'live' ? 'Live view' : stage === 'connecting' ? 'Connecting' : 'Stream stopped'}</span><button ref={expandButton} className="icon-button" aria-label={expanded ? 'Exit desktop fullscreen' : 'View desktop fullscreen'} title={expanded ? 'Exit fullscreen (Esc)' : 'Fullscreen'} aria-pressed={expanded} onClick={() => void toggleExpanded()}><Icon name={expanded ? 'close' : 'expand'} /></button></div>
    <div className="screen-stage">
      {/* Each explicitly decoded stream frame is displayed without remounting. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img ref={image} alt="Live local desktop" />
      {stage !== 'live' && <div className="screen-overlay"><Icon name="screen" width="36" height="36" /><h3>{stage === 'connecting' ? 'Connecting to the desktop…' : 'Live view stopped'}</h3><p>{stage === 'connecting' ? 'Waiting for the first frame.' : error}</p>{stage === 'stopped' && <button className="button primary-button" onClick={onRetry}><Icon name="refresh" width="16" height="16" />Reconnect</button>}</div>}
    </div></div><div className="stream-meta"><span>{resolution || 'Waiting for resolution'}</span><span>{status ? `${status.settings.fps} fps · ${Math.round(status.settings.scale * 100)}% scale` : 'Reading capture settings'}</span><span>{status?.tool ?? 'Local capture'}</span></div>{stage === 'live' && error && <p className="inline-notice" role="status">{error}</p>}</>;
}

function Evolution({ runtime, state, connected, onCopy }: { runtime: RuntimeStatus | null; state: DataState; connected: boolean; onCopy: Copy }) {
  const versions = runtime?.versions ?? [];
  return <section className="panel evolution-panel"><div className="surface-heading"><div><p className="eyebrow">EVERY VERSION IS A COMMIT</p><h2>Evolution history</h2></div><span className="subtle-badge"><Icon name="branch" width="14" height="14" />{runtime?.revision.branch ?? 'Git'}</span></div><p className="surface-copy">Changes requested in chat become tested commits. The supervisor reports the version it has activated.</p><div className="commit-timeline">{versions.map((version) => {
    const active = runtime?.revision.commit.startsWith(version.commit);
    return <article className={`commit-row ${active ? 'current' : ''}`} key={version.commit}><span className="commit-node"><Icon name={active ? 'check' : 'branch'} width="14" height="14" /></span><div className="commit-content"><div className="commit-labels"><code>{version.commit}</code>{active && <span className="version-badge">{connected ? 'Active version' : 'Last active version'}</span>}</div><h3>{version.message}</h3><time dateTime={version.committed_at} title={formatDate(version.committed_at)}>{formatDate(version.committed_at)}</time></div><button className="icon-button" aria-label={`Copy commit ${version.commit}`} title="Copy commit" onClick={() => void onCopy(version.commit)}><Icon name="copy" width="16" height="16" /></button></article>;
  })}{!versions.length && <EmptyState icon="branch" title={state === 'loading' ? 'Reading version history…' : 'Version history is unavailable'}><p>The supervisor supplies recent commits with its runtime snapshot.</p></EmptyState>}</div>{runtime && !versions.some((version) => runtime.revision.commit.startsWith(version.commit)) && <div className="inline-notice">The last reported active commit is {runtime.revision.short}. It is outside this recent history.</div>}<div className="recovery-note"><Icon name="terminal" /><div><h3>Recovery stays in your hands.</h3><p>Stop the supervisor and check out a known working commit when recovery is needed. A failed candidate self-test keeps the current agent running.</p></div></div></section>;
}

function Memory({ runtime, context, contextState, onCopy }: { runtime: RuntimeStatus | null; context: ConversationContext | null; contextState: DataState; onCopy: Copy }) {
  return <section className="panel memory-panel"><div className="surface-heading"><div><p className="eyebrow">PERSISTENT LOCAL STATE</p><h2>What Veyra carries forward</h2></div><Icon name="memory" width="24" height="24" /></div><p className="surface-copy">Conversation context and runtime choices survive agent restarts and version changes.</p>
    <div className="memory-record"><div className="record-icon"><Icon name="chat" /></div><div><h3>Recent conversation</h3><p>{context ? `${context.messages.length} messages retained, up to a limit of ${context.limit}.` : contextState === 'loading' ? 'Reading local conversation…' : 'Conversation memory is unavailable.'}</p><code>state/memory/recent_messages.json</code></div></div>
    <div className="memory-record"><div className="record-icon"><Icon name="code" /></div><div><h3>Selected provider</h3><p>{runtime ? providerName(runtime.runtime.provider) : 'Unavailable'}</p><code>state/memory/provider.json</code></div><button className="button small-button" onClick={() => void onCopy('/provider', 'Command copied. Send it to Veyra in Telegram.')}><Icon name="copy" width="14" height="14" />/provider</button></div>
    <div className="memory-record"><div className="record-icon"><Icon name="memory" /></div><div><h3>Selected model</h3><p>{runtime ? runtime.runtime.model ?? 'Not selected' : 'Unavailable'}<span className="muted"> · remembered per provider</span></p><code>state/memory/models.json</code></div><button className="button small-button" onClick={() => void onCopy('/model', 'Command copied. Send it to Veyra in Telegram.')}><Icon name="copy" width="14" height="14" />/model</button></div>
    <div className="memory-boundary"><Icon name="memory" /><div><h3>Memory is separate from source history.</h3><p>These files live under <code>state/</code> and are ignored by Git. Changing a provider or model does not create a new agent version.</p></div></div></section>;
}

function Runtime({ runtime, stateLabel, connected, onCopy }: { runtime: RuntimeStatus | null; stateLabel: string; connected: boolean; onCopy: Copy }) {
  return <section className="panel runtime-panel"><div className="surface-heading"><div><p className="eyebrow">LIFECYCLE & CONTROL</p><h2>Local runtime</h2></div><span className={`runtime-state ${connected && runtime?.agent.state === 'running' ? 'online' : ''}`}>{stateLabel}</span></div><dl className="runtime-details"><div><dt>Agent process</dt><dd>{runtime?.agent.pid ? `${connected ? 'PID' : 'Last known PID'} ${runtime.agent.pid}` : 'No running process reported'}</dd></div><div><dt>Supervisor snapshot</dt><dd>{runtime ? formatDate(runtime.updated_at) : 'Unavailable'}</dd></div><div><dt>Active commit</dt><dd><code>{runtime?.revision.short ?? 'Unavailable'}</code></dd></div><div><dt>Branch</dt><dd>{runtime?.revision.branch ?? 'Unavailable'}</dd></div></dl>
    <div className="startup-block"><div><h3>Start your local instance</h3><p>Run from the repository root when Veyra is stopped.</p></div><button className="code-button" onClick={() => void onCopy('./supervisor/supervisor', 'Startup command copied')}><code>./supervisor/supervisor</code><Icon name="copy" width="16" height="16" /></button></div>
    <div className="runtime-checks"><h3>How the supervisor works</h3><ul><li><Icon name="check" />One supervisor per repository</li><li><Icon name="check" />A clean, Git-tracked agent before startup</li><li><Icon name="check" />Self-test before activating a commit</li><li><Icon name="check" />Current agent stays running if a candidate fails</li></ul></div>
    <div className="command-directory"><h3>Commands for your Telegram chat</h3>{[['/help', 'List every available command'], ['/provider', 'View or choose your model provider'], ['/model', 'View or change your active model'], ['/screenshot', 'Request a desktop screenshot'], ['/codex ', 'Stage a prompt in an open Codex CLI'], ['/claude ', 'Stage a prompt in an open Claude CLI']].map(([command, detail]) => <button key={command} onClick={() => void onCopy(command, 'Command copied. Paste it into your Veyra chat in Telegram.')}><code>{command.trim()}</code><span>{detail}</span><Icon name="copy" width="15" height="15" /></button>)}</div></section>;
}
