'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { isRuntimeStatus, isRuntimeStale, type RuntimeStatus } from '../lib/veyra';

type SafariVideo = HTMLVideoElement & {
  webkitEnterFullscreen?: () => void;
  webkitExitFullscreen?: () => void;
};

export default function Home() {
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const response = await fetch('/veyra-status.json', {
          cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]),
        });
        const value = await response.json();
        if (!response.ok || !isRuntimeStatus(value)) throw new Error('Status unavailable');
        if (controller.signal.aborted) return;
        setRuntime(value);
        setConnected(!isRuntimeStale(value, Date.now()));
      } catch { if (!controller.signal.aborted) setConnected(false); }
      if (!controller.signal.aborted) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);
  return <main className="page">
    <header><h1>Veyra</h1><dl>
      <div><dt>{runtime && !connected ? 'Last known version' : 'Version'}</dt><dd><code title={runtime?.revision.commit}>{runtime?.revision.short ?? '—'}</code></dd></div>
      <div><dt>{runtime && !connected ? 'Last known model' : 'Active model'}</dt><dd>{runtime?.runtime.model ?? '—'}</dd></div>
    </dl></header>
    {!connected && <p className="notice" role="status">{runtime ? 'Runtime status is unavailable or out of date.' : 'Waiting for runtime status…'}</p>}
    <LiveVideo />
  </main>;
}

function LiveVideo() {
  const video = useRef<SafariVideo>(null);
  const viewer = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const [attempt, setAttempt] = useState(0);
  const [stage, setStage] = useState<'connecting' | 'live' | 'stopped'>('connecting');
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState('');
  const close = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    video.current?.webkitExitFullscreen?.();
    setExpanded(false);
    button.current?.focus();
  }, []);
  useEffect(() => {
    const element = video.current;
    if (!element) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    const start = async () => {
      const sourceType = window.MediaSource ?? (window as typeof window & { ManagedMediaSource?: typeof MediaSource }).ManagedMediaSource;
      const mime = 'video/mp4; codecs="avc1.42C01F"';
      if (!sourceType?.isTypeSupported(mime)) throw new Error('This browser does not support live H.264 video.');
      const source = new sourceType();
      element.disableRemotePlayback = true;
      objectUrl = URL.createObjectURL(source);
      element.src = objectUrl;
      await new Promise<void>((resolve, reject) => {
        source.addEventListener('sourceopen', () => resolve(), { once: true });
        controller.signal.addEventListener('abort', () => reject(controller.signal.reason), { once: true });
      });
      const buffer = source.addSourceBuffer(mime);
      const response = await fetch(`/veyra-screen.mp4?attempt=${attempt}`, { signal: controller.signal });
      if (!response.ok || !response.body) throw new Error('Could not load the live video.');
      const reader = response.body.getReader();
      void element.play().catch(() => {});
      const append = (data?: Uint8Array) => new Promise<void>((resolve, reject) => {
        const done = () => { cleanup(); resolve(); };
        const fail = () => { cleanup(); reject(new Error('Video decoding stopped.')); };
        const cleanup = () => {
          buffer.removeEventListener('updateend', done);
          buffer.removeEventListener('error', fail);
          controller.signal.removeEventListener('abort', fail);
        };
        buffer.addEventListener('updateend', done, { once: true });
        buffer.addEventListener('error', fail, { once: true });
        controller.signal.addEventListener('abort', fail, { once: true });
        try {
          if (data) buffer.appendBuffer(data as Uint8Array<ArrayBuffer>);
          else buffer.remove(0, element.currentTime - 15);
        } catch (error) { cleanup(); reject(error); }
      });
      try {
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read();
          if (done) throw new Error('The video connection closed.');
          await append(value);
          if (element.currentTime > 30 && buffer.buffered.length && buffer.buffered.start(0) < element.currentTime - 20) await append();
        }
      } finally { await reader.cancel().catch(() => {}); }
    };
    void start().catch((reason: unknown) => {
      if (!controller.signal.aborted) {
        setStage('stopped');
        setError(reason instanceof Error ? reason.message : 'Could not load the live video.');
      }
    });
    return () => {
      controller.abort(); element.pause(); element.removeAttribute('src'); element.load();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attempt]);
  useEffect(() => {
    const sync = () => setExpanded(!!document.fullscreenElement);
    const end = () => setExpanded(false);
    document.addEventListener('fullscreenchange', sync);
    const element = video.current;
    element?.addEventListener('webkitendfullscreen', end);
    return () => {
      document.removeEventListener('fullscreenchange', sync);
      element?.removeEventListener('webkitendfullscreen', end);
    };
  }, []);
  useEffect(() => {
    if (!expanded) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
    document.addEventListener('keydown', escape);
    return () => { document.body.style.overflow = previous; document.removeEventListener('keydown', escape); };
  }, [expanded, close]);
  const maximize = async () => {
    if (expanded) { close(); return; }
    try {
      // iPhone Safari exposes native VIDEO fullscreen, not element fullscreen.
      if (!viewer.current?.requestFullscreen && video.current?.webkitEnterFullscreen) {
        video.current.webkitEnterFullscreen();
        setExpanded(true);
        return;
      }
      if (viewer.current?.requestFullscreen) {
        await viewer.current.requestFullscreen();
        return;
      }
    } catch { /* Keep an edge-to-edge fallback for embedded browsers. */ }
    setExpanded(true);
    setError('Browser fullscreen is unavailable. Showing an expanded video instead.');
  };
  return <section aria-label="Live desktop video">
    <div ref={viewer} className={`viewer${expanded ? ' expanded' : ''}`} role="region" aria-label="Desktop viewer">
      <div className="toolbar"><span className={`stream-status ${stage}`}>{stage === 'live' ? 'Live video' : stage === 'connecting' ? 'Connecting…' : 'Live video stopped'}</span>
        <button ref={button} aria-label={expanded ? 'Exit video fullscreen' : 'Maximize video'} aria-pressed={expanded} onClick={() => void maximize()}>{expanded ? 'Exit fullscreen' : 'Maximize'}</button>
      </div>
      <div className="video-stage">
        <video ref={video} aria-label="Live local desktop" autoPlay muted playsInline controls crossOrigin="anonymous"
          onPlaying={() => { setStage('live'); setError(''); }}
          onError={() => { setStage('stopped'); setError('Could not load the live video.'); }}
          onEnded={() => { setStage('stopped'); setError('The video connection closed.'); }} />
        {stage !== 'live' && <div className="overlay" role="status"><p>{stage === 'connecting' ? 'Connecting to the desktop…' : error}</p>
          {stage === 'stopped' && <button onClick={() => { setStage('connecting'); setError(''); setAttempt((value) => value + 1); }}>Reconnect</button>}
        </div>}
      </div>
      {stage === 'live' && error && <p className="notice" role="status">{error}</p>}
    </div>
  </section>;
}
