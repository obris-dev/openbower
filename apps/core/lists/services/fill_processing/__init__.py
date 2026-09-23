"""Fill processing: the runtime call the agent kind's processor
(lists/processors/column_agent.py) makes, `cell_run`. How a result
lands on the sheet is the cell layer's (lists/cells: the kind's
writes through the list service's one landing). The state machines
(node_runs, fill_progress) stay at the services top level because
admission and provisioning drive them too."""
