"""The operator command's library (scripts/lib/bower-admin.sh) carries two copies of
facts owned elsewhere, pinned here so a drift fails CI instead of
misrouting a setup state or leaving a worker on stale config: the probe
exit codes (owner: management/probe.py) and the long-running services
built from the core image (owner: docker-compose.yml).

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

from agents.management.probe import ProbeExit

_REPO = Path(__file__).resolve().parents[4]
_SCRIPT = _REPO / "scripts" / "lib" / "bower-admin.sh"
_COMPOSE = _REPO / "docker-compose.yml"


class BowerAdminParityTests(SimpleTestCase):
    def test_the_shell_exit_codes_are_the_enum(self):
        script = _SCRIPT.read_text(encoding="utf-8")
        line = next(line for line in script.splitlines() if line.startswith("PROBE_OK="))
        shell = {name: int(value) for name, value in re.findall(r"PROBE_([A-Z_]+)=(\d+)", line)}
        self.assertEqual(shell, {member.name: member.value for member in ProbeExit})

    def test_the_restart_list_is_the_compose_file_s_long_running_core_services(self):
        script = _SCRIPT.read_text(encoding="utf-8")
        listed = set(re.search(r'^CORE_SERVICES="([^"]+)"', script, re.M).group(1).split())
        compose = _COMPOSE.read_text(encoding="utf-8")
        services = compose.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        image: dict[str, str] = {}
        current = ""
        for text in services.splitlines():
            if m := re.match(r"^  ([a-z-]+):\s*$", text):
                current = m.group(1)
            elif m := re.match(r"^    image:\s*(.+)$", text):
                image[current] = m.group(1)
        core = {name for name, value in image.items() if "core_image" in value}
        # A one-shot is what another service waits on to COMPLETE; it
        # has no config to reload.
        one_shots = set(
            re.findall(r"^      ([a-z-]+):\n        condition: service_completed_successfully", services, re.M)
        )
        self.assertTrue(core and one_shots, (core, one_shots))
        self.assertEqual(listed, core - one_shots)
