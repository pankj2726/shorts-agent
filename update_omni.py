path = 'shorts-agent/agent/modules/omniroute_embedded.py'
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()

# 1. Update _is_provider_online to include ARENA_KEY, AGNES_API_KEY, AGENTROUTER_API_KEY
idx = c.find('def _is_provider_online') - 4
end_idx = c.find('return any(bool(os.environ.get(k)) for k in keys)', idx) + len('return any(bool(os.environ.get(k)) for k in keys)')
old_online = c[idx:end_idx]

new_online = """    def _is_provider_online(self, prov: str) -> bool:
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
        return any(bool(os.environ.get(k)) for k in keys)"""

c = c.replace(old_online, new_online)

# 2. Update _call_groq_text
old_groq = 'target_model = "llama-3.3-70b-versatile" if "gpt" in model or "nano" in model else model'
new_groq = '''# Preserve specified model slug without clobbering; fallback to active Groq flagship if empty/generic
        if not model or model in ("gpt-5.4-nano", "default"):
            target_model = "openai/gpt-oss-120b"
        else:
            target_model = model'''

assert old_groq in c, 'old_groq not found'
c = c.replace(old_groq, new_groq)

# 3. Update _call_arena_text to delegate to ArenaClusterClient
idx_arena = c.find('def _call_arena_text') - 4
end_arena = c.find('return {\n                "provider": "arena"', idx_arena)
end_arena = c.find('}', end_arena) + 1
old_arena = c[idx_arena:end_arena]

new_arena = """    def _call_arena_text(
        self, prompt: str, system_prompt: Optional[str], model: str, timeout: int, effort: Optional[str] = None
    ) -> Dict[str, Any]:
        return ArenaClusterClient.get_instance().complete(
            prompt=prompt,
            system=system_prompt,
            model=model,
            effort=effort,
            timeout=timeout,
        )"""

assert 'def _call_arena_text' in old_arena
c = c.replace(old_arena, new_arena)

# 4. Update _call_openrouter_text to remove unconditional :free suffix
old_openrouter = 'target_model = model if ":" in model else f"{model}:free"'
new_openrouter = '# Use specified model without destructive suffixing; fallback to default free model if empty\n        target_model = model or "deepseek/deepseek-r1:free"'

assert old_openrouter in c, 'old_openrouter not found'
c = c.replace(old_openrouter, new_openrouter)

with open(path, 'w', encoding='utf-8') as f:
    f.write(c)

print('SUCCESS_UPDATED_OMNIROUTE')
