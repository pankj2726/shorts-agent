import re

path = 'shorts-agent/agent/modules/omniroute_embedded.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update _is_provider_online
old_online = '''    def _is_provider_online(self, prov: str) -> bool:
        if prov == "pollinations":
            return True
        key_map = {
            "arena": ["ARENA_API_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_CONSOLE_API_KEY", "CLAUDE_API_KEY"],
            "openai": ["OPENAI_API_KEY", "CHATGPT_API_KEY"],
            "openrouter": ["OPENROUTER_API_KEY"],
            "omniroute": ["OMNIROUTE_API_KEY"],
            "opencode": ["OPENCODE_API_KEY"],
            "anyapi": ["ANYAPI_API_KEY"],
            "groq": ["GROQ_API_KEY"],
            "deepinfra": ["DEEPINFRA_API_KEY"],
            "vertex_ai": ["VERTEX_API_KEY", "GEMINI_API_KEY"],
            "fal_ai": ["FAL_KEY", "FAL_AI_API_KEY"]
        }
        keys = key_map.get(prov, [])
        return any(bool(os.environ.get(k)) for k in keys)'''

new_online = '''    def _is_provider_online(self, prov: str) -> bool:
        if prov == "pollinations":
            return True
        key_map = {
            "arena": ["ARENA_API_KEY", "ARENA_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_CONSOLE_API_KEY", "CLAUDE_API_KEY"],
            "openai": ["OPENAI_API_KEY", "CHATGPT_API_KEY"],
            "openrouter": ["OPENROUTER_API_KEY"],
            "omniroute": ["OMNIROUTE_API_KEY"],
            "opencode": ["OPENCODE_API_KEY"],
            "anyapi": ["ANYAPI_API_KEY"],
            "agnes": ["AGNES_API_KEY"],
            "agentrouter": ["AGENTROUTER_API_KEY"],
            "groq": ["GROQ_API_KEY"],
            "deepinfra": ["DEEPINFRA_API_KEY"],
            "vertex_ai": ["VERTEX_API_KEY", "GEMINI_API_KEY"],
            "fal_ai": ["FAL_KEY", "FAL_AI_API_KEY"]
        }
        keys = key_map.get(prov, [])
        return any(bool(os.environ.get(k)) for k in keys)'''

assert old_online in content, 'old_online not found'
content = content.replace(old_online, new_online)

# 2. Update _call_groq_text
old_groq = '''        target_model = "llama-3.3-70b-versatile" if "gpt" in model or "nano" in model else model'''
new_groq = '''        # Preserve specified model slug without clobbering; fallback to active Groq flagship if empty/generic
        if not model or model in ("gpt-5.4-nano", "default"):
            target_model = "openai/gpt-oss-120b"
        else:
            target_model = model'''

assert old_groq in content, 'old_groq not found'
content = content.replace(old_groq, new_groq)

# 3. Update _call_arena_text
old_arena = '''    def _call_arena_text(
        self, prompt: str, system_prompt: Optional[str], model: str, timeout: int
    ) -> Dict[str, Any]:
        key = os.environ.get("ARENA_API_KEY")
        if not key:
            raise ValueError("ARENA_API_KEY missing")

        url = "https://api.arena.ai/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Default to Arena coding router if generic model passed
        target_model = "coding-router-preview" if "gpt" in model or "nano" in model else model
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "reasoning_effort": "high",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "ShortsAgent-ArenaRouter/1.0",
                "X-Workspace-Identity": "pankajbhati140@gmail.com",
                "X-Organization-Name": "ShortsForge-Enterprise",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            choice = res_json["choices"][0]["message"]["content"]
            return {
                "provider": "arena",
                "model": target_model,
                "cost": 0.0,
                "text": choice.strip(),
                "status": "success"
            }'''

new_arena = '''    def _call_arena_text(
        self, prompt: str, system_prompt: Optional[str], model: str, timeout: int, effort: Optional[str] = None
    ) -> Dict[str, Any]:
        return ArenaClusterClient.get_instance().complete(
            prompt=prompt,
            system=system_prompt,
            model=model,
            effort=effort,
            timeout=timeout,
        )'''

assert old_arena in content, 'old_arena not found'
content = content.replace(old_arena, new_arena)

# 4. Update _call_openrouter_text (line 438 bug fix)
old_openrouter = '''        # Ensure we use free model suffix if model not explicitly specified
        target_model = model if ":" in model else f"{model}:free"'''
new_openrouter = '''        # Use specified model without destructive suffixing; fallback to default free model if empty
        target_model = model or "deepseek/deepseek-r1:free"'''

assert old_openrouter in content, 'old_openrouter not found'
content = content.replace(old_openrouter, new_openrouter)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print('SUCCESS')
