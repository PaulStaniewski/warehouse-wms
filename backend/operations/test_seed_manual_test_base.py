from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.models import UserBranchMembership
from operations.models import (
    AuditLog,
    BranchDispatchPolicy,
    CartPickedItem,
    DeliveryRoute,
    ExternalReturnDocument,
    Order,
    OrderLine,
    PickingShortage,
    PickingTask,
    ReturnBatch,
    RouteRoundSchedule,
    RouteRun,
    RouteRunOverrideHistory,
    SalesCorrection,
    ScannerCart,
    ScannerSession,
    Shipment,
    ShipmentLine,
    TransferDiscrepancy,
)
from warehouse.models import Branch, InventoryItem, Location, Product


class SeedManualTestBaseCommandTests(TestCase):
    def run_seed(self):
        output = StringIO()
        call_command("seed_manual_test_base", stdout=output)
        return output.getvalue()

    def test_seeds_only_idempotent_manual_test_master_data(self):
        first_output = self.run_seed()
        first_counts = self.master_counts()
        Branch.objects.filter(code="GDA").update(name="Legacy GDA name", city="Legacy GDA city")
        second_output = self.run_seed()

        self.assertEqual(self.master_counts(), first_counts)
        self.assertEqual(Branch.objects.filter(code__in=["GDA", "GDY"]).count(), 2)
        gda = Branch.objects.get(code="GDA")
        self.assertEqual(gda.name, "Gdansk")
        self.assertEqual(gda.city, "Gdansk")
        self.assertEqual(UserBranchMembership.objects.count(), 6)
        self.assertEqual(Product.objects.count(), 4)
        self.assertEqual(ScannerCart.objects.filter(status=ScannerCart.Status.AVAILABLE).count(), 3)
        self.assertEqual(DeliveryRoute.objects.count(), 13)
        self.assertEqual(RouteRoundSchedule.objects.count(), 98)
        self.assertEqual(BranchDispatchPolicy.objects.count(), 2)
        self.assert_no_operational_data()

        for output in (first_output, second_output):
            self.assertIn("Manual test master data seeded successfully.", output)
            self.assertIn("Demo printer: ZEBRA-01", output)
            self.assertIn("Operational Orders: 0", output)
            self.assertIn("Shipments: 0", output)
            self.assertIn("RouteRuns: 0", output)
            self.assertIn("PickingTasks: 0", output)
            self.assertIn("Active Scanner Sessions: 0", output)

    def master_counts(self):
        return (
            Branch.objects.count(),
            Product.objects.count(),
            Location.objects.count(),
            InventoryItem.objects.count(),
            ScannerCart.objects.count(),
            DeliveryRoute.objects.count(),
            RouteRoundSchedule.objects.count(),
            BranchDispatchPolicy.objects.count(),
            UserBranchMembership.objects.count(),
        )

    def assert_no_operational_data(self):
        for model in (
            Order,
            OrderLine,
            Shipment,
            ShipmentLine,
            RouteRun,
            PickingTask,
            ScannerSession,
            CartPickedItem,
            PickingShortage,
            ReturnBatch,
            ExternalReturnDocument,
            TransferDiscrepancy,
            SalesCorrection,
            RouteRunOverrideHistory,
            AuditLog,
        ):
            self.assertEqual(model.objects.count(), 0, model.__name__)
