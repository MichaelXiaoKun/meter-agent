"""
main.py — Conversational CLI for the bluebot orchestrator agent.

Starts an interactive chat session. Each message you send is processed by the
orchestrator, which decides which sub-agents to call and synthesises a reply.
Conversations are persisted to SQLite and can be resumed across sessions.

Usage:
    python main.py                    # start a new conversation
    python main.py --resume <id>      # continue a saved conversation
    python main.py --list             # list saved conversations

Bearer token:
    Set the BLUEBOT_TOKEN environment variable, or pass --token explicitly.
"""

import argparse
import os
import sys
import time

from agent import run_turn
import store
from summarizer import update_title

# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

_TOOL_LABELS = {
    "resolve_time_range": "Resolving time range",
    "check_meter_status": "Checking meter status",
    "analyze_flow_data":  "Analysing flow data",
    "configure_meter_pipe": "Configuring meter pipe",
    "set_transducer_angle_only": "Setting transducer angle (SSA only)",
    "sweep_transducer_angles": "Sweeping transducer angles",
}


def _on_event(event: dict) -> None:
    """Print a one-line status update for each processing step."""
    kind = event["type"]

    if kind == "token_usage":
        pct   = event["pct"]
        filled = int(pct * 20)
        bar   = "█" * filled + "░" * (20 - filled)
        print(f"  [context: {bar} {pct:.0%} of 200k tokens ({event['tokens']:,})]", file=sys.stderr)

    elif kind == "compressing":
        print(f"  [⚠ compressing history — context at {event['pct']:.0%}, summarizing older messages...]", file=sys.stderr)

    elif kind == "thinking":
        print("  [thinking...]", file=sys.stderr)

    elif kind == "tool_call":
        tool = event["tool"]
        inp  = event["input"]
        label = _TOOL_LABELS.get(tool, tool)

        if tool == "resolve_time_range":
            detail = f"'{inp.get('description', '')}'"
        elif tool in ("check_meter_status", "analyze_flow_data"):
            detail = inp.get("serial_number", "")
            if tool == "analyze_flow_data":
                detail += f"  {inp.get('start')} → {inp.get('end')}"
        elif tool == "configure_meter_pipe":
            detail = inp.get("serial_number", "")
        elif tool in ("set_transducer_angle_only", "sweep_transducer_angles"):
            detail = inp.get("serial_number", "")
        else:
            detail = ""

        print(f"  [{label}{': ' + detail if detail else ''}...]", file=sys.stderr)

    elif kind == "tool_progress":
        print(f"  [{event.get('message', event.get('tool', 'progress'))}]", file=sys.stderr)

    elif kind == "tool_result":
        status = "done" if event["success"] else "failed"
        print(f"  [{event['tool']} {status}]", file=sys.stderr)


# ---------------------------------------------------------------------------
# Conversation listing
# ---------------------------------------------------------------------------

def _print_conversations(convs: list[dict]) -> None:
    if not convs:
        print("No saved conversations.")
        return
    print(f"\n{'ID':<10}  {'Turns':>5}  {'Updated':<17}  Title")
    print("-" * 65)
    for c in convs:
        updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(c["updated_at"]))
        title   = c["title"] or "(untitled)"
        print(f"{c['id']:<10}  {c['message_count']:>5}  {updated:<17}  {title}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="bluebot conversational assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--token", default=None,
        help="bluebot Bearer token (default: reads BLUEBOT_TOKEN env var)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List saved conversations and exit",
    )
    parser.add_argument(
        "--resume", metavar="ID",
        help="Resume a saved conversation by ID",
    )
    args = parser.parse_args()

    if args.list:
        _print_conversations(store.list_conversations(""))
        return

    token = args.token or os.environ.get("BLUEBOT_TOKEN")
    if not token:
        print(
            "Error: Bearer token required. Use --token or set BLUEBOT_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.resume:
        messages = store.load_messages(args.resume)
        if not messages:
            print(f"Error: No conversation found with ID '{args.resume}'.", file=sys.stderr)
            sys.exit(1)
        conversation_id = args.resume
        turns = sum(1 for m in messages if m["role"] == "user")
        print(f"bluebot Assistant  (resuming {conversation_id}, {turns} previous turns)")
    else:
        messages = []
        conversation_id = store.create_conversation("")
        print(f"bluebot Assistant  (new conversation {conversation_id})")

    print("Type your question and press Enter. Type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            print("Goodbye.")
            break

        checkpoint = len(messages)
        messages.append({"role": "user", "content": user_input})

        # Auto-title from the first user message.
        if checkpoint == 0:
            store.set_title(conversation_id, user_input[:60])

        try:
            reply, history_replaced = run_turn(messages, token, on_event=_on_event)
            if history_replaced:
                store.replace_conversation_messages(conversation_id, messages)
            else:
                store.append_messages(conversation_id, messages[checkpoint:])
            update_title(conversation_id, messages)
            print(f"\nAssistant: {reply}\n")
        except Exception as exc:
            print(f"\nError: {exc}\n", file=sys.stderr)
            # Roll back to the checkpoint so the conversation stays consistent.
            del messages[checkpoint:]


if __name__ == "__main__":
    main()
