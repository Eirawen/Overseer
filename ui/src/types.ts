export type SpeechRecognitionEventLike = {
  results: ArrayLike<ArrayLike<{ transcript?: string }>>;
};

export type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

export type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

export type FeatureFlags = {
  voiceEnabled?: boolean;
};

declare global {
  interface Window {
    webkitSpeechRecognition?: SpeechRecognitionCtor;
    SpeechRecognition?: SpeechRecognitionCtor;
    __OVERSEER_FLAGS__?: FeatureFlags;
  }
}
