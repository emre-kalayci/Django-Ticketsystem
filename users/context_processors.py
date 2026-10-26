from django.urls import reverse
from django.utils.translation import gettext as _


def support_portal_context(request):
    if not request.user.is_authenticated:
        return {}
    user = request.user
    navigation = []
    if user.is_support:
        navigation.extend(
            [
                {"label": _("All tickets"), "url": reverse("ticket-list")},
                {
                    "label": _("Assigned to me"),
                    "url": f"{reverse('ticket-list')}?queue=mine",
                },
            ]
        )
    else:
        navigation.append({"label": _("My tickets"), "url": reverse("ticket-list")})
    navigation.append({"label": _("New ticket"), "url": reverse("ticket-create")})
    if user.is_administrator:
        navigation.extend(
            [
                {
                    "label": _("Administration"),
                    "url": reverse("administration-users"),
                },
                {
                    "label": _("Categories"),
                    "url": reverse("administration-categories"),
                },
            ]
        )
    current_path = request.path
    current_queue = request.GET.get("queue", "all")
    for item in navigation:
        item_path = item["url"].split("?", maxsplit=1)[0]
        item["active"] = current_path == item_path
        if item["label"] == _("All tickets"):
            item["active"] = item["active"] and current_queue != "mine"
        elif item["label"] == _("Assigned to me"):
            item["active"] = item["active"] and current_queue == "mine"
    return {"sidebar_navigation": navigation}
