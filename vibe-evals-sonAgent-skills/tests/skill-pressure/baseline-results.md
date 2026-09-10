# Baseline results without the new skills

## Observed failures

- Under one-file pressure, an agent proposed freezing and transferring complete model source and conversation snapshots. This is auditable but defeats the goal of avoiding full task-package transfer.
- Under a forced 0/1 format, an agent proposed using a conservative 0 for unverifiable conversation or dynamic behavior. This turns “unknown” into an unsupported failure.
- Under conflicting evidence, an agent proposed pre-filling 0 while simultaneously declaring the item pending adjudication. The number can be propagated downstream as if it were a verified fact.
- Several answers expected the human scoring loop to happen on the third-party machine, which does not meet the intended local-review workflow.

## Rules required in the new skills

- Transfer minimum sufficient excerpts, raw logs, hashes, and stable references; do not transfer the complete source tree by default.
- Represent insufficient evidence and policy ambiguity as `null`, never as a convenience 0.
- A pending adjudication prevents `ready_for_form`, even if a Markdown draft contains a provisional suggestion.
- Remote work ends at evidence and machine recommendations; human rulings and final V2.1 synthesis happen locally.
