import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  buildFormattedRationale,
  copyToClipboard,
  readVoiceEnabled,
  writeVoiceEnabled,
  getSpeechRecognitionCtor,
} from '../utils';

// Compatibility for Bun
const g = (globalThis as any);
if (!g.window) {
    g.window = g;
}
if (!g.navigator) {
    g.navigator = { clipboard: {} };
}
if (!g.localStorage) {
    g.localStorage = {
        getItem: () => null,
        setItem: () => {},
        clear: () => {}
    };
}

// vi.stubGlobal mock for Bun if needed
if (!vi.stubGlobal) {
    vi.stubGlobal = (name: string, value: any) => {
        g[name] = value;
    };
}
if (!vi.mocked) {
    vi.mocked = (obj: any) => obj;
}

describe('utils', () => {
  describe('buildFormattedRationale', () => {
    it('returns trimmed rationale if no replyFormat', () => {
      expect(buildFormattedRationale(null, 'A', '  test  ')).toBe('test');
      expect(buildFormattedRationale(undefined, 'A', '  test  ')).toBe('test');
    });

    it('returns formatted rationale if replyFormat exists', () => {
      expect(buildFormattedRationale('format', 'A', 'rationale')).toBe(
        'REPLY_FORMAT: format\nCHOICE: A\nRATIONALE: rationale'
      );
    });
  });

  describe('copyToClipboard', () => {
    beforeEach(() => {
      vi.stubGlobal('navigator', {
        clipboard: {
          writeText: vi.fn(),
        },
      });
    });

    it('returns true when successful', async () => {
      vi.mocked(navigator.clipboard.writeText).mockResolvedValue(undefined);
      const result = await copyToClipboard('test');
      expect(result).toBe(true);
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('test');
    });

    it('returns false when failed', async () => {
      vi.mocked(navigator.clipboard.writeText).mockRejectedValue(new Error('failed'));
      const result = await copyToClipboard('test');
      expect(result).toBe(false);
    });
  });

  describe('voice settings', () => {
    beforeEach(() => {
      window.localStorage.clear();
      window.__OVERSEER_FLAGS__ = undefined;
      // Mock import.meta.env
      if (!(import.meta as any).env) (import.meta as any).env = {};
      (import.meta as any).env.VITE_VOICE_ENABLED = 'false';
    });

    it('reads from localStorage', () => {
      vi.spyOn(window.localStorage, 'getItem').mockReturnValue('true');
      expect(readVoiceEnabled()).toBe(true);
      vi.spyOn(window.localStorage, 'getItem').mockReturnValue('false');
      expect(readVoiceEnabled()).toBe(false);
    });

    it('prefers runtime override', () => {
      window.__OVERSEER_FLAGS__ = { voiceEnabled: true };
      vi.spyOn(window.localStorage, 'getItem').mockReturnValue('false');
      expect(readVoiceEnabled()).toBe(true);
    });

    it('writes to localStorage', () => {
      const spy = vi.spyOn(window.localStorage, 'setItem');
      writeVoiceEnabled(true);
      expect(spy).toHaveBeenCalledWith('overseer.voice.enabled', 'true');
      writeVoiceEnabled(false);
      expect(spy).toHaveBeenCalledWith('overseer.voice.enabled', 'false');
    });

    it('falls back to environment variable', () => {
        (import.meta as any).env.VITE_VOICE_ENABLED = 'true';
        vi.spyOn(window.localStorage, 'getItem').mockReturnValue(null);
        expect(readVoiceEnabled()).toBe(true);
    });
  });

  describe('getSpeechRecognitionCtor', () => {
    beforeEach(() => {
      window.SpeechRecognition = undefined;
      window.webkitSpeechRecognition = undefined;
    });

    it('returns SpeechRecognition if available', () => {
      const Mock = vi.fn();
      window.SpeechRecognition = Mock as any;
      expect(getSpeechRecognitionCtor()).toBe(Mock);
    });

    it('returns webkitSpeechRecognition if SpeechRecognition is not available', () => {
      const Mock = vi.fn();
      window.webkitSpeechRecognition = Mock as any;
      expect(getSpeechRecognitionCtor()).toBe(Mock);
    });

    it('returns null if neither is available', () => {
      expect(getSpeechRecognitionCtor()).toBeNull();
    });
  });
});
