import { describe, expect, it } from 'vitest';
import { logger } from '../../src/util/logger.js';

describe('util/logger', () => {
  it('exports a working pino instance', () => {
    expect(logger).toBeDefined();
    expect(typeof logger.info).toBe('function');
    expect(typeof logger.warn).toBe('function');
    expect(typeof logger.error).toBe('function');
  });

  it('has a configurable level', () => {
    const prev = logger.level;
    try {
      logger.level = 'silent';
      expect(logger.level).toBe('silent');
    } finally {
      logger.level = prev;
    }
  });
});