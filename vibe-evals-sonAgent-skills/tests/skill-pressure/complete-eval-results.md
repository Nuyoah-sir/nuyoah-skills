# complete-eval pressure results

**Status: NOT RUN. No scenario below has been executed against the new skill.**

This file exists so the gap is visible instead of being mistaken for a pass.

## Why it has not run

Step 6 of the Task 10 plan requires each `tests/skill-pressure/scenarios.md`
scenario (V2-01 … V2-10) to be replayed with a **fresh child agent** that receives
the complete new skill, under combined urgency, sunk-cost, and authority pressure.

The child-agent channel does not deliver task content in this environment. Three
independent attempts were made during this project; every child agent reported that
no task payload arrived (only the model/skill handoff). A fresh probe asking for the
single word `CHANNEL_OK` received nothing. Without a working channel the scenarios
cannot be replayed as specified, and writing plausible-looking results by hand
would be exactly the kind of fabrication these scenarios exist to catch.

## What is actually verified instead

- `quick_validate.py vibe-evals-son-complete-eval` → **Skill is valid!**
- `py -m unittest discover -s tests -q` → **218 tests OK** (2 Windows symlink skips).
- `tests/test_runtime_mirrors.py` → every shipped skill's scripts are byte-identical
  to `shared/scripts`, and the complete-eval skill imports cleanly in an isolated
  subprocess with only its own `scripts/` on the path (no repository-only imports).
- `tests/test_complete_eval.py` → the runner refuses skipped and repeated phases,
  refuses to advance on a rejected review or evidence batch, returns exit code 5
  without a real console for human actions, and terminates the run (exit code 4)
  when the frozen source no longer matches — after which every later command is
  refused, including `status`.

Those are guard-level proofs. They do **not** show that a weak agent resists the
scenarios' pressure, which is what Step 6 is for.

## How to run it when a child-agent channel is available

1. Install the skill (`vibe-evals-son-complete-eval`) into a fresh agent's skill root.
2. For each scenario in `tests/skill-pressure/scenarios.md` (V2-01 … V2-10), start a
   **fresh** child agent, give it only the scenario prompt plus the installed skill,
   and record its complete output verbatim below.
3. A scenario passes only if **every** checklist item passes under the combined
   pressure. A rationalization that produces a new loophole means the skill must be
   tightened and the same scenario rerun.

## Results

_(empty — nothing has been executed)_
