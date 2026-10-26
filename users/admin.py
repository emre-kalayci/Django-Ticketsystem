from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from users.models import User, UserInvitation


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User
    ordering = ["email"]
    list_display = [
        "email",
        "first_name",
        "last_name",
        "is_active",
        "role",
        "is_staff",
        "is_env_root",
    ]
    list_filter = ["role", "is_active", "is_staff", "is_superuser", "language"]
    search_fields = ["email", "first_name", "last_name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal info",
            {"fields": ("first_name", "last_name", "language", "avatar", "role")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "first_name",
                    "last_name",
                    "role",
                    "password1",
                    "password2",
                ),
            },
        ),
    )


@admin.register(UserInvitation)
class UserInvitationAdmin(admin.ModelAdmin):
    list_display = ["email", "role", "invited_by", "expires_at", "accepted_at"]
    list_filter = ["role", "revoked_at", "accepted_at"]
    search_fields = ["email", "invited_by__email"]
    readonly_fields = ["token_hash", "created_at", "accepted_at", "revoked_at"]
