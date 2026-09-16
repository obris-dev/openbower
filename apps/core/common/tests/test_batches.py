"""The paged-write iterator: pages in id order, ends on empty, and
refuses to spin on a caller that does not consume its page.

Run: DJANGO_ENV=test uv run python manage.py test common
"""

from __future__ import annotations

from django.test import TestCase

from openbower_kernel.batches import iter_id_pages
from webhooks.constants import DeliveryStatus, WebhookEnvelopeType
from webhooks.models import WebhookDelivery

ACCOUNT = "01AC" + "A" * 22


def _rows(n: int) -> list[str]:
    return [
        str(
            WebhookDelivery.objects.create(
                account_id=ACCOUNT,
                destination_id="01DS" + "A" * 22,
                type=WebhookEnvelopeType.PING,
                status=DeliveryStatus.OK,
            ).id
        )
        for _ in range(n)
    ]


class IterIdPagesTests(TestCase):
    def test_pages_in_id_order_until_the_queryset_is_empty(self):
        ids = _rows(5)
        seen = []
        for page in iter_id_pages(WebhookDelivery.objects.all(), batch=2):
            seen.append(page)
            WebhookDelivery.objects.filter(id__in=page).delete()
        self.assertEqual([len(page) for page in seen], [2, 2, 1])
        self.assertEqual([pk for page in seen for pk in page], sorted(ids))
        self.assertEqual(WebhookDelivery.objects.count(), 0)

    def test_an_empty_queryset_yields_nothing(self):
        self.assertEqual(list(iter_id_pages(WebhookDelivery.objects.none(), batch=2)), [])

    def test_an_unconsumed_page_raises_instead_of_spinning(self):
        _rows(3)
        pages = iter_id_pages(WebhookDelivery.objects.all(), batch=2)
        next(pages)
        with self.assertRaises(RuntimeError):
            next(pages)
