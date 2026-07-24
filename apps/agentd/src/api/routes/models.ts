import type { FastifyInstance } from 'fastify';
import type { ProviderRegistry } from '../../providers/registry.js';

export async function modelsRoute(
  app: FastifyInstance,
  registry: ProviderRegistry,
): Promise<void> {
  app.get('/v1/models', async () => {
    const providers = registry.list();
    const data = providers.map((p) => ({
      id: `agentd/${p.provider.id}`,
      object: 'model',
      created: Math.floor(Date.now() / 1000),
      owned_by: 'agentd',
      // Extra fields clients can use to show health (OpenAI ignores unknown keys).
      agentd: {
        protocol: p.provider.protocol,
        status: p.status,
        label: p.provider.label,
      },
    }));
    return { object: 'list', data };
  });
}
