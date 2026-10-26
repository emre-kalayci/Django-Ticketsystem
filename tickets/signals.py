from django.db.models.signals import post_delete
from django.dispatch import receiver

from tickets.models import TemporaryUpload, TicketAttachment


@receiver(post_delete, sender=TicketAttachment)
def delete_attachment_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)


@receiver(post_delete, sender=TemporaryUpload)
def delete_temporary_upload_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
