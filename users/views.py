from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from users.application.services import UserService
from users.forms import (
    InvitationForm,
    InvitationSignupForm,
    LoginForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    ProfileForm,
    UserAdministrationForm,
)
from users.models import User, UserInvitation


def administrator_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_administrator:
            raise Http404("Page not found.")
        return view(request, *args, **kwargs)

    return wrapped


def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    next_url = request.GET.get("next") or request.POST.get("next") or ""
    form = LoginForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        auth_login(request, form.user)
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("home")
    return render(request, "users/login.html", {"form": form, "next": next_url})


@login_required
def logout_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    auth_logout(request)
    return redirect("login")


@login_required
def profile_view(request):
    form = ProfileForm(
        request.POST or None, request.FILES or None, instance=request.user
    )
    if request.method == "POST" and form.is_valid():
        had_password = bool(form.cleaned_data.get("new_password"))
        user = UserService().update_profile(request.user, form.cleaned_data.copy())
        if had_password:
            update_session_auth_hash(request, user)
        messages.success(request, _("Profile updated."))
        response = redirect("profile")
        response.set_cookie("django_language", user.language, max_age=31536000)
        return response
    return render(request, "users/profile.html", {"form": form})


@login_required
def avatar_file(request, user_id: int):
    user = get_object_or_404(User, pk=user_id)
    if not user.avatar:
        raise Http404("Avatar not found.")
    response = FileResponse(
        user.avatar.open("rb"),
        content_type="image/png" if user.avatar.name.endswith(".png") else "image/jpeg",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


def password_reset_request(request):
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        UserService().request_password_reset(form.cleaned_data["email"])
        messages.success(
            request,
            _("If that active account exists, a reset link has been sent."),
        )
        return redirect("login")
    return render(request, "users/password_reset_request.html", {"form": form})


def password_reset_confirm(request, uid: str, token: str):
    service = UserService()
    user = service.password_reset_user(uid, token)
    if user is None:
        return render(request, "users/password_reset_invalid.html", status=400)
    form = PasswordResetConfirmForm(request.POST or None, user=user)
    if request.method == "POST" and form.is_valid():
        service.set_reset_password(user, form.cleaned_data["password"])
        messages.success(request, _("Password reset. You can now sign in."))
        return redirect("login")
    return render(request, "users/password_reset_confirm.html", {"form": form})


def invitation_accept(request, token: str):
    service = UserService()
    invitation = service.get_invitation(token)
    if invitation is None:
        return render(request, "users/invitation_invalid.html", status=400)
    existing_user = User.objects.filter(email__iexact=invitation.email).first()
    if existing_user:
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if request.user.pk != existing_user.pk:
            return render(request, "users/invitation_invalid.html", status=403)
        if request.method == "POST":
            try:
                service.accept_existing(invitation, request.user)
            except ValidationError:
                return render(request, "users/invitation_invalid.html", status=400)
            messages.success(request, _("Invitation accepted."))
            return redirect("home")
        return render(
            request,
            "users/invitation_existing_confirm.html",
            {"invitation": invitation},
        )
    if request.user.is_authenticated:
        auth_logout(request)
    form = InvitationSignupForm(
        request.POST or None,
        initial={"email": invitation.email},
    )
    if request.method == "POST" and form.is_valid():
        try:
            user = service.signup(invitation, form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            auth_login(request, user)
            messages.success(request, _("Account created. Welcome."))
            return redirect("home")
    return render(
        request,
        "users/invitation_accept.html",
        {"form": form, "invitation": invitation},
    )


@administrator_required
def administration_users(request):
    service = UserService()
    invitation_form = InvitationForm(request.POST or None)
    if request.method == "POST" and invitation_form.is_valid():
        try:
            service.invite(request.user, **invitation_form.cleaned_data)
        except ValidationError as exc:
            invitation_form.add_error(None, exc)
        else:
            messages.success(request, _("Invitation sent."))
            return redirect("administration-users")
    search = request.GET.get("q", "").strip()
    users = User.objects.all()
    if search:
        users = users.filter(
            Q(email__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
        )
    return render(
        request,
        "users/administration_users.html",
        {
            "users": users,
            "pending_invitations": UserInvitation.objects.filter(
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
            ),
            "invitation_form": invitation_form,
            "search": search,
        },
    )


@administrator_required
def administration_user_update(request, user_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User, pk=user_id)
    form = UserAdministrationForm(request.POST, instance=target)
    if not form.is_valid():
        messages.error(request, _("User update was invalid."))
        return redirect("administration-users")
    try:
        UserService().update_user_access(
            request.user,
            target_id=target.pk,
            role=form.cleaned_data["role"],
            is_active=form.cleaned_data["is_active"],
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("administration-users")
    messages.success(request, _("Updated %(email)s.") % {"email": target.email})
    return redirect("administration-users")


@administrator_required
def administration_invitation_action(request, invitation_id: int, action: str):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    invitation = get_object_or_404(UserInvitation, pk=invitation_id)
    service = UserService()
    try:
        if action == "resend":
            service.resend(request.user, invitation)
            messages.success(request, _("Invitation resent with a new link."))
        elif action == "revoke":
            service.revoke(invitation)
            messages.success(request, _("Invitation revoked."))
        else:
            raise Http404("Action not found.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect(reverse("administration-users"))
