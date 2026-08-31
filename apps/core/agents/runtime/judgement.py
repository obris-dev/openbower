"""What the ANSWERER decides about one run's answer, as a value the
answerer hands back: the model's confidence and reason per output
(including the ones the floor discarded), and whether any answered
field was dropped. Written by the output validator inside the
framework run, which is why a slot for it rides the dependency
channel (a validator receives only RunContext); read only by the
answerer's caller, never by a tool."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AnswerJudgement:
    # key -> the model's confidence and the reason it gave, for every
    # answered output INCLUDING the ones the floor discarded (those
    # also carry the value under `dropped`). The per-answer audit
    # trail, and the only place the rejected distribution exists.
    assessments: dict = field(default_factory=dict)
    # Whether verification dropped any answered field: an all-blank
    # row with drops diagnoses UNVERIFIED, never a bare no-evidence.
    verification_dropped: bool = False
