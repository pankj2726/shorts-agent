import sys
import tempfile
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent.modules.knowledge import validate_knowledge_file, parse_knowledge_file, _is_null_or_dummy, _normalize_string_list

print("=== Checking Edge Case Dict Citation Bypass ===")
with tempfile.TemporaryDirectory() as td:
    for s_val in ['{"": ""}', "{'': ''}", '{"k": null}', "{'k': None}", "{'a': {}}"]:
        p = pathlib.Path(td) / "bypass_probe.md"
        content = f"""---
id: kb-bypass-probe
title: Probe
category: youtube
verification_status: VERIFIED
confidence: 0.95
sources: '{s_val}'
tags: [test]
---
Body content"""
        p.write_text(content, encoding="utf-8")
        is_valid, errs = validate_knowledge_file(p)
        entry = parse_knowledge_file(p)
        print(f"sources: {s_val!r}")
        print("  is_valid:", is_valid)
        print("  parsed sources:", entry.metadata.sources)
