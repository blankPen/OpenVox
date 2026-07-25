import type { FastifyInstance } from 'fastify';
import type { SessionManager } from '../../sessions/manager.js';

export async function sessionsRoute(
  app: FastifyInstance,
  sessions: SessionManager,
): Promise<void> {
  app.get('/v1/sessions', async () => {
    const list = sessions.list();
    return {
      object: 'list',
      data: list.map((s) => ({
        id: s.id,
        object: 'agentd.session',
        provider: s.provider,
        room_id: s.roomId ?? null,
        cli_session_id: s.cliSessionId ?? null,
        created_at: s.createdAt,
        last_active_at: s.lastActiveAt,
      })),
    };
  });

  app.delete<{ Params: { id: string } }>('/v1/sessions/:id', async (req, reply) => {
    const ok = await sessions.close(req.params.id);
    if (!ok) return reply.code(404).send({ error: { message: 'not found' } });
    return { id: req.params.id, object: 'agentd.session', closed: true };
  });
}
