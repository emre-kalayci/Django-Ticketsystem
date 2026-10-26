from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        from users.bootstrap import provision_root_user

        post_migrate.connect(
            provision_root_user,
            sender=self,
            dispatch_uid="users.provision_root_user",
        )
