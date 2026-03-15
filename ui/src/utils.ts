import { type SpeechRecognitionCtor } from './types';

const VOICE_ENABLED_STORAGE_KEY = 'overseer.voice.enabled';

export function buildFormattedRationale(replyFormat: string | null | undefined, choice: string, rationale: string): string {
  const trimmed = rationale.trim();
  if (!replyFormat) {
    return trimmed;
  }
  return `REPLY_FORMAT: ${replyFormat}
CHOICE: ${choice}
RATIONALE: ${trimmed}`;
}

export function readVoiceEnabled(): boolean {
  const runtimeOverride = window.__OVERSEER_FLAGS__?.voiceEnabled;
  if (typeof runtimeOverride === 'boolean') {
    return runtimeOverride;
  }

  const persisted = window.localStorage.getItem(VOICE_ENABLED_STORAGE_KEY);
  if (persisted === 'true') {
    return true;
  }
  if (persisted === 'false') {
    return false;
  }

  return String(import.meta.env.VITE_VOICE_ENABLED ?? '').toLowerCase() === 'true';
}

export function writeVoiceEnabled(enabled: boolean): void {
  window.localStorage.setItem(VOICE_ENABLED_STORAGE_KEY, enabled ? 'true' : 'false');
}

export function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  return window.SpeechRecognition ?? window.webkitSpeechRecognition ?? null;
}

/**
 * Copies the given text to the system clipboard.
 * Returns true if successful, false otherwise.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
