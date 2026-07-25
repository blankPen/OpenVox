import { describe, expect, it } from 'vitest';
import {
  ConfigSchema,
  CustomProviderSchema,
  ProtocolSchema,
} from '../../src/config/schema.js';

describe('config/schema', () => {
  describe('ProtocolSchema', () => {
    it('accepts known protocols', () => {
      expect(ProtocolSchema.parse('stream-json')).toBe('stream-json');
      expect(ProtocolSchema.parse('openai-http')).toBe('openai-http');
      expect(ProtocolSchema.parse('acp')).toBe('acp');
      expect(ProtocolSchema.parse('jsonrpc')).toBe('jsonrpc');
    });

    it('rejects unknown protocols', () => {
      expect(() => ProtocolSchema.parse('grpc')).toThrow();
      expect(() => ProtocolSchema.parse('')).toThrow();
    });
  });

  describe('CustomProviderSchema', () => {
    it('accepts a minimal custom provider', () => {
      const r = CustomProviderSchema.parse({
        id: 'my-acp', label: 'My ACP', command: '/usr/local/bin/my-cli', protocol: 'acp',
      });
      expect(r.args).toEqual([]);
      expect(r.env).toBeUndefined();
    });

    it('rejects missing required fields', () => {
      expect(() => CustomProviderSchema.parse({ id: 'x', label: 'x', command: 'x' })).toThrow();
    });

    it('rejects bad baseUrl', () => {
      expect(() => CustomProviderSchema.parse({
        id: 'openclaw', label: 'openclaw', command: 'openclaw',
        protocol: 'openai-http', baseUrl: 'not-a-url',
      })).toThrow();
    });
  });

  describe('ConfigSchema', () => {
    it('applies defaults when fields are missing', () => {
      const r = ConfigSchema.parse({});
      expect(r.port).toBe(8787);
      expect(r.host).toBe('127.0.0.1');
      expect(r.sessionTtlSeconds).toBe(1800);
      expect(r.maxConcurrentPerProvider).toBe(4);
      expect(r.providers).toEqual([]);
      expect(r.auth.tokens).toEqual([]);
    });

    it('preserves user-provided values', () => {
      const r = ConfigSchema.parse({
        port: 9000,
        auth: { tokens: ['secret-1', 'secret-2'] },
        providers: [{ id: 'p1', label: 'P1', command: '/bin/echo', protocol: 'acp' }],
      });
      expect(r.port).toBe(9000);
      expect(r.auth.tokens).toEqual(['secret-1', 'secret-2']);
      expect(r.providers).toHaveLength(1);
      expect(r.providers[0]?.id).toBe('p1');
    });
  });
});