from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, username):
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})

    def create_user(
        self,
        email: str,
        password: str | None = None,
        *,
        first_name: str,
        last_name: str,
        **extra_fields,
    ) -> User:
        if not email:
            raise ValueError("The email address is required.")
        if not first_name.strip() or not last_name.strip():
            raise ValueError("First and last name are required.")

        user = self.model(
            email=self.normalize_email(email).lower(),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        email: str,
        password: str | None = None,
        **extra_fields,
    ) -> User:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", User.Role.ADMINISTRATOR)
        extra_fields.setdefault("first_name", "Root")
        extra_fields.setdefault("last_name", "User")
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        REQUESTER = "requester", _("Requester")
        SUPPORT_AGENT = "support_agent", _("Support Agent")
        ADMINISTRATOR = "administrator", _("Administrator")

    class Language(models.TextChoices):
        ENGLISH = "en", _("English")
        GERMAN = "de", _("German")

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    language = models.CharField(
        max_length=2,
        choices=Language.choices,
        default=Language.ENGLISH,
    )
    avatar = models.FileField(
        upload_to="avatars/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png"])],
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.REQUESTER,
    )
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_env_root = models.BooleanField(default=False, editable=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        ordering = ["email"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_env_root"],
                condition=Q(is_env_root=True),
                name="single_environment_root_user",
            )
        ]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        self.email = type(self).objects.normalize_email(self.email).lower()
        return super().save(*args, **kwargs)

    @property
    def is_support(self) -> bool:
        return self.role in {self.Role.SUPPORT_AGENT, self.Role.ADMINISTRATOR}

    @property
    def is_administrator(self) -> bool:
        return self.role == self.Role.ADMINISTRATOR


class UserInvitation(models.Model):
    email = models.EmailField(unique=True)
    role = models.CharField(
        max_length=20,
        choices=User.Role.choices,
        default=User.Role.REQUESTER,
    )
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="sent_user_invitations",
    )
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.email

    @property
    def is_pending(self) -> bool:
        from django.utils import timezone

        return (
            self.revoked_at is None
            and self.accepted_at is None
            and self.expires_at > timezone.now()
        )
