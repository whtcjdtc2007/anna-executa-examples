# Media Studio — Anna App sample

The reference App for the **platform media generation surface**
(`platform-media-generation-video-audio.md`, forum #373/#358, and
`platform-media-generation-music.md`, forum #426): async text/image-to-video
jobs, async **music scores**, synchronous TTS narration, capability discovery
with CU pricing, URL-expiry recovery, and persisting results to host
storage. It also bundles the **clip-narrator** executa demonstrating the
plugin-side reverse-RPC half of the same surface.

| UI action            | SDK call (`@anna-ai/app-runtime` ≥ 0.23.0)       |
| -------------------- | ------------------------------------------------ |
| **Model picker**     | `anna.llm.catalog({serviceType: "video_gen"})`   |
| **Generate video**   | `anna.video.generateAwait(args, {onProgress, signal})` |
| **Cancel**           | `AbortController.abort()` → `anna.video.cancelJob` |
| **Image → Video**    | `anna.image.generate` → `anna.video.generate({imageUrl})`; with **Run first frame as job** ticked: `anna.image.generateAwait(args, {onProgress, signal})` (`ijob_…`, reload-safe, cancellable; `result.images[].r2Key` re-signable via `anna.image.resign`) |
| **TTS model picker** | `anna.llm.catalog({serviceType: "tts"})`         |
| **Speak (TTS)**      | `anna.audio.speak({text, model, voice, format, speed, language, timestamps})` |
| **Music model picker** | `anna.llm.catalog({serviceType: "music_gen"})` — the whole form (lyrics / instrumental / duration / reference image) is derived from `capabilities` |
| **Generate music**   | `anna.music.generateAwait(args, {onProgress, signal})` — `idempotencyKey` replay shows `↩ idempotent replay · 0 CU charged` |
| **Persist**          | `anna.upload.inline` (≤ 8 MB) / `negotiate`+`confirm` — video, music and narration |
| **Send to chat**     | `anna.chat.append_artifact`                      |
| **Re-sign URL**      | `anna.video.getJob({jobId})`                     |

Auth is the `app_session_token` minted by the AnnaApp runtime during
handshake — the app holds zero long-lived credentials and never touches a
provider API key.

## Two channels

The **Channel** selector at the top of the sidebar switches generate,
re-sign, speak and transcribe between two adapters with the same methods
(`hostChannel` / `executaChannel` in `bundle/app.js`); the panels call
`channel.<method>()` and never branch on which one is active.

| | **Host API** | **Reverse RPC** |
| --- | --- | --- |
| Path | page → `anna.video` / `music` / `image` / `audio` → host | page → `anna.tools.invoke` → clip-narrator `media_call` → `video/*` · `music/*` · `image/*` · `audio/*` → host |
| Job progress | `media_job` events + adaptive polling inside `generateAwait` | one `tools.invoke` per step: `submit`, then a `get_job` poll every 5 s |
| Cancel | `AbortController.abort()` → `cancelJob` | `AbortController.abort()` → `media_call(op=cancel_job)` |
| First frame | sync `image.generate`, or a job when ticked | always a job (`image/generate_async`) |
| Billing and ownership in production | this App | the Executa — a job is only visible on the channel that created it, so every job (and history entry) remembers its channel |
| Result size | no tool-result limit | a tool result is capped at 4 MiB (256 KB for an async tool job): return URLs, never bytes |

**Transcribe it back** (under the narration player) sends the synthesized
audio to `audio.transcribe` / `audio/transcribe` on the active channel.
Catalog discovery, uploads, history and the error demos are page-side and
always use the Host API.

Locally the Reverse RPC channel needs `@anna-ai/cli` ≥ 0.1.68 (earlier
versions answer `-32601` for these reverse RPCs) and a signed-in
`anna-app dev`; both channels then reach the real platform and are billed to
your dev App.

## The async job model

Unlike `image.generate` (synchronous, seconds), video renders for minutes.
`video.generate` therefore returns **immediately** (image has the same job
shape as an opt-in — `image.generateAsync` / `image.editAsync` → `ijob_…` —
for 2K/4K or `quality: "high"` renders that take 60–120 s):

```json
{"jobId": "vjob_…", "state": "queued", "estimatedCostCU": 40, "deadlineAt": "…"}
```

`estimatedCostCU` is **pre-charged** from the user's CU pool and fully
refunded on failure / cancel / expiry. Progress then arrives two ways:

1. **`media_job` host events** — pushed into the iframe
   (`{jobId, state, seq, progress, terminal}`); terminal pushes never carry
   the result.
2. **`video.getJob` polling** — the authoritative snapshot; on `succeeded`
   the `result.url` presigned GET (~30 min TTL) is **freshly re-signed on
   every call**, which is also the recovery path after URL expiry (no
   regeneration, no extra billing).

`anna.video.generateAwait` packages both — event subscription with an
adaptive polling fallback — into one awaitable, with `onProgress`,
`AbortSignal` (queued cancel = guaranteed refund; running cancel is
best-effort), and a wall-clock timeout derived from `deadlineAt`.

## Capability discovery & pricing

`anna.llm.catalog({serviceType: "video_gen"})` returns, per active model,
the **allowed parameter space** (duration range/enum, resolutions, aspect
ratios, `imageToVideo`, native `audio`) and a **CU pricing block**
(`cuByResolution` / `cuPerSecond` / `cuPerSecondAudioOn`), plus the user's
`quota.remainingCU`. The sidebar selects are populated entirely from this
response — nothing is hardcoded — and a client-side estimate mirrors the
authoritative `estimatedCostCU` the server returns at submit time.

Out-of-range parameters are rejected at submit with the canonical
validation error (jsonrpc `-32122`, carrying the allowed range in
`.details`) — try the **Error demos** section.

## Narration — same catalog-driven pattern as video

`anna.llm.catalog({serviceType: "tts"})` returns, per active TTS model,
its **voice whitelist** (`capabilities.voices[]` with `id`/`label`/
`languages`, plus `defaultVoice`), the **option space**
(`options.{speed:{min,max,default}, format:{values,default}, language:{values}}`),
`maxCharsPerCall`, `timestamps` support, and `pricing.cuPer1kChars`. The
narration panel builds its model → voice / language / format / speed /
timestamps controls entirely from that response — exactly as the video
panel builds duration / resolution / ratio from `video_gen` capabilities —
and shows a live `chars / cap · ~CU` estimate.

`anna.audio.speak` is synchronous: `{text, model?, voice?, language?,
format?, speed?, timestamps?, delivery}` → `{url | audioBase64, mimeType,
charCount, billedCostCU, model, voice, expiresIn}`. The response echoes the
**resolved** model/voice so the UI can show what actually ran when the
caller left them to the host's defaults. Voices are a per-model whitelist:
a voice from another model fails with `-32145 AUDIO_VOICE_INVALID` whose
`.details.allowed` lists the valid ids — try it in **Error demos**.

Narration can be persisted to host storage the same way as the video
(inline ≤ 8 MB, negotiate+confirm above).

## Music — capability-driven, never silently downgraded

Music models differ a lot (MiniMax sings your lyrics verbatim and *requires*
them for vocal tracks; Lyria embeds lyrics in the prompt and may rewrite
them; Stable Audio is instrumental-only; ElevenLabs / Stable Audio / ACE-Step
take an exact `durationSec`, MiniMax / Lyria decide the length themselves).
The panel never hard-codes any of this — it reads `capabilities`:

```js
const { models } = await anna.llm.catalog({ serviceType: "music_gen" });
// capabilities: { lyrics, lyricsMode: "field"|"prompt_embedded", lyricsRequiredForVocals,
//   instrumental, instrumentalOnly, vocals, duration: {type:"fixed"|"range",…},
//   formats, referenceImage, stems:false, continuation:false, options (schema v2) }
// pricing: { unit: "per_song"|"per_second"|"per_minute", cuPerSong | cuPerSecond | cuPerMinute(+roundUp) }
```

Sending something a model cannot do fails fast with
`MUSIC_FEATURE_UNSUPPORTED` (-32165) and `details.alternatives` — the panel
renders those as one-click model switches (try **-32165 lyrics on
instrumental-only model** in the error demos). **Idempotent replay** (same
`idempotencyKey` within 24 h) returns the existing job and holds no CU — try
the second error-demo button. The agent → App hand-off is covered too: an
`entry_payload.music = {prompt, lyrics, model, instrumental, durationSec,
idempotencyKey}` prefills the panel (nothing auto-submits).

## Bundled executa: clip-narrator

[`executas/clip-narrator`](executas/clip-narrator/clip_narrator.py) is the
**plugin-side** mirror: an Executa v2 python tool using `executa_sdk`'s
`MediaClient` to call `video/generate` + `video/get_job` (wrapped by
`generate_await`), `music/generate` + `music/get_job` (wrapped by
`music_generate_await`) and `audio/speak` over reverse RPC. The Matrix relays
each call with the invoke's short-lived `media_token`; manifest declares

```json
"host_capabilities": ["llm.video", "llm.music", "llm.audio.speak"]
```

Tools: `clip_create` (await a clip), `clip_narrate` (TTS), `clip_score`
(await a background score; `idempotency_key` = invoke id so a retried tool
call never pays twice; -32165 alternatives are returned to the agent),
`clip_story` (clip + narration concurrently on one invoke token), and
`media_call` — one reverse RPC per call, selected by `op`:

| `op` | Reverse RPC |
| --- | --- |
| `submit` (`kind` = `image` / `video` / `music`) | `image/generate_async` (`image/edit_async` when `request.image_url` is set) · `video/generate` · `music/generate` |
| `get_job` | `image/get_job` · `video/get_job` · `music/get_job` |
| `cancel_job` | `image/cancel_job` · `video/cancel_job` · `music/cancel_job` |
| `resign` | `image/resign` |
| `speak` | `audio/speak` |
| `transcribe` | `audio/transcribe` |

`request` takes the arguments of the matching Host API call; the plugin maps
them onto the SDK's keyword arguments. `media_call` never waits for a job
and answers in-band (`{ok: true, result}` or
`{ok: false, error: {code, message, data}}`), which is what lets the page
drive it as the Reverse RPC channel.

Image has the same plugin-side job surface in `executa_sdk.ImageClient`
(`image/generate_async` · `edit_async` · `get_job` · `cancel_job` · `resign`,
wrapped by `generate_await` / `edit_await`; node `imageGenerateAwait`, go
`GenerateAwait`). Polling cadence equals music (5 s / 9 min). Keep
`await_timeout_s` inside the invoke budget — for longer renders return the
`jobId` as the invoke result and resume with `get_job` on the next call;
`resign([...r2_keys])` re-signs already-paid artifacts for up to 7 days.

## Grants

Everything is gated by the per-app **media_grant** (plus `image_grant` /
`upload_grant` for the auxiliary paths) in the Anna Admin panel. Without
it, calls fail with `VIDEO_NOT_GRANTED` (-32120) / `MUSIC_NOT_GRANTED`
(-32160) / `AUDIO_NOT_GRANTED` (-32140). There are **no per-feature rate
caps or per-invoke job caps** — the CU quota pool is the only gate.

## Run it

```bash
cd examples/anna-app-media-studio
npm install
npm run dev        # local harness; see fixtures/happy-path.jsonl for the mock flow
npm run validate   # manifest + bundle checks
```

Publish (mints real tool_ids and rewrites `bundle/anna-tool-ids.js`):

```bash
npx anna-app apps publish
```

## Files

```
anna-app-media-studio/
├── app.json                      # store listing + bundled_executas
├── manifest.json                 # schema 3 — host_capabilities + ui.host_api.{video,music,audio,…}
├── bundle/                       # static SPA (index.html / app.js / style.css)
├── fixtures/happy-path.jsonl     # catalog → generate → events → getJob → speak → upload → -32122 → music (replay, -32165)
└── executas/clip-narrator/       # Executa v2 python plugin (MediaClient + ImageClient reverse RPC)
```
