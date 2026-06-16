export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(runMatchNestMaintenance(env));
  },

  async fetch(_request, env) {
    return runMatchNestMaintenance(env);
  },
};

async function runMatchNestMaintenance(env) {
  const baseUrl = requiredEnv(env.MATCHNEST_URL, "MATCHNEST_URL").replace(/\/$/, "");
  const dispatchToken = requiredEnv(env.NOTIFICATION_DISPATCH_TOKEN, "NOTIFICATION_DISPATCH_TOKEN");
  const headers = { "X-Notification-Dispatch-Token": dispatchToken };

  const health = await fetch(`${baseUrl}/health`, { cache: "no-store" });
  const refresh = await fetch(`${baseUrl}/background/tick`, { method: "POST", headers });
  const dispatch = await fetch(`${baseUrl}/notifications/dispatch`, { method: "POST", headers });

  return Response.json({
    ok: health.ok && refresh.ok && dispatch.ok,
    health: health.status,
    refresh: await safeJson(refresh),
    dispatch: await safeJson(dispatch),
  });
}

function requiredEnv(value, name) {
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

async function safeJson(response) {
  const text = await response.text();
  try {
    return { status: response.status, body: JSON.parse(text) };
  } catch {
    return { status: response.status, body: text };
  }
}
