from django.urls import path

from users import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("settings/profile/", views.profile_view, name="profile"),
    path("avatars/<int:user_id>/", views.avatar_file, name="avatar-file"),
    path(
        "reset-password/", views.password_reset_request, name="password-reset-request"
    ),
    path(
        "reset-password/<str:uid>/<str:token>/",
        views.password_reset_confirm,
        name="password-reset-confirm",
    ),
    path("invite/<str:token>/", views.invitation_accept, name="invitation-accept"),
    path(
        "administration/users/",
        views.administration_users,
        name="administration-users",
    ),
    path(
        "administration/users/<int:user_id>/update/",
        views.administration_user_update,
        name="administration-user-update",
    ),
    path(
        "administration/invitations/<int:invitation_id>/<str:action>/",
        views.administration_invitation_action,
        name="administration-invitation-action",
    ),
]
