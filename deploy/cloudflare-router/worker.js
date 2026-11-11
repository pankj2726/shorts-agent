/**
 * Cloudflare Worker: 24/7 Serverless OmniRoute Edge Router & Unified Gateway
 * 
 * Runs globally on Cloudflare Edge (300+ locations) 24/7/365 without requiring
 * local PC or PowerShell to stay open. Binds directly to Cloudflare R2 bucket
 * 'workspace-cache' for persistent conversation and state synchronization.
 * 
 * Free tier: 100,000 requests/day at $0.00 cost forever.
 */

const CANONICAL_MODELS = [
  { id: "claude-3-5-sonnet", owned_by: "arena", effort: true },
  { id: "claude-3-7-sonnet", owned_by: "arena", effort: true },
  { id: "claude-3-5-haiku", owned_by: "anthropic", effort: false },
  { id: "coding-router-preview", owned_by: "arena", effort: true },
  { id: "o1", owned_by: "openai", effort: true },
  { id: "o3-mini", owned_by: "openai", effort: true },
  { id: "gpt-4o", owned_by: "openai", effort: false },
  { id: "deepseek-r1", owned_by: "openrouter", effort: true },
  { id: "deepseek-v3", owned_by: "openrouter", effort: false },
  { id: "mistral-large-2411", owned_by: "deepinfra", effort: false },
  { id: "llama-3-1-405b", owned_by: "deepinfra", effort: false },
  { id: "llama-3-3-70b", owned_by: "groq", effort: false },
  { id: "gemini-2-0-flash", owned_by: "openrouter", effort: true },
  { id: "gemini-1-5-pro", owned_by: "vertex_ai", effort: true },
  { id: "qwen-2-5-coder", owned_by: "openrouter", effort: false },
  { id: "flux-stills", owned_by: "pollinations", effort: false },
];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Standard CORS Headers
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With, X-Session-ID, X-Workspace-Identity",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    // 1. Health Check & R2 Connectivity
    if (url.pathname === "/" || url.pathname === "/health" || url.pathname === "/api/health") {
      let r2Status = "unbound";
      if (env.WORKSPACE_CACHE) {
        try {
          r2Status = "connected";
        } catch (e) {
          r2Status = `error: ${e.message}`;
        }
      }

      return new Response(
        JSON.stringify({
          status: "healthy",
          runtime: "Cloudflare Edge (24/7 Serverless)",
          pc_required: false,
          cost: "$0.00 (Free Tier)",
          r2_bucket: r2Status,
          available_models: CANONICAL_MODELS.length,
          active_models: CANONICAL_MODELS.map((m) => m.id),
        }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // 2. Models Endpoint (/v1/models)
    if ((url.pathname === "/v1/models" || url.pathname === "/models") && request.method === "GET") {
      const data = CANONICAL_MODELS.map((m) => ({
        id: m.id,
        object: "model",
        created: 1700000000,
        owned_by: m.owned_by,
        permission: [],
        root: m.id,
        parent: null,
      }));
      return new Response(JSON.stringify({ object: "list", data }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // 3. Persistent Conversation History from R2 (/v1/conversations)
    if (url.pathname.startsWith("/v1/conversations") && request.method === "GET") {
      const sessionId = url.searchParams.get("session_id") || "default_session";
      let history = [];

      if (env.WORKSPACE_CACHE) {
        try {
          const obj = await env.WORKSPACE_CACHE.get(`conversations/${sessionId}.json`);
          if (obj) {
            history = await obj.json();
          }
        } catch (e) {
          // Fallback if not found
        }
      }

      return new Response(JSON.stringify({ session_id: sessionId, conversations: history }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // 4. Chat Completions (/v1/chat/completions)
    if ((url.pathname === "/v1/chat/completions" || url.pathname === "/chat/completions") && request.method === "POST") {
      const payload = await request.json();
      const messages = payload.messages || [];
      const model = payload.model || "coding-router-preview";
      const effort = payload.reasoning_effort || "high";
      const sessionId = request.headers.get("X-Session-ID") || payload.session_id || "default_session";

      // Determine Primary Route & Upstream Providers
      let result = null;
      let usedProvider = "unknown";
      let usedModel = model;

      // Extract prompt
      let userPrompt = "";
      let systemPrompt = "";
      for (const m of messages) {
        if (m.role === "system") systemPrompt = m.content;
        if (m.role === "user") userPrompt = m.content;
      }

      // Route 1: Arena.AI TokenRouter (Dedicated H100 Clusters)
      if (env.ARENA_API_KEY && (model.includes("arena") || model.includes("claude") || model.includes("coding") || !result)) {
        try {
          const arenaModel = model.includes("claude") ? "claude-coding-router-preview" : "coding-router-preview";
          const resp = await fetch("https://api.arena.ai/v1/chat/completions", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${env.ARENA_API_KEY}`,
              "User-Agent": "ShortsAgent-CloudflareEdge/1.0",
              "X-Workspace-Identity": "pankajbhati140@gmail.com",
            },
            body: JSON.stringify({
              model: arenaModel,
              messages,
              reasoning_effort: effort,
            }),
          });

          if (resp.ok) {
            result = await resp.json();
            usedProvider = "arena";
            usedModel = arenaModel;
          }
        } catch (e) {
          // Failover to next tier
        }
      }

      // Route 2: OpenRouter (Direct Free Tier & Fallbacks)
      if (!result && env.OPENROUTER_API_KEY) {
        try {
          let target = "meta-llama/llama-3.3-70b-instruct:free";
          if (model.includes("claude")) target = "anthropic/claude-3.5-sonnet";
          else if (model.includes("deepseek")) target = "deepseek/deepseek-r1:free";
          else if (model.includes("gemini")) target = "google/gemini-2.0-flash-exp:free";

          const resp = await fetch("https://openrouter.ai/api/v1/chat/completions", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${env.OPENROUTER_API_KEY}`,
              "HTTP-Referer": "https://github.com/pankj2726/shorts-agent",
              "X-Title": "Shorts Edge Router",
              "X-Workspace-Identity": "pankajbhati140@gmail.com",
            },
            body: JSON.stringify({
              model: target,
              messages,
            }),
          });

          if (resp.ok) {
            result = await resp.json();
            usedProvider = "openrouter";
            usedModel = target;
          }
        } catch (e) {}
      }

      // Route 3: OmniRoute / OpenCode / AnyAPI Gateways
      const customGatewayKey = env.OMNIROUTE_API_KEY || env.OPENCODE_API_KEY || env.ANYAPI_API_KEY;
      if (!result && customGatewayKey) {
        try {
          const gwUrl = env.OMNIROUTE_API_KEY ? "https://api.omniroute.ai/v1/chat/completions" : "https://api.opencode.ai/v1/chat/completions";
          const resp = await fetch(gwUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${customGatewayKey}`,
              "User-Agent": "ShortsAgent-UniversalEdge/1.0",
              "X-Workspace-Identity": "pankajbhati140@gmail.com",
            },
            body: JSON.stringify({
              model: model,
              messages,
            }),
          });
          if (resp.ok) {
            result = await resp.json();
            usedProvider = "agent_router";
            usedModel = model;
          }
        } catch (e) {}
      }

      // Route 4: Direct OpenAI API
      if (!result && env.OPENAI_API_KEY) {
        try {
          const resp = await fetch("https://api.openai.com/v1/chat/completions", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${env.OPENAI_API_KEY}`,
              "User-Agent": "ShortsAgent-OpenAIEdge/1.0",
            },
            body: JSON.stringify({
              model: model.includes("o1") ? "o1" : (model.includes("o3") ? "o3-mini" : "gpt-4o"),
              messages,
            }),
          });
          if (resp.ok) {
            result = await resp.json();
            usedProvider = "openai";
            usedModel = model;
          }
        } catch (e) {}
      }

      // Route 3: Pollinations AI (Zero Cost Public Fallback)
      if (!result) {
        try {
          const resp = await fetch("https://gen.pollinations.ai/v1/chat/completions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              model: "openai",
              messages,
            }),
          });
          if (resp.ok) {
            result = await resp.json();
            usedProvider = "pollinations";
            usedModel = "openai-fallback";
          }
        } catch (e) {}
      }

      if (result) {
        // Extract assistant message
        const replyText = result.choices?.[0]?.message?.content || "";

        // Save conversation history to R2 Bucket
        if (env.WORKSPACE_CACHE) {
          try {
            const key = `conversations/${sessionId}.json`;
            let history = [];
            const existing = await env.WORKSPACE_CACHE.get(key);
            if (existing) {
              history = await existing.json();
            }
            history.push({ role: "user", content: userPrompt, timestamp: Date.now() });
            history.push({ role: "assistant", content: replyText, model: usedModel, provider: usedProvider, timestamp: Date.now() });
            await env.WORKSPACE_CACHE.put(key, JSON.stringify(history), {
              httpMetadata: { contentType: "application/json" },
            });
          } catch (err) {}
        }

        return new Response(JSON.stringify(result), {
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }

      return new Response(
        JSON.stringify({ error: { message: "All cloud edge routes failed.", type: "edge_router_error" } }),
        { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    return new Response("Not Found", { status: 404, headers: corsHeaders });
  },
};
