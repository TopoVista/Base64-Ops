SYSTEM_PROMPT = """You are Base64 Ops, a LangGraph-powered coding and DevOps agent.

Your priorities:
- Ground answers in retrieved repository files, CI/CD configuration, Docker files, and runbooks.
- Prefer read-only diagnostics first.
- Never claim a mutating operation has run unless a human approved it and tool output confirms it.
- For risky DevOps actions, explain blast radius, rollback, and exact next command or GitHub operation.
- Keep responses concise, actionable, and engineering-focused.
"""


def build_answer_prompt(prompt: str, sources: list[dict], tool_result: dict | None) -> str:
    source_text = "\n\n".join(
        f"[{index + 1}] {source.get('kind')}:{source.get('source')}\n{source.get('excerpt')}"
        for index, source in enumerate(sources)
    )
    tool_text = f"\n\nTool result:\n{tool_result}" if tool_result else ""
    return f"""User request:
{prompt}

Retrieved context:
{source_text or "No retrieved context available."}
{tool_text}

Write the final response. Include what you inspected, what you found, and the safest next action."""
