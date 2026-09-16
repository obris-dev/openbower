"""`/v1/webhooks`: the account's destinations, a synchronous test
delivery, and each destination's delivery log (session-authed like
lists). Views are thin dispatch: custody lives in the service, the
HTTP path in delivery/, and wire shapes construct through the contract
models at these boundaries."""

from __future__ import annotations

import logging
from functools import cached_property

from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from common.views import ScopedView
from openbower_kernel.pagination import next_cursor_from, parse_limit
from openbower_schema.webhooks import WebhookDestinationCreated, WebhookDestinationsList

from .constants import DEFAULT_DELIVERIES_PAGE, MAX_DELIVERIES_PAGE
from .models import WebhookDestination
from .serializers import (
    DestinationCreateRequest,
    DestinationPatchRequest,
    deliveries_page_wire,
    delivery_wire,
    destination_wire,
)
from .services import DestinationNotFound, WebhookDeliveryService, WebhookDestinationService, WebhookRefused

logger = logging.getLogger(__name__)


def _refused(e: WebhookRefused) -> Response:
    # Every refusal here is fixed by the caller changing the request
    # or the account, never by waiting: 400, with the machine code and
    # the server's copy.
    return Response({"error": e.code, "detail": str(e)}, status=400)


class _ScopedView(ScopedView):
    @cached_property
    def destinations(self) -> WebhookDestinationService:
        return WebhookDestinationService(account_id=self.request.user.account_id, user_id=self.request.user.id)

    @cached_property
    def deliveries(self) -> WebhookDeliveryService:
        return WebhookDeliveryService(account_id=self.request.user.account_id)

    def _destination_or_404(self, destination_id: str) -> WebhookDestination:
        try:
            return self.destinations.get(destination_id)
        except DestinationNotFound as e:
            raise NotFound("no destination with that id") from e

    def _wire(self, destination: WebhookDestination) -> dict:
        newest = self.deliveries.newest_for([str(destination.id)]).get(str(destination.id))
        header_names = self.destinations.header_names_of(destination)
        return destination_wire(destination, header_names=header_names, newest=newest)


class WebhooksView(_ScopedView):
    def get(self, request: Request) -> Response:
        rows = self.destinations.list_for()
        newest = self.deliveries.newest_for([str(row.id) for row in rows])
        items = [
            destination_wire(row, header_names=self.destinations.header_names_of(row), newest=newest.get(str(row.id)))
            for row in rows
        ]
        return Response(WebhookDestinationsList(items=items).model_dump())

    def post(self, request: Request) -> Response:
        serializer = DestinationCreateRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            destination, secret = self.destinations.create(
                label=data["label"], url=data["url"], headers=data["headers"]
            )
        except WebhookRefused as e:
            return _refused(e)
        created = WebhookDestinationCreated(destination=self._wire(destination), signing_secret=secret)
        return Response(created.model_dump(), status=201)


class WebhookDetailView(_ScopedView):
    def get(self, request: Request, id: str) -> Response:
        destination = self._destination_or_404(id)
        return Response(self._wire(destination))

    def patch(self, request: Request, id: str) -> Response:
        destination = self._destination_or_404(id)
        serializer = DestinationPatchRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            updated = self.destinations.patch(
                destination,
                label=data.get("label"),
                url=data.get("url"),
                enabled=data.get("enabled"),
                headers=data.get("headers"),
            )
        except WebhookRefused as e:
            return _refused(e)
        return Response(self._wire(updated))

    def delete(self, request: Request, id: str) -> Response:
        destination = self._destination_or_404(id)
        self.destinations.delete(destination)
        return Response(status=204)


class WebhookTestView(_ScopedView):
    """POST /v1/webhooks/{id}/test: one signed test delivery, sent now,
    on a disabled destination too (the test is an explicit gesture;
    `enabled` gates the automatic lane). Answers 200 with the delivery
    whatever the receiver did: a failed delivery is an API object, its
    `status` the outcome."""

    def post(self, request: Request, id: str) -> Response:
        destination = self._destination_or_404(id)
        delivery = self.destinations.test(destination)
        return Response(delivery_wire(delivery))


class WebhookDeliveriesView(_ScopedView):
    def get(self, request: Request, id: str) -> Response:
        destination = self._destination_or_404(id)
        limit = parse_limit(request, default=DEFAULT_DELIVERIES_PAGE, maximum=MAX_DELIVERIES_PAGE)
        after = request.query_params.get("after", "")
        rows = self.deliveries.page_for(str(destination.id), after_id=after, limit=limit)
        return Response(deliveries_page_wire(rows, next_cursor=next_cursor_from(rows, limit=limit)))
