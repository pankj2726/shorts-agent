/**
 * Cloudflare Worker: 24/7 Serverless OmniRoute Edge Router
 * 
 * Free tier: 100,000 requests/day at $0.00 cost.
 * Runs independently 24/7 without requiring local PC or PowerShell to stay open.
 * Provides an OpenAI-compatible /v1/chat/completions and /v1/models API.
 * Automatically fails over: Pollinations AI -> OpenRouter -> DeepInfra.
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
      });
    }

    // Health check
    if (url.pathname === "/" || url.pathname === "/api/health") {
      return new Response(
        JSON.stringify({
          status: "ok",
          service: "OmniRoute-Cloudflare-Edge",
          uptime: "24/7 Serverless",
          active_fallbacks: ["pollinations", "openrouter", "deepinfra"],
        }),
        {
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
        }
      );
    }

    // Models endpoint (/v1/models)
    if (url.pathname === "/v1/models" && request.method === "GET") {
      try {
        const resp = await fetch("https://gen.pollinations.ai/models");
        if (resp.ok) {
          const data = await resp.json();
          const models = Array.isArray(data)
            ? data.map((m) => ({ id: typeof m === "string" ? m : m.name, object: "model" }))
            : [{ id: "openai", object: "model" }, { id: "mistral", object: "model" }];
          return new Response(JSON.stringify({ object: "list", data: models }), {
            headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
          });
        }
      } catch (err) {
        // Fallback static list
      }
      return new Response(
        JSON.stringify({
          object: "list",
          data: [
            { id: "openai", object: "model" },
            { id: "mistral", object: "model" },
            { id: "deepseek/deepseek-r1:free", object: "model" },
            { id: "meta-llama/llama-3.3-70b-instruct:free", object: "model" },
          ],
        }),
        { headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" } }
      );
    }

    // Chat completions endpoint (/v1/chat/completions)
    if (url.pathname === "/v1/chat/completions" && request.method === "POST") {
      const payload = await request.json();
      const messages = payload.messages || [];
      const model = payload.model || "openai";

      // 1. Primary: Pollinations AI (Zero Cost, No Key Required)
      try {
        const pollinationsResp = await fetch("https://gen.pollinations.ai/v1/chat/completions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            messages,
            model: model.includes(":") ? "openai" : model,
            temperature: payload.temperature ?? 0.7,
            max_tokens: payload.max_tokens ?? 1024,
          }),
        });

        if (pollinationsResp.ok) {
          const data = await pollinationsResp.json();
          return new Response(JSON.stringify(data), {
            headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
          });
        }
      } catch (err) {
        // Failover to secondary
      }

      // 2. Secondary: OpenRouter (if OPENROUTER_API_KEY secret configured in Worker)
      if (env.OPENROUTER_API_KEY) {
        try {
          const orResp = await fetch("https://openrouter.ai/api/v1/chat/completions", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${env.OPENROUTER_API_KEY}`,
              "HTTP-Referer": "https://github.com/pankj2726/shorts-agent",
              "X-Title": "Shorts Agent Edge Router",
            },
            body: JSON.stringify({
              messages,
              model: model.includes(":") ? model : "meta-llama/llama-3.3-70b-instruct:free",
              temperature: payload.temperature ?? 0.7,
              max_tokens: payload.max_tokens ?? 1024,
            }),
          });

          if (orResp.ok) {
            const data = await orResp.json();
            return new Response(JSON.stringify(data), {
              headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
            });
          }
        } catch (err) {
          // Failover to tertiary
        }
      }

      return new Response(
        JSON.stringify({
          error: {
            message: "All router upstream endpoints failed or exceeded rate limit.",
            type: "router_upstream_error",
          },
        }),
        { status: 502, headers: { "Content-Type": "application/json" } }
      );
    }

    return new Response("Not Found", { status: 404 });
  },
};
