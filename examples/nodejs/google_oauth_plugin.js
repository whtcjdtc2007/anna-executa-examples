#!/usr/bin/env node
/**
 * Executa Google OAuth Plugin Example — Google Calendar via platform OAuth credentials (Node.js)
 *
 * Demonstrates:
 * 1. Declaring an OAuth-sourced credential (GOOGLE_ACCESS_TOKEN) in the Manifest
 * 2. Receiving the auto-injected OAuth access_token via context.credentials
 * 3. Using the token to call Google Calendar API
 * 4. The plugin experience is identical to API Key — OAuth complexity is handled by Nexus
 *
 * How it works (end-to-end):
 *   1. User authorizes Google in Nexus (/settings/authorizations), granting Calendar scopes
 *   2. Nexus stores tokens (AES-256-GCM encrypted), auto-refreshes when expired
 *   3. credential_mapping: "GOOGLE_ACCESS_TOKEN" → "$access_token"
 *   4. Agent invokes this plugin → resolved credentials injected via context.credentials
 *   5. Plugin reads context.credentials["GOOGLE_ACCESS_TOKEN"] — a valid OAuth access token
 *
 * Credential resolution priority (three tiers):
 *   1. Platform unified credentials — Google OAuth token from /settings/authorizations
 *   2. Plugin-level credentials — manually entered access token
 *   3. Environment variables — GOOGLE_ACCESS_TOKEN env var for local development
 *
 * Usage:
 *   node google_oauth_plugin.js
 *
 * Local development:
 *   GOOGLE_ACCESS_TOKEN=ya29.xxx node google_oauth_plugin.js
 *
 * Protocol:
 *   - stdin:  JSON-RPC requests (one per line)
 *   - stdout: JSON-RPC responses (one per line)
 *   - stderr: Log output
 */

const readline = require("readline");

// ─── Manifest ────────────────────────────────────────────────────────
//
// Key point: The credential name "GOOGLE_ACCESS_TOKEN" aligns with the
// Google provider's credential_mapping in Nexus.
//
// This means: when a user has authorized Google on the platform, ANY plugin
// that declares "GOOGLE_ACCESS_TOKEN" automatically receives the OAuth token.
// No per-plugin OAuth setup needed.
//
// Required Google OAuth scopes (user selects when authorizing):
//   - https://www.googleapis.com/auth/calendar.readonly   (for list_events)
//   - https://www.googleapis.com/auth/calendar.events     (for create_event)

const MANIFEST = {
  name: "google-calendar-tool",
  display_name: "Google Calendar Tool",
  version: "1.0.0",
  description:
    "Google Calendar event manager — demonstrates OAuth credential usage via platform authorization",
  author: "Anna Developer",
  // ─── OAuth Credential Declaration ──────────────────────────────────
  // "GOOGLE_ACCESS_TOKEN" is the canonical name for Google OAuth tokens.
  // It maps to "$access_token" in the platform's Google provider registry.
  //
  // Alternative names that resolve to the same token:
  //   - GMAIL_ACCESS_TOKEN (alias)
  //   - GOOGLE_WORKSPACE_CLI_TOKEN (alias)
  //   - YOUTUBE_ACCESS_TOKEN (alias)
  //   - GOOGLE_DOCS_ACCESS_TOKEN (alias)
  //   - GOOGLE_SHEETS_ACCESS_TOKEN (alias)
  credentials: [
    {
      name: "GOOGLE_ACCESS_TOKEN",
      display_name: "Google Access Token",
      description:
        "Google OAuth Access Token — automatically provided by the platform when user authorizes Google at /settings/authorizations. Required scopes: calendar.readonly, calendar.events",
      required: true,
      sensitive: true,
    },
  ],
  tools: [
    {
      name: "list_events",
      description:
        "List upcoming Google Calendar events",
      parameters: [
        {
          name: "max_results",
          type: "integer",
          description: "Maximum number of events to return (1-20, default 10)",
          required: false,
          default: 10,
        },
        {
          name: "calendar_id",
          type: "string",
          description:
            "Calendar ID (default: 'primary' for the user's main calendar)",
          required: false,
          default: "primary",
        },
      ],
    },
    {
      name: "create_event",
      description: "Create a new Google Calendar event",
      parameters: [
        {
          name: "summary",
          type: "string",
          description: "Event title",
          required: true,
        },
        {
          name: "start_time",
          type: "string",
          description:
            "Start time in ISO 8601 format (e.g. 2025-04-15T10:00:00+08:00)",
          required: true,
        },
        {
          name: "end_time",
          type: "string",
          description:
            "End time in ISO 8601 format (e.g. 2025-04-15T11:00:00+08:00)",
          required: true,
        },
        {
          name: "description",
          type: "string",
          description: "Event description (optional)",
          required: false,
          default: "",
        },
        {
          name: "location",
          type: "string",
          description: "Event location (optional)",
          required: false,
          default: "",
        },
      ],
    },
  ],
  runtime: {
    type: "npm",
    min_version: "1.0.0",
  },
};

// ─── Credential Helper ───────────────────────────────────────────────

/**
 * Get credential value (resolution priority: context > env > default)
 * @param {Object|null} credentials - context.credentials from invoke
 * @param {string} name - Credential name
 * @param {string} [defaultValue] - Default value
 * @returns {string|undefined}
 */
function getCredential(credentials, name, defaultValue) {
  const creds = credentials || {};
  return creds[name] || process.env[name] || defaultValue;
}

// ─── Tool Implementation ─────────────────────────────────────────────

function toolListEvents(args, credentials) {
  const { max_results = 10, calendar_id = "primary" } = args;
  const accessToken = getCredential(credentials, "GOOGLE_ACCESS_TOKEN");

  if (!accessToken) {
    return {
      error: "GOOGLE_ACCESS_TOKEN not configured",
      hint: [
        "This plugin requires Google OAuth authorization.",
        "Configuration options (choose one):",
        "  1. Platform authorization (recommended): Go to /settings/authorizations,",
        "     connect Google, and grant 'Calendar Read' scope",
        "  2. Plugin-level credential: Enter an OAuth access_token in plugin settings",
        "  3. Local development: GOOGLE_ACCESS_TOKEN=ya29.xxx node google_oauth_plugin.js",
      ].join("\n"),
    };
  }

  const effectiveMax = Math.max(1, Math.min(20, max_results));

  // ─── Actual Calendar API call (commented out) ───
  // const https = require("https");
  // const now = new Date().toISOString();
  // const url = new URL(`https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendar_id)}/events`);
  // url.searchParams.set("maxResults", effectiveMax.toString());
  // url.searchParams.set("timeMin", now);
  // url.searchParams.set("singleEvents", "true");
  // url.searchParams.set("orderBy", "startTime");
  //
  // const options = {
  //   hostname: url.hostname,
  //   path: url.pathname + url.search,
  //   headers: {
  //     "Authorization": `Bearer ${accessToken}`,
  //     "Accept": "application/json",
  //   },
  // };
  // // https.get(options, (res) => { ... });
  // ────────────────────────────

  // Simulated data (for demonstration)
  const now = new Date();
  const events = [];
  for (let i = 0; i < effectiveMax && i < 5; i++) {
    const startDate = new Date(now.getTime() + i * 86400000 + 36000000); // +10h + i days
    const endDate = new Date(startDate.getTime() + 3600000); // +1h
    events.push({
      id: `evt_${Date.now()}_${i}`,
      summary: [
        "Team Standup",
        "Design Review",
        "Sprint Planning",
        "1:1 with Manager",
        "Tech Talk: OAuth2 Deep Dive",
      ][i],
      start: { dateTime: startDate.toISOString(), timeZone: "Asia/Shanghai" },
      end: { dateTime: endDate.toISOString(), timeZone: "Asia/Shanghai" },
      location: ["Conference Room A", "", "Room B", "Zoom", "Auditorium"][i],
      status: "confirmed",
    });
  }

  return {
    calendar_id,
    total: events.length,
    events,
    token_configured: true,
    token_preview:
      accessToken.length > 12
        ? `${accessToken.slice(0, 8)}...${accessToken.slice(-4)}`
        : "***",
    _note: "This is simulated data for demonstration purposes",
  };
}

function toolCreateEvent(args, credentials) {
  const {
    summary,
    start_time,
    end_time,
    description = "",
    location = "",
  } = args;
  const accessToken = getCredential(credentials, "GOOGLE_ACCESS_TOKEN");

  if (!accessToken) {
    return {
      error: "GOOGLE_ACCESS_TOKEN not configured",
      hint: [
        "This plugin requires Google OAuth authorization.",
        "Go to /settings/authorizations, connect Google,",
        "and grant 'Calendar Events' scope.",
      ].join("\n"),
    };
  }

  if (!summary) return { error: "summary is required" };
  if (!start_time) return { error: "start_time is required" };
  if (!end_time) return { error: "end_time is required" };

  // ─── Actual Calendar API call (commented out) ───
  // const https = require("https");
  // const body = JSON.stringify({
  //   summary,
  //   description,
  //   location,
  //   start: { dateTime: start_time },
  //   end: { dateTime: end_time },
  // });
  //
  // const options = {
  //   hostname: "www.googleapis.com",
  //   path: "/calendar/v3/calendars/primary/events",
  //   method: "POST",
  //   headers: {
  //     "Authorization": `Bearer ${accessToken}`,
  //     "Content-Type": "application/json",
  //     "Content-Length": Buffer.byteLength(body),
  //   },
  // };
  // ────────────────────────────

  // Simulated response
  return {
    created: true,
    event: {
      id: `evt_${Date.now()}`,
      summary,
      description,
      location,
      start: { dateTime: start_time },
      end: { dateTime: end_time },
      status: "confirmed",
      htmlLink: `https://calendar.google.com/calendar/event?eid=simulated_${Date.now()}`,
    },
    token_configured: true,
    _note: "This is simulated data for demonstration purposes",
  };
}

const TOOL_DISPATCH = {
  list_events: toolListEvents,
  create_event: toolCreateEvent,
};

// ─── JSON-RPC Handling ───────────────────────────────────────────────

function makeResponse(id, result, error) {
  const resp = { jsonrpc: "2.0", id };
  if (error !== undefined) resp.error = error;
  else resp.result = result;
  return resp;
}

function handleRequest(line) {
  let request;
  try {
    request = JSON.parse(line);
  } catch {
    return makeResponse(null, undefined, {
      code: -32700,
      message: "Parse error",
    });
  }

  const { id, method, params = {} } = request;

  switch (method) {
    case "describe":
      return makeResponse(id, MANIFEST);

    case "invoke": {
      const { tool, arguments: args = {}, context = {} } = params;
      const credentials = context.credentials || null;

      if (!tool) {
        return makeResponse(id, undefined, {
          code: -32602,
          message: "Missing 'tool' in params",
        });
      }
      const fn = TOOL_DISPATCH[tool];
      if (!fn) {
        return makeResponse(id, undefined, {
          code: -32601,
          message: `Unknown tool: ${tool}`,
          data: { available_tools: Object.keys(TOOL_DISPATCH) },
        });
      }
      try {
        const result = fn(args, credentials);
        return makeResponse(id, { success: true, data: result, tool });
      } catch (e) {
        return makeResponse(id, undefined, {
          code: -32603,
          message: `Tool execution failed: ${e.message}`,
        });
      }
    }

    case "health":
      return makeResponse(id, {
        status: "healthy",
        timestamp: new Date().toISOString(),
        version: MANIFEST.version,
        tools_count: MANIFEST.tools.length,
        credentials_declared: MANIFEST.credentials.length,
        auth_type: "oauth2 (via platform)",
      });

    default:
      return makeResponse(id, undefined, {
        code: -32601,
        message: `Method not found: ${method}`,
      });
  }
}

// ─── Main Loop ───────────────────────────────────────────────────────

const rl = readline.createInterface({ input: process.stdin });

process.stderr.write("🔌 Google Calendar OAuth credential plugin started (Node.js)\n");
process.stderr.write(
  `   Tools: ${Object.keys(TOOL_DISPATCH).join(", ")}\n`
);
process.stderr.write(
  `   Credentials required: ${MANIFEST.credentials.map((c) => c.name).join(", ")}\n`
);
process.stderr.write(
  "   Auth type: OAuth2 (via platform — plugin receives ready-to-use token)\n"
);

rl.on("line", (line) => {
  line = line.trim();
  if (!line) return;

  process.stderr.write(`← ${line}\n`);
  const responseObj = handleRequest(line);
  const payload = JSON.stringify(responseObj);
  process.stdout.write(payload + "\n");
  process.stderr.write(`→ ${payload}\n`);
});
