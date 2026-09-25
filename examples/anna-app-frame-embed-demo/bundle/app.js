// Frame Embed Demo — the "jump to cited timestamp" pattern from forum #343.
//
// No Host API calls, no SDK: the whole demo is the manifest's
// csp_overrides["frame-src"] declaration plus the <iframe allow=...>
// delegation in index.html. This file only swaps the embed URL.

const EMBED_BASE = "https://www.youtube-nocookie.com/embed/aqz-KE-bpKQ";

const player = document.getElementById("yt");
document.getElementById("chapters").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-start]");
  if (!btn) return;
  const start = Number(btn.dataset.start) || 0;
  // autoplay=1 works because the platform delegated `autoplay` to the
  // declared frame-src origins — and index.html forwards it via allow=.
  player.src = `${EMBED_BASE}?rel=0&start=${start}&autoplay=1`;
});
