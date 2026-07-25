import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import { z } from 'zod';
import type { AgentdConfig } from '../../config/schema.js';
import type { ProviderRegistry } from '../../providers/registry.js';
import type { SessionManager } from '../../sessions/manager.js';
import { writeSseStream } from '../../stream/sse.js';
import {
  buildFinalCompletion,
  mapEventToChunk,
  type ChatCompletionChunk,
} from '../../stream/openai-shape.js';
import { logger } from '../../util/logger.js';
import { SemaphoreTable } from '../../util/semaphore.js';

const ChatBodySchema = z.object({
  model: z.string().min(1),
  messages: z
    .array(
      z.object({
        role: z.enum(['system', 'user', 'assistant', 'tool']),
        content: z.string().default(''),
      }),
    )
    .min(1),
  stream: z.boolean().default(false),
  /** OpenAI-style tools — passed through to providers that support them. */
  tools: z.array(z.unknown()).optional(),
  tool_choice: z.unknown().optional(),
  /** Optional explicit room id for session reuse. */
  room_id: z.string().optional(),
  /** Optional explicit agentd session id to resume. */
  session_id: z.string().optional(),
  /** Force a fresh session even if one exists for the room. */
  new_session: z.boolean().optional(),
});

export interface ChatRouteDeps {
  cfg: AgentdConfig;
  registry: ProviderRegistry;
  sessions: SessionManager;
}

export async function chatRoute(
  app: FastifyInstance,
  deps: ChatRouteDeps,
): Promise<void> {
  const sems = new SemaphoreTable(
    () => deps.cfg.maxConcurrentPerProvider,
  );

  app.post('/v1/chat/completions', async (req: FastifyRequest, reply: FastifyReply) => {
    const parsed = ChatBodySchema.safeParse(req.body);
    if (!parsed.success) {
      reply.code(400);
      return {
        error: { message: 'invalid body', issues: parsed.error.issues },
      };
    }

    const body = parsed.data;
    const entry = deps.registry.resolveByModel(body.model);
    if (!entry) {
      reply.code(404);
      return { error: { message: `unknown model ${body.model}`, type: 'invalid_request_error' } };
    }

    // Session resolution: prefer explicit session_id, then room_id, then new.
    let session = body.session_id ? deps.sessions.get(body.session_id) : undefined;
    if (!session && body.room_id && !body.new_session) {
      session = deps.sessions.byRoom(body.room_id);
    }
    if (!session) {
      session = deps.sessions.create({
        provider: entry.provider.id,
        roomId: body.room_id,
        meta: { model: body.model },
      });
    }
    deps.sessions.touch(session.id);

    const sem = sems.get(entry.provider.id);
    try {
      await sem.acquire();
    } catch {
      reply.code(503);
      return { error: { message: 'provider overloaded', type: 'overloaded' } };
    }

    try {
      const result = await entry.provider.send({
        messages: body.messages.map((m) => ({
          role: m.role === 'tool' ? 'user' : (m.role as 'system' | 'user' | 'assistant'),
          content: m.content,
        })),
        resumeCliSessionId: session.cliSessionId,
        model: body.model,
        signal: deps.sessions.signal(session.id),
      });

      // The events() async generator can only be iterated once.
      // We fan-out within a single consumer using a session_id callback,
      // and re-yield events to either an SSE sink or a non-stream buffer.
      const sessionIdCallback = (id: string) => {
        deps.sessions.setCliSessionId(session!.id, id);
      };

      if (body.stream) {
        const modelId = body.model;
        async function* sseSource() {
          for await (const evt of result.events) {
            if (evt.type === 'session_id') sessionIdCallback(evt.id);
            const chunk: ChatCompletionChunk | null = mapEventToChunk(evt, modelId);
            if (chunk) yield chunk;
          }
        }
        await writeSseStream(reply, sseSource());
        return reply;
      }

      // Non-stream: collect all text deltas in a single pass.
      let content = '';
      let inputTok = 0;
      let outTok = 0;
      for await (const evt of result.events) {
        if (evt.type === 'session_id') sessionIdCallback(evt.id);
        else if (evt.type === 'text') content += evt.delta;
        else if (evt.type === 'usage') {
          inputTok = evt.inputTokens;
          outTok = evt.outputTokens;
        }
      }
      return buildFinalCompletion(body.model, content, inputTok, outTok);
    } finally {
      sem.release();
    }
  });
}
