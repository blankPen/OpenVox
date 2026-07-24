import { pino } from 'pino';

export const logger = pino({
  level: process.env['AGENTD_LOG_LEVEL'] ?? 'info',
  base: { service: 'agentd' },
  timestamp: pino.stdTimeFunctions.isoTime,
});

export type Logger = typeof logger;
