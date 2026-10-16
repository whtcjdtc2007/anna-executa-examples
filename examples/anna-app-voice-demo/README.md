# anna-app-voice-demo

Reference App for the **realtime voice session** surface —
`anna.audio.session()` (matrix-nexus design
`app-realtime-voice-session-and-executa-transcribe.md` §5.2, SDK
`@anna-ai/app-runtime` ≥ 0.21.0).

What it shows:

| UI | SDK call | Server event(s) |
| --- | --- | --- |
| **Start session** | `anna.audio.session({llm:"agent"\|"none", context, system, maxDurationSec})` + `anna.audio.mic()` + `anna.audio.player()` | `session_started` → handle `models` / `audioConfig` |
| **Hold to talk** (push-to-talk, also `Space`) | `mic.onChunk → vs.sendAudio(pcm16)` — *only while pressed*; on release `vs.flushAudio()` | `asr_partial` / `asr_final` → `llm_start` / `llm_partial` / `llm_done` → `tts_audio` (→ `player.enqueue`) / `tts_done` (→ `player.flush`); `asr_paused` after release / idle, `asr_resumed{reconnect_ms}` on the next press |
| Speak while Anna talks | — | `interrupted{source:"barge_in"}` → `player.stop()` |
| **Set context** / auto-update | `vs.setContext(text)` (≤ 4 KB, replace semantics, applies from the next utterance, never enters history) | `context_updated` |
| **Say** (normal / high) | `vs.say(text, {priority, interrupt})` — TTS-only, no LLM | `say_start` / `say_done{status}` (+ `interrupted{source:"say"}` when preempting) |
| **Interrupt** | `vs.interrupt()` | — |
| **End** | `await vs.end()` → `{duration, total_asr_seconds, total_tts_characters}` | `session_ended` |

The footer shows first-audio latency (`asr_final → first tts_audio`, and
`say → first tts_audio`) so you can compare against the design targets
(`say` ≤ 1.5 s; conversation ≤ 1 s + model TTFT).

## Manifest

```json
"form_factors": ["desktop", "mobile"],
"host_api": {
  "audio": ["speak", "transcribe", "session"],
  "agent": { "session": { "auto": true }, "tools": [] }
}
```

- `form_factors ∋ "mobile"` is what lets the Anna mobile app open it; without
  it the launcher shows a "desktop" badge and refuses (default is desktop-only).

- `audio ∋ "session"` makes the host delegate `microphone` to the iframe
  (`allow="microphone"` + `Permissions-Policy: microphone=(self)`) and
  requires the user's `media_grant.audio.{speak, transcribe}`.
- `llm:"agent"` additionally needs `host_api.agent.session.auto` (the voice
  turn runs through this app's agent session; LLM usage is billed like
  `agent.session.run`).
- `anna.audio.mic()` tries an AudioWorklet (Blob-URL module) and falls back
  to `ScriptProcessorNode` when the bundle CSP (`script-src 'self'`) blocks
  `blob:` — the emitted 16 kHz PCM16 frames are identical either way.

## Run locally

```bash
pnpm install
pnpm dev          # anna-app dev — harness mocks the voice server
```

In the harness the session is **mocked in the host page**: ASR uses the
browser's `SpeechRecognition` (Chrome), the LLM turn runs through the
harness `agent.session`, and TTS uses `speechSynthesis` (so no `tts_audio`
frames arrive — the player stays idle). Everything else (events, context,
say, barge-in, end summary) follows the production protocol.

## Push-to-talk and the ASR connection

Streaming ASR only finalizes a sentence after enough trailing *silence
frames*, and the provider drops a connection that receives no audio for
~23 s. The host therefore (a) finalizes the pending sentence immediately on
`flushAudio()` (release), (b) pauses the ASR connection after
`vs.asrIdleTimeoutSec` (15 s) of silence, and (c) rebuilds it transparently
on the next `sendAudio` (~0.5 s, shown as `asr_resumed{reconnect_ms}`).
Always call `flushAudio()` when the user releases the button.

## Mobile (Anna RN app)

The same bundle runs unchanged inside the mobile shell. The host grants the
iframe `allow="microphone"` from `frame_permissions`, the RN side pre-requests
the OS microphone permission and switches the audio session when a voice
session opens, and relays `AppState` as the `host.app_state` event. Voice
sessions are foreground-only on iOS (WKWebView suspends `getUserMedia`), so
the demo ends the session when the app goes to the background.

## Cost note

ASR is billed **per second of audio you send**. Keep `sendAudio` behind
push-to-talk / a wake word; do not stream continuously.
