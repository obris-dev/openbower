"""Fill processing: the EXECUTE path. A claimed task runs its agent and
lands the result, the mirror of fill_admission's write gate.

One room per concern: `processor` the ProcessNodeRun base and its two
lanes (AutofillRun live, FillBackedRun frozen), `cell_run` the runtime
call, `landing` how a result lands on the sheet. cell_run and landing are
private to this pipeline (nothing else imports them); the state machines
they drive (node_runs, fill_progress) stay at the services top level
because admission and provisioning drive them too."""

from .processor import AutofillRun, FillBackedRun, ProcessNodeRun, to_result

__all__ = [
    "AutofillRun",
    "FillBackedRun",
    "ProcessNodeRun",
    "to_result",
]
