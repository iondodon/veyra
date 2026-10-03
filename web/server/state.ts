import { readFile } from 'node:fs/promises';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Plugin } from 'vite';
import type { ConversationContext, ConversationMessage } from '../lib/veyra';

const STATE_ROOT = new URL('../../state/', import.meta.url);

/** Expose only the bounded conversation the agent already retains. */
export async function readConversation(path: URL | string): Promise<ConversationContext> {
  let document;
  try {
    document = JSON.parse(await readFile(path, 'utf8'));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return { schema_version: 1, limit: 20, messages: [] };
    }
    throw error;
  }

  if (!document || document.version !== 1 || !Array.isArray(document.messages)) {
    throw new Error('Unsupported conversation memory format');
  }

  const limit = Number.isInteger(document.limit) && document.limit > 0
    ? Math.min(document.limit, 100) : 20;
  const messages: ConversationMessage[] = document.messages
    .filter((item: ConversationMessage | null) => item && ['user', 'assistant'].includes(item.role)
      && typeof item.content === 'string')
    .slice(-limit)
    .map((item: ConversationMessage) => ({
      role: item.role,
      content: item.content,
      ...(typeof item.commit === 'string' && item.commit ? { commit: item.commit } : {}),
    }));

  return { schema_version: 1, limit, messages };
}

export function veyraLocalState(stateRoot = STATE_ROOT): Plugin {
  const middleware = async (
    request: IncomingMessage, response: ServerResponse, next: () => void,
  ) => {
    const path = request.url?.split('?', 1)[0];
    if (path !== '/veyra-status.json' && path !== '/veyra-context.json') {
      next();
      return;
    }

    response.setHeader('Content-Type', 'application/json; charset=utf-8');
    response.setHeader('Cache-Control', 'no-store, private');
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      response.statusCode = 405;
      response.setHeader('Allow', 'GET, HEAD');
      response.end(JSON.stringify({ error: 'Read-only endpoint' }));
      return;
    }

    try {
      const body = path === '/veyra-status.json'
        ? await readFile(new URL('dashboard.json', stateRoot), 'utf8')
        : JSON.stringify(await readConversation(new URL('memory/recent_messages.json', stateRoot)));
      response.end(request.method === 'HEAD' ? undefined : body);
    } catch {
      response.statusCode = 503;
      response.end(JSON.stringify({ error: path === '/veyra-status.json'
        ? 'Veyra runtime status unavailable' : 'Veyra conversation memory unavailable' }));
    }
  };

  return {
    name: 'veyra-local-state',
    enforce: 'pre',
    configureServer(server) { server.middlewares.use(middleware); },
    configurePreviewServer(server) { server.middlewares.use(middleware); },
  };
}
