// Bundle harness test — drives bundle/app.js via @anna-ai/cli/test (mountBundle).
// Run with: pnpm test
import { describe, it, expect, beforeEach } from "vitest";
import { mountBundle, HostApiError } from "@anna-ai/cli/test";
import manifest from "../../manifest.json" with { type: "json" };

const TOOL_ID = "tool-test-focus-session-12345678";

function defaultMocks() {
  // Minimal in-memory focus-session simulator. Mirrors the real plugin's
  // shape (state + active.view) so the bundle is exercised end-to-end.
  let state: { active: any; history: any[] } = { active: null, history: [] };
  return {
    "tools.invoke": ({ tool_id, method, args }: any) => {
      if (tool_id !== TOOL_ID) {
        return { success: false, error: { code: "unknown_tool" } };
      }
      const action = args?.action;
      if (action === "start") {
        state.active = {
          topic: args.topic ?? "",
          duration_minutes: args.duration_minutes,
          started_at: 0,
          status: "running",
        };
        return {
          success: true,
          data: { active: { view: { ...state.active, remaining_seconds: args.duration_minutes * 60 } } },
        };
      }
      if (action === "get_state") {
        return {
          success: true,
          data: { active: state.active ? { view: { ...state.active, remaining_seconds: 1500 } } : null },
        };
      }
      if (action === "complete") {
        state.history.push(state.active);
        state.active = null;
        return { success: true, data: { active: null } };
      }
      return { success: false, error: { code: "unknown_action" } };
    },
  };
}

describe("focus-flow bundle", () => {
  let harness: Awaited<ReturnType<typeof mountBundle>>;

  beforeEach(async () => {
    harness = await mountBundle({ manifest: manifest as any, mocks: defaultMocks() });
  });

  it("starts a 25-minute session via tools.invoke", async () => {
    const res = await harness.runtime.tools.invoke({
      tool_id: TOOL_ID,
      method: "session",
      args: { action: "start", duration_minutes: 25, topic: "ship phase 9" },
    });
    expect((res as any).success).toBe(true);
    const last = harness.calls.lastOf("tools.invoke")!;
    expect(last.outcome).toBe("ok");
    expect(last.args).toMatchObject({ tool_id: TOOL_ID });
  });

  it("blocks fs.read because manifest.ui.host_api.fs is empty", async () => {
    await expect(
      harness.runtime.call("fs", "read", { path: "/etc/passwd" }),
    ).rejects.toBeInstanceOf(HostApiError);
    expect(harness.calls.last()?.outcome).toBe("denied");
  });

  it("delivers entry_payload events to bundle subscribers", async () => {
    const seen: any[] = [];
    harness.runtime.on("entry_payload", (p) => seen.push(p));
    harness.events.emit("entry_payload", { mode: "deep_focus" });
    await harness.wait(0);
    expect(seen).toEqual([{ mode: "deep_focus" }]);
  });
});
