from django.db.models import Max

from .models import GroupNotification, ProjectNotification


def unread_notification_count(request):
    if not request.user.is_authenticated:
        return {
            "unread_notification_count": 0,
            "latest_project_notification_id": 0,
            "latest_group_notification_id": 0,
        }

    project_notifications = ProjectNotification.objects.filter(
        recipient=request.user,
    )
    group_notifications = GroupNotification.objects.filter(
        recipient=request.user,
    )
    project_count = project_notifications.filter(read_at__isnull=True).count()
    group_count = group_notifications.filter(read_at__isnull=True).count()
    return {
        "unread_notification_count": project_count + group_count,
        "latest_project_notification_id": project_notifications.aggregate(latest_id=Max("id"))["latest_id"] or 0,
        "latest_group_notification_id": group_notifications.aggregate(latest_id=Max("id"))["latest_id"] or 0,
    }
