from django.apps import AppConfig


class TicketsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tickets"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        import tickets.signals  # noqa: F401

        post_migrate.connect(
            ensure_ticket_defaults,
            sender=self,
            dispatch_uid="tickets.ensure_ticket_defaults",
        )


def ensure_ticket_defaults(**kwargs) -> None:
    from django.db import OperationalError, ProgrammingError

    from tickets.models import Category, TicketSequence

    try:
        Category.objects.get_or_create(name="General")
        TicketSequence.objects.get_or_create(pk=1)
    except (OperationalError, ProgrammingError):
        return
