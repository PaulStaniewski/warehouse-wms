from datetime import time
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from operations.models import DeliveryRoute, Order, PickingTask, RouteRoundSchedule, RouteRun, Shipment, ShipmentLine
from operations.operational_import import ExternalShipmentInput, ExternalShipmentLineInput, upsert_external_shipment
from operations.operational_projections import route_run_workload_projection, shipment_line_progress
from operations.serializers import RouteRunSerializer, ShipmentSerializer
from warehouse.models import Branch, InventoryItem, Location, Product


class OperationalDataSpineTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="SPN", name="Spine")
        self.location = Location.objects.create(
            branch=self.branch,
            code="SPN-PICK",
            name="Spine Picking",
            location_type=Location.LocationType.PICKING,
        )
        self.product = Product.objects.create(sku="SPN-001", name="Spine Product", barcode="SPN-001")
        InventoryItem.objects.create(
            branch=self.branch,
            location=self.location,
            product=self.product,
            quantity_on_hand=Decimal("20"),
        )
        self.route = DeliveryRoute.objects.create(branch=self.branch, code="SPN-R", name="Spine Route")
        now = timezone.localtime()
        RouteRoundSchedule.objects.create(
            route=self.route,
            weekday=now.weekday(),
            round_number=1,
            cutoff_time=(now + timezone.timedelta(hours=1)).time().replace(microsecond=0),
            departure_time=(now + timezone.timedelta(hours=2)).time().replace(microsecond=0),
            dispatch_wave="SPN",
        )
        self.payload = ExternalShipmentInput(
            source_system="TEST",
            external_order_reference="SPN-ORDER-001",
            external_shipment_reference="SPN-EXT-001",
            shipment_reference="SPN-SHIP-001",
            branch_code=self.branch.code,
            route_code=self.route.code,
            customer_name="Spine Customer",
            external_created_at=timezone.now(),
            lines=(
                ExternalShipmentLineInput(
                    external_line_reference="SPN-LINE-1",
                    line_number=1,
                    product_sku=self.product.sku,
                    quantity=Decimal("5"),
                ),
            ),
        )

    def test_repeated_external_import_reuses_the_whole_operational_graph(self):
        first, first_created = upsert_external_shipment(self.payload)
        second, second_created = upsert_external_shipment(self.payload)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Shipment.objects.count(), 1)
        self.assertEqual(ShipmentLine.objects.count(), 1)
        self.assertEqual(PickingTask.objects.count(), 1)
        self.assertEqual(RouteRun.objects.count(), 1)

    def test_shipments_and_route_monitor_share_line_state_and_quantities(self):
        shipment, _ = upsert_external_shipment(self.payload)
        line = shipment.lines.select_related("order_line").prefetch_related("order_line__picking_tasks").get()
        task = line.order_line.picking_tasks.get()
        task.quantity_picked = Decimal("2")
        task.status = PickingTask.Status.IN_PROGRESS
        task.save(update_fields=["quantity_picked", "status", "updated_at"])
        line = ShipmentLine.objects.select_related("shipment", "order_line").prefetch_related("order_line__picking_tasks").get(pk=line.pk)
        progress = shipment_line_progress(line)
        route_run = RouteRun.objects.prefetch_related("shipments__lines__order_line__picking_tasks").get(pk=shipment.route_run_id)
        workload = route_run_workload_projection(route_run)
        shipment_data = ShipmentSerializer(Shipment.objects.prefetch_related("lines__order_line__picking_tasks").get(pk=shipment.pk)).data
        route_data = RouteRunSerializer(route_run).data

        self.assertEqual(progress.state, "started")
        self.assertEqual(progress.remaining_to_pick, Decimal("3"))
        self.assertEqual(workload.started, 1)
        self.assertEqual(shipment_data["lines"][0]["operational_line_state"], "started")
        self.assertEqual(shipment_data["picked_quantity"], "2.000")
        self.assertEqual(route_data["started_lines_count"], 1)
        self.assertEqual(route_data["picked_line_bucket_count"], 0)

    def test_consistency_checker_reports_clean_imported_graph(self):
        upsert_external_shipment(self.payload)
        output = StringIO()
        call_command("check_operational_consistency", branch=self.branch.code, fail_on_error=True, stdout=output)
        self.assertIn("0 error(s)", output.getvalue())

