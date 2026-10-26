from __future__ import annotations

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from users.application.services import validate_avatar
from users.models import User, UserInvitation


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.user = None

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        password = cleaned.get("password")
        if email and password:
            normalized = get_user_model().objects.normalize_email(email).lower()
            self.user = authenticate(
                self.request,
                email=normalized,
                password=password,
            )
            if self.user is None:
                raise ValidationError(_("Invalid email or password."))
        return cleaned


class ProfileForm(forms.ModelForm):
    new_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    remove_avatar = forms.BooleanField(required=False)

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "language", "avatar"]

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"]).lower()
        if (
            User.objects.filter(email__iexact=email)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise ValidationError(_("A user with this email already exists."))
        return email

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if (
            avatar
            and hasattr(avatar, "temporary_file_path")
            or (avatar and avatar != self.instance.avatar)
        ):
            validate_avatar(avatar)
        return avatar

    def clean_new_password(self):
        password = self.cleaned_data.get("new_password")
        if password:
            validate_password(password, user=self.instance)
        return password


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class PasswordResetConfirmForm(forms.Form):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        if password and password != cleaned.get("password_confirm"):
            self.add_error("password_confirm", _("Passwords do not match."))
        if password and self.user:
            validate_password(password, user=self.user)
        return cleaned


class InvitationForm(forms.Form):
    email = forms.EmailField()
    role = forms.ChoiceField(choices=User.Role.choices)

    def clean_email(self):
        return User.objects.normalize_email(self.cleaned_data["email"]).lower()


class InvitationSignupForm(forms.Form):
    email = forms.EmailField(disabled=True)
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        if password and password != cleaned.get("password_confirm"):
            self.add_error("password_confirm", _("Passwords do not match."))
        if password:
            candidate = User(
                email=cleaned.get("email", ""),
                first_name=cleaned.get("first_name", ""),
                last_name=cleaned.get("last_name", ""),
            )
            validate_password(password, user=candidate)
        return cleaned


class UserAdministrationForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["role", "is_active"]


class InvitationActionForm(forms.Form):
    invitation_id = forms.ModelChoiceField(queryset=UserInvitation.objects.all())
    action = forms.ChoiceField(choices=[("resend", "Resend"), ("revoke", "Revoke")])
