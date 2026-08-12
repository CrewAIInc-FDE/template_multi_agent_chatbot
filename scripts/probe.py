#!/usr/bin/env python
"""Exercise every agent path locally: routing accuracy, latency, tool usage.

Runs a fixed set of prompts through the real flow, one turn each, and reports
where the time went and which tools fired. Answers three questions the unit
tests can't:

  1. Does the router send each phrasing to the agent you expect?
  2. How much of a turn is routing versus the agent doing work?
  3. Did the tools you think are wired up actually get called?

Usage:
    python scripts/probe.py                  # every enabled route
    python scripts/probe.py --route SLACK    # one route
    python scripts/probe.py --repeat 3       # latency stability
    python scripts/probe.py --list           # show cases, run nothing
    python scripts/probe.py --routing-only   # skip handlers: fast, cheap

Costs real API calls. --routing-only stops after the routing decision, so it
exercises the router (one small model call per case) without running crews.
"""

import argparse
import os
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# Prompt -> the route it should reach. Phrasings are deliberately varied: the
# router's real failure mode is an indirect ask, not a keyword-perfect one.
CASES: list[tuple[str, str]] = [
    ("hey", "converse"),
    ("thanks, that's helpful", "converse"),
    ("what can you actually do?", "converse"),
    ("draw me an otter on a skateboard", "IMAGE_CREATION_UPDATE"),
    ("make a picture of a neon city at night", "IMAGE_CREATION_UPDATE"),
    ("what's the latest news on retro emulation?", "INTERNET_SEARCH"),
    ("who won the last F1 race?", "INTERNET_SEARCH"),
    ("what did the team say about pricing in slack?", "SLACK"),
    ("summarise recent discussion in #crewai-opensource", "SLACK"),
    ("who is in the support channel?", "SLACK"),
    ("how do I create a crew with custom tools in CrewAI?", "CREWAI_DOCS"),
    ("explain how memory works in crewai", "CREWAI_DOCS"),
]


class Recorder:
    """Collects timings and tool calls for one turn off the CrewAI event bus."""

    def __init__(self):
        self.route: str | None = None
        self.route_at: float | None = None
        self.started = time.perf_counter()
        self.tools: list[tuple[str, float]] = []
        self.llm_calls = 0
        self.errors: list[str] = []

    def register(self, bus):
        from crewai.events.types.flow_events import ConversationRouteSelectedEvent
        from crewai.events.types.llm_events import LLMCallStartedEvent
        from crewai.events.types.tool_usage_events import (
            ToolUsageErrorEvent,
            ToolUsageFinishedEvent,
        )

        @bus.on(ConversationRouteSelectedEvent)
        def _route(source, event):
            # First decision only: a cyclic flow could emit more than one.
            if self.route is None:
                self.route = event.route
                self.route_at = time.perf_counter()

        @bus.on(ToolUsageFinishedEvent)
        def _tool(source, event):
            seconds = (event.finished_at - event.started_at).total_seconds()
            self.tools.append((event.tool_name, seconds))

        @bus.on(ToolUsageErrorEvent)
        def _tool_error(source, event):
            self.errors.append(f"tool {event.tool_name}: {getattr(event, 'error', '')}")

        @bus.on(LLMCallStartedEvent)
        def _llm(source, event):
            self.llm_calls += 1

    @property
    def routing_seconds(self) -> float | None:
        return None if self.route_at is None else self.route_at - self.started

    @property
    def total_seconds(self) -> float:
        return time.perf_counter() - self.started


def run_case(prompt: str, routing_only: bool) -> Recorder:
    from crewai.events.event_bus import crewai_event_bus

    from template_multi_agent_chatbot.main import ConversationalFlow

    recorder = Recorder()
    recorder.register(crewai_event_bus)

    flow = ConversationalFlow()
    if routing_only:
        # Hydrate a turn far enough to route, then stop before any handler runs.
        flow._pending_user_message = prompt
        flow._apply_pending_conversational_turn()
        recorder.route = flow.route_turn(flow.build_router_context())
        recorder.route_at = time.perf_counter()
        return recorder

    flow.kickoff(inputs={"id": f"probe-{uuid.uuid4().hex[:8]}", "user_message": prompt})
    if recorder.route is None:
        recorder.route = flow.state.last_intent
    return recorder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", help="only cases expecting this route")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case")
    parser.add_argument("--list", action="store_true", help="list cases and exit")
    parser.add_argument(
        "--routing-only",
        action="store_true",
        help="stop after the routing decision (fast, no crews)",
    )
    args = parser.parse_args()

    from template_multi_agent_chatbot.routing.router_config import ROUTES

    enabled = {"converse", *ROUTES}
    cases = [c for c in CASES if c[1] in enabled]
    skipped = [c for c in CASES if c[1] not in enabled]
    if args.route:
        cases = [c for c in cases if c[1] == args.route.upper() or c[1] == args.route]

    if args.list:
        for prompt, route in cases:
            print(f"  {route:22} {prompt}")
        for prompt, route in skipped:
            print(f"  {route:22} {prompt}   [route disabled]")
        return 0

    print(f"Enabled routes : {sorted(enabled)}")
    if skipped:
        disabled = sorted({route for _, route in skipped})
        print(f"Skipping       : {disabled} (credentials not configured)")
    print(f"Mode           : {'routing only' if args.routing_only else 'full turn'}")
    print(f"Cases          : {len(cases)} x {args.repeat}\n")

    header = f"{'ok':3} {'expected':22} {'actual':22} {'route':>7} {'total':>8}  tools"
    print(header)
    print("-" * len(header))

    failures: list[str] = []
    by_route: dict[str, list[float]] = defaultdict(list)
    routing_times: list[float] = []

    for prompt, expected in cases:
        for _ in range(args.repeat):
            try:
                rec = run_case(prompt, args.routing_only)
            except Exception as exc:  # a crashing route must not stop the sweep
                print(f"{'ERR':3} {expected:22} {type(exc).__name__:22} {'-':>7} {'-':>8}")
                failures.append(f"{prompt!r} raised {type(exc).__name__}: {exc}")
                continue

            ok = rec.route == expected
            route_s = rec.routing_seconds
            tools = ", ".join(f"{n}({s:.1f}s)" for n, s in rec.tools) or "-"
            print(
                f"{'✓' if ok else '✗':3} {expected:22} {str(rec.route):22} "
                f"{(f'{route_s:.2f}s' if route_s else '-'):>7} "
                f"{rec.total_seconds:>7.1f}s  {tools}"
            )

            if not ok:
                failures.append(f"{prompt!r} -> {rec.route} (expected {expected})")
            for err in rec.errors:
                failures.append(f"{prompt!r}: {err}")
            if route_s:
                routing_times.append(route_s)
            by_route[expected].append(rec.total_seconds)

    print()
    if routing_times:
        ordered = sorted(routing_times)
        print(
            f"Routing latency: median {ordered[len(ordered) // 2]:.2f}s  "
            f"min {ordered[0]:.2f}s  max {ordered[-1]:.2f}s"
        )
    if not args.routing_only:
        for route, times in sorted(by_route.items()):
            ordered = sorted(times)
            print(
                f"  {route:22} median {ordered[len(ordered) // 2]:>6.1f}s  "
                f"max {ordered[-1]:>6.1f}s"
            )

    total = sum(len(v) for v in by_route.values()) or 1
    print(f"\nRouting accuracy: {total - len(failures)}/{total}")
    for failure in failures:
        print(f"  ✗ {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    sys.exit(main())
