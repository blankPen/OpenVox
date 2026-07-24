import { z } from 'zod';

/**
 * Provider kinds recognised by agentd.
 *
 * - `stream-json`  : Claude Code style `bin -p --output-format stream-json --verbose`
 * - `openai-http`  : subprocess exposes an OpenAI-compatible HTTP endpoint (e.g. OpenClaw)
 * - `acp`          : subprocess speaks JSON-RPC over stdio per ACP spec
 * - `jsonrpc`      : generic JSON-RPC over stdio (not strictly ACP, but used as escape hatch)
 */
export const ProtocolSchema = z.enum(['stream-json', 'openai-http', 'acp', 'jsonrpc']);
export type ProviderProtocol = z.infer<typeof ProtocolSchema>;

/**
 * One custom provider entry from ~/.agentd/config.json providers[].
 *
 * Minimum required: { id, label, command, protocol }.
 */
export const CustomProviderSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  command: z.string().min(1),
  args: z.array(z.string()).default([]),
  protocol: ProtocolSchema,
  env: z.record(z.string()).optional(),
  /** Optional HTTP base URL for openai-http providers. */
  baseUrl: z.string().url().optional(),
  /** Discovery hint: which discovered binary id should reuse this factory. */
  reuseBinary: z.string().optional(),
});

export const AuthSchema = z.object({
  tokens: z.array(z.string()).default([]),
});

export const RateLimitSchema = z.object({
  max: z.number().int().positive().default(60),
  windowMs: z.number().int().positive().default(60_000),
});

export const CliOAuthSchema = z.object({
  probeClaudeCredentials: z.boolean().default(true),
});

export const AcpSchema = z.object({
  serverSocket: z.string().nullable().default(null),
});

/**
 * Root config schema validated against disk JSON.
 * Fields not provided fall back to DEFAULT_CONFIG in loader.ts.
 */
export const ConfigSchema = z.object({
  port: z.number().int().positive().default(8787),
  host: z.string().default('127.0.0.1'),
  logLevel: z.string().default('info'),
  sessionTtlSeconds: z.number().int().positive().default(60 * 30),
  maxConcurrentPerProvider: z.number().int().positive().default(4),
  rateLimit: RateLimitSchema.default({ max: 60, windowMs: 60_000 }),
  auth: AuthSchema.default({ tokens: [] }),
  providers: z.array(CustomProviderSchema).default([]),
  cliOAuth: CliOAuthSchema.default({ probeClaudeCredentials: true }),
  acp: AcpSchema.default({ serverSocket: null }),
});

export type AgentdConfig = z.infer<typeof ConfigSchema>;
export type CustomProvider = z.infer<typeof CustomProviderSchema>;
