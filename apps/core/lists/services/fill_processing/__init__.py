"""Fill processing: the EXECUTE path's two rooms the agent kind's
processor (lists/processors/column_agent.py) runs through, the mirror
of fill_admission's write gate.

`cell_run` is the runtime call, `landing` how a result lands on the
sheet. The state machines they drive (node_runs, fill_progress) stay at
the services top level because admission and provisioning drive them
too."""
