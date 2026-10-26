const GITHUB_API = "https://api.github.com";

async function dispatchWorkflow(env) {
  const repo = env.GITHUB_REPO;
  const token = env.GITHUB_TOKEN;
  const branch = env.GITHUB_BRANCH || "main";
  const workflow = env.WORKFLOW_FILE || "email-cleaner.yml";

  if (!repo || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) {
    throw new Error("GITHUB_REPO must be in owner/repository format");
  }
  if (!token) {
    throw new Error("GITHUB_TOKEN is not configured");
  }
  if (!/^[A-Za-z0-9_.-]+\.ya?ml$/.test(workflow)) {
    throw new Error("WORKFLOW_FILE must be a workflow filename");
  }

  const response = await fetch(
    `${GITHUB_API}/repos/${repo}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "outlook-email-cleaner",
      },
      body: JSON.stringify({ ref: branch }),
    },
  );

  if (!response.ok) {
    const details = await response.text();
    throw new Error(`GitHub workflow dispatch failed: ${response.status} ${details}`);
  }
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(
      dispatchWorkflow(env).catch((error) => {
        console.error("Scheduled workflow dispatch failed", error);
        throw error;
      }),
    );
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/trigger") {
      return new Response("email-cleaner cron relay: ok\n", { status: 200 });
    }
    if (request.method !== "POST") {
      return new Response("Method Not Allowed\n", {
        status: 405,
        headers: { Allow: "POST" },
      });
    }
    if (
      !env.TRIGGER_SECRET ||
      request.headers.get("Authorization") !== `Bearer ${env.TRIGGER_SECRET}`
    ) {
      return new Response("Unauthorized\n", { status: 401 });
    }

    try {
      await dispatchWorkflow(env);
      return Response.json({ ok: true });
    } catch (error) {
      console.error("Manual workflow dispatch failed", error);
      return Response.json({ ok: false, error: String(error) }, { status: 502 });
    }
  },
};
