"""Fill processing: the EXECUTE path. A claimed task runs its agent and
lands the result, the mirror of fill_admission's write gate.

One room per concern: `processor` the ProcessFillTask base and its two
lanes (AutofillTask live, FillBackedTask frozen), `cell_run` the runtime
call, `landing` how a result lands on the sheet. cell_run and landing are
private to this pipeline (nothing else imports them); the state machines
they drive (fill_tasks, fill_progress) stay at the services top level
because admission and provisioning drive them too."""

from .processor import AutofillTask, FillBackedTask, ProcessFillTask, to_result

__all__ = [
    "AutofillTask",
    "FillBackedTask",
    "ProcessFillTask",
    "to_result",
]
