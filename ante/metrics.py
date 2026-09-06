"""The six published hackathon metrics, collected by both graphs.

One counter object per run. Print it at the end of everything -- retrofitting
measurement the night before submission is how teams lose the technical mark.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class Metrics:
    schema_attempts: int = 0
    schema_passes: int = 0
    tool_calls: int = 0
    tool_successes: int = 0
    tasks_attempted: int = 0
    tasks_completed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    turns_used: int = 0
    turn_limit: int = 0
    claims: int = 0
    claims_cited: int = 0

    def tool(self, result) -> None:
        """Record one tool call. A returned {"error": ...} is a failure."""
        self.tool_calls += 1
        if not (isinstance(result, dict) and "error" in result):
            self.tool_successes += 1

    def schema(self, ok: bool) -> None:
        self.schema_attempts += 1
        self.schema_passes += int(ok)

    def usage(self, meta) -> None:
        """Accumulate token usage from a LangChain response's usage_metadata."""
        if not meta:
            return
        self.input_tokens += meta.get("input_tokens", 0) or 0
        self.output_tokens += meta.get("output_tokens", 0) or 0

    @staticmethod
    def _pct(num: int, den: int) -> str:
        return f"{100.0 * num / den:.0f}%  ({num}/{den})" if den else "n/a  (0/0)"

    def report(self) -> str:
        rows = [
            ("Schema Validation Pass Rate", self._pct(self.schema_passes, self.schema_attempts)),
            ("Tool-Call Success Rate", self._pct(self.tool_successes, self.tool_calls)),
            ("Task Completion Rate", self._pct(self.tasks_completed, self.tasks_attempted)),
            ("Token Cost Per Run", f"{self.input_tokens} in / {self.output_tokens} out"),
            ("Loop Discipline", f"{self.turns_used}/{self.turn_limit} turns"
                                if self.turn_limit else f"{self.turns_used} turns"),
            ("Answer Fidelity", self._pct(self.claims_cited, self.claims)),
        ]
        width = max(len(r[0]) for r in rows)
        body = "\n".join(f"  {name:<{width}}   {value}" for name, value in rows)
        return f"metrics\n{body}"
