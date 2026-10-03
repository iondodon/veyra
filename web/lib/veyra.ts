export type RuntimeStatus = {
  schema_version: 1;
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

export type ConversationMessage = {
  role: 'user' | 'assistant';
  content: string;
  commit?: string;
};

export type ConversationContext = {
  schema_version: 1;
  limit: number;
  messages: ConversationMessage[];
};

export type ScreenStatus = {
  schema_version: number;
  available: boolean;
  tool: string | null;
  stream_path: string;
  settings: { fps: number; quality: number; scale: number };
  viewers: number;
  frames: number;
  last_frame_at: string | null;
  error: string | null;
};

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function nullableString(value: unknown) {
  return value === null || typeof value === 'string';
}

export function isRuntimeStatus(value: unknown): value is RuntimeStatus {
  if (!record(value) || !record(value.agent) || !record(value.revision) || !record(value.runtime)) return false;
  return value.schema_version === 1
    && typeof value.updated_at === 'string' && Number.isFinite(Date.parse(value.updated_at))
    && typeof value.agent.state === 'string' && ['running', 'restarting', 'stopped'].includes(value.agent.state)
    && (value.agent.pid === null || (Number.isInteger(value.agent.pid) && Number(value.agent.pid) > 0))
    && typeof value.revision.commit === 'string' && typeof value.revision.short === 'string'
    && typeof value.revision.branch === 'string' && typeof value.revision.message === 'string'
    && nullableString(value.revision.committed_at)
    && nullableString(value.runtime.provider) && nullableString(value.runtime.model)
    && Number.isInteger(value.runtime.recent_message_count) && Number(value.runtime.recent_message_count) >= 0
    && Array.isArray(value.versions)
    && value.versions.every((version) => record(version) && typeof version.commit === 'string'
      && typeof version.message === 'string' && typeof version.committed_at === 'string');
}

export function isConversationContext(value: unknown): value is ConversationContext {
  if (!record(value)) return false;
  return value.schema_version === 1
    && Number.isInteger(value.limit) && Number(value.limit) > 0
    && Array.isArray(value.messages)
    && value.messages.every((message) => record(message) && typeof message.role === 'string'
      && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string'
      && (message.commit === undefined || typeof message.commit === 'string'));
}

export function isScreenStatus(value: unknown): value is ScreenStatus {
  if (!record(value) || !record(value.settings)) return false;
  return value.schema_version === 1 && typeof value.available === 'boolean'
    && nullableString(value.tool) && typeof value.stream_path === 'string'
    && Number.isFinite(value.settings.fps) && Number.isFinite(value.settings.quality)
    && Number.isFinite(value.settings.scale) && Number.isInteger(value.frames)
    && Number.isInteger(value.viewers) && nullableString(value.error)
    && (value.last_frame_at === null || (typeof value.last_frame_at === 'string' && Number.isFinite(Date.parse(value.last_frame_at))));
}

export function isRuntimeStale(status: RuntimeStatus, now: number) {
  return now - Date.parse(status.updated_at) > 15_000;
}
