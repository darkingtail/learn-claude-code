#!/usr/bin/env python3
"""
s01_agent_loop.py - The Agent Loop

The entire secret of an AI coding agent in one pattern:

    while stop_reason == "tool_use":
        response = LLM(messages, tools)
        execute tools
        append results

    +----------+      +-------+      +---------+
    |   User   | ---> |  LLM  | ---> |  Tool   |
    |  prompt  |      |       |      | execute |
    +----------+      +---+---+      +----+----+
                          ^               |
                          |   tool_result |
                          +---------------+
                          (loop continues)

This is the core loop: feed tool results back to the model
until the model decides to stop. Production agents layer
policy, hooks, and lifecycle controls on top.
"""

import os
import subprocess

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks. Act, don't explain."

TOOLS = [{
    "name": "bash",
    "description": "Run a shell command.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}]


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


# -- The core pattern: a while loop that calls tools until the model stops --
def agent_loop(messages: list):
    loop_count = 0
    while True:
        loop_count += 1
        print(f"\n\033[35m{'='*50}")
        print(f"  Loop #{loop_count}")
        print(f"{'='*50}\033[0m")

        # Print what we're sending to LLM
        print(f"\033[34m[INPUT] system: {SYSTEM}\033[0m")
        print(f"\033[34m[INPUT] tools: {[t['name'] for t in TOOLS]}\033[0m")
        print(f"\033[34m[INPUT] messages ({len(messages)} items):\033[0m")
        for i, msg in enumerate(messages):
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                preview = content[:150]
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "tool_result":
                            parts.append(f'tool_result({item["content"][:80]}...)')
                    elif hasattr(item, "text"):
                        parts.append(f'text({item.text[:80]}...)')
                    elif hasattr(item, "type") and item.type == "tool_use":
                        parts.append(f'tool_use({item.name}: {str(item.input)[:80]})')
                preview = " | ".join(parts)
            else:
                preview = str(content)[:150]
            print(f"\033[34m  [{i}] {role}: {preview}\033[0m")

        print(f"\033[34m--- Calling LLM... ---\033[0m")
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # Print raw response from LLM
        print(f"\033[32m[RAW RESPONSE]\033[0m")
        print(f"\033[32m{response}\033[0m")

        # If the model didn't call a tool, we're done
        if response.stop_reason != "tool_use":
            print(f"\n\033[35m>>> Loop ended after {loop_count} round(s)\033[0m")
            return
        # Execute each tool call, collect results
        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = run_bash(block.input["command"])
                print(f"\033[36m[tool_result] {output[:300]}\033[0m")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output})
        messages.append({"role": "user", "content": results})

        # Wait for user to press 1 before next loop
        step = input("\n\033[31m>>> Press 1 to continue next loop: \033[0m")
        if step.strip() != "1":
            print("Aborted.")
            return


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()
