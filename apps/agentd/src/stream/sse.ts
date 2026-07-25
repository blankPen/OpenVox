/**
 * SSE response writer — accepts an object sink and emits `data: <json>\n\n`.
 *
 * Used by /v1/chat/completions when stream=true.
 */
import type { FastifyReply } from 'fastify';

export async function writeSseStream(
  reply: FastifyReply,
  source: AsyncIterable<unknown>,
): Promise<void> {
  reply.raw.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
  reply.raw.setHeader('Cache-Control', 'no-cache');
  reply.raw.setHeader('Connection', 'keep-alive');
  reply.raw.setHeader('X-Accel-Buffering', 'no');
  // Tell proxies not to chunk — node will flush as we write.
  reply.raw.flushHeaders?.();

  for await (const evt of source) {
    const data = typeof evt === 'string' ? evt : JSON.stringify(evt);
    reply.raw.write(`data: ${data}\n\n`);
  }
  reply.raw.write('data: [DONE]\n\n');
  reply.raw.end();
}
