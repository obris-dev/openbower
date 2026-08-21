from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import UserScopedModel

from ..constants import STATUS_MAX_LENGTH, TEST_RUN_ERROR_MAX_LENGTH, TestRunStatus


class AgentTestRun(UserScopedModel):
    """One test-bench execution, POLLED rather than awaited: the bench
    must not hold a connection open for the seconds a run takes. The
    row is throwaway diagnostics (purged opportunistically), never a
    fill: phase 5's jobs are the durable machinery."""

    # Bare CharField (see Agent.provider): the enum lives in constants
    # and the service writes it; choices= buys nothing at the DB.
    status = models.CharField(_("status"), max_length=STATUS_MAX_LENGTH, default=TestRunStatus.PENDING)
    error = models.CharField(_("error"), max_length=TEST_RUN_ERROR_MAX_LENGTH, blank=True, default="")
    # Stamped by the poll GET: the abandonment signal (silence here
    # means the loop that started the run is gone).
    polled_at = models.DateTimeField(_("polled at"), null=True, blank=True)
    result = models.JSONField(_("result"), default=dict, help_text=_("{cells, evidence, searches} when complete"))

    class Meta:
        verbose_name = _("agent test run")
        verbose_name_plural = _("agent test runs")
        indexes = [models.Index(fields=["account_id", "id"], name="agenttestrun_account_idx")]

    def __str__(self) -> str:
        return f"{self.id} ({self.status})"
