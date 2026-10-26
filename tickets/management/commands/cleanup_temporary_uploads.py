from django.core.management.base import BaseCommand

from tickets.application.services import TicketService


class Command(BaseCommand):
    help = "Delete unclaimed temporary uploads older than 24 hours."

    def handle(self, *args, **options):
        deleted_count = TicketService.cleanup_temporary_uploads()
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {deleted_count} expired temporary upload(s).")
        )
