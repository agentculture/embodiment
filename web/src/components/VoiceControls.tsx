export interface VoiceControlsProps {
  /** true once `state`'s status token reports the voice loop running. */
  voiceOn: boolean;
  /** null before any `mic` event has arrived. */
  micHot: boolean | null;
  onStart: () => void;
  onStop: () => void;
  onToggleMute: () => void;
  disabled?: boolean;
}

export function VoiceControls({
  voiceOn,
  micHot,
  onStart,
  onStop,
  onToggleMute,
  disabled = false,
}: VoiceControlsProps) {
  return (
    <div className="voice-controls">
      <button
        type="button"
        aria-pressed={voiceOn}
        disabled={disabled}
        onClick={voiceOn ? onStop : onStart}
      >
        {voiceOn ? "Stop voice" : "Start voice"}
      </button>
      <button
        type="button"
        aria-pressed={micHot === true}
        disabled={disabled || micHot === null}
        onClick={onToggleMute}
      >
        {micHot === null ? "mic: unknown" : micHot ? "Mute mic" : "Unmute mic"}
      </button>
    </div>
  );
}
