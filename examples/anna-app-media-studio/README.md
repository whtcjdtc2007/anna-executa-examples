# Media Studio — Anna App sample

The reference App for the **platform media generation surface**
(`platform-media-generation-video-audio.md`, forum #373/#358): async
text/image-to-video jobs, synchronous TTS narration, capability discovery
with CU pricing, URL-expiry recovery, and persisting results to host
storage. It also bundles the **clip-narrator** executa demonstrating the
plugin-side reverse-RPC half of the same surface.

| UI action            | SDK call (`@anna-ai/app-runtime` ≥ 0.20.0)       |
| -------------------- | ------------------------------------------------ |
| **Model picker**     | `anna.llm.catalog({serviceType: "video_gen"})`   |
| **Generate video**   | `anna.video.generateAwait(args, {onProgress, signal})` |
| **Cancel**           | `AbortController.abort()` → `anna.video.cancelJob` |
| **Image → Video**    | `anna.image.generate` → `anna.video.generate({imageUrl})` |
| **TTS model picker** | `anna.llm.catalog({serviceType: "tts"})`         |
| **Speak (TTS)**      | `anna.audio.speak({text, model, voice, format, speed, language, timestamps})` |
| **Persist**          | `anna.upload.inline` (≤ 8 MB) / `negotiate`+`confirm` — video and narration |
| **Send to chat**     | `anna.chat.append_artifact`                      |
| **Re-sign URL**      | `anna.video.getJob({jobId})`                     |

Auth is the `app_session_token` minted by the AnnaApp runtime during
handshake — the app holds zero long-lived credentials and never touches a
provider API key.

## The async job model

Unlike `image.generate` (synchronous, seconds), video renders for minutes.
`video.generate` therefore returns **immediately**:

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

## Bundled executa: clip-narrator

[`executas/clip-narrator`](executas/clip-narrator/clip_narrator.py) is the
**plugin-side** mirror: an Executa v2 python tool using `executa_sdk`'s
`MediaClient` to call `video/generate` + `video/get_job` (wrapped by
`generate_await`) and `audio/speak` over reverse RPC. The Matrix relays
each call with the invoke's short-lived `media_token`; manifest declares

```json
"host_capabilities": ["llm.video", "llm.audio.speak"]
```

Tools: `clip_create` (await a clip), `clip_narrate` (TTS), `clip_story`
(clip + narration concurrently on one invoke token).

## Grants

Everything is gated by the per-app **media_grant** (plus `image_grant` /
`upload_grant` for the auxiliary paths) in the Anna Admin panel. Without
it, calls fail with `VIDEO_NOT_GRANTED` (-32120) / `AUDIO_NOT_GRANTED`
(-32140). There are **no per-feature rate caps** — the CU quota pool is
the only gate.

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
├── manifest.json                 # schema 3 — host_capabilities + ui.host_api.{video,audio,…}
├── bundle/                       # static SPA (index.html / app.js / style.css)
├── fixtures/happy-path.jsonl     # catalog → generate → events → getJob → speak → upload → -32122
└── executas/clip-narrator/       # Executa v2 python plugin (MediaClient reverse RPC)
```
