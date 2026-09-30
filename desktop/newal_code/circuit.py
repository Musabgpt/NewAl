"""The circuit breaker: stops an agent turn that is going nowhere, instead of spending a small model's steps (up to a
minute each on a phone) on the same mistake.

    CLOSED --(a call fails)--> counting that exact call (tool + arguments, since the last file change)
       ^                          |
       |                          | the same call fails a 2nd time: HALF-OPEN - its result says so and asks for a
       |                          |   different approach
       |  a call succeeds,        | the same call fails a 3rd time: OPEN - the turn stops and says why
       |  or a file changes       v
       +----------------------- OPEN --> "Stopped: bash failed 3 times the same way (exit 1: ...)"

    The step budget opens it too: 25 steps for a local or small model (OmniCode's limit for weak devices), 60 for
    an API model, both changeable (max_steps in the settings, "steps" in a sub-agent). A call that succeeds but is
    made again with nothing changed in between gets a note from the 3rd time (its result will not change).

A file change starts a new count: the same command after an edit is a new try, not a repeat."""

import json

CLOSED, HALF_OPEN, OPEN = "closed", "half-open", "open"
LOCAL_STEPS, API_STEPS = 25, 60


def step_budget(cfg, agent_def=None, local=False):
    """How many steps a turn may take: a sub-agent's own, the settings', else 25 local / 60 API."""
    return int((agent_def or {}).get("steps") or (cfg or {}).get("max_steps") or (LOCAL_STEPS if local else API_STEPS))


class Breaker:
    def __init__(self, same_failures=3, same_calls=3):
        self.same_failures = same_failures
        self.same_calls = same_calls
        self.state = CLOSED
        self.reason = ""
        self.failures = {}
        self.calls = {}

    @staticmethod
    def key(change, name, args):
        """One call: its tool and arguments, since the change `change` (a step number)."""
        return "%s:%s%s" % (change, name, json.dumps(args, sort_keys=True, default=str))

    def record(self, key, failed, name="", error=""):
        """Counts a call's outcome; returns a note for its result ("" when none) and may open the breaker."""
        self.calls[key] = self.calls.get(key, 0) + 1
        if not failed:
            if self.state == HALF_OPEN:
                self.state = CLOSED
            if self.calls[key] >= self.same_calls:
                return ("\n(Note: you already made this exact call %d times; the result will not change.)"
                        % (self.calls[key] - 1))
            return ""
        n = self.failures[key] = self.failures.get(key, 0) + 1
        if n >= self.same_failures:
            self.state = OPEN
            self.reason = "%s failed %d times the same way (%s)" % (name or "a tool", n, (error or "").strip()[:200])
            return "\n(Stopping: this exact call failed %d times.)" % n
        if n == self.same_failures - 1:
            self.state = HALF_OPEN
            return "\n(You made this exact call before and it failed the same way: change it, try another approach.)"
        return ""

    def over_budget(self, step, budget):
        if step >= budget and self.state != OPEN:
            self.state = OPEN
            self.reason = "the step budget (%d) is spent" % budget
        return self.state == OPEN

    @property
    def open(self):
        return self.state == OPEN
