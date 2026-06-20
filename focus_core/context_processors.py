from .models import GroupNotification, ProjectNotification


def unread_notification_count(request):
    if not request.user.is_authenticated:
        return {"unread_notification_count": 0}

    project_count = ProjectNotification.objects.filter(
        recipient=request.user,
        read_at__isnull=True,
    ).count()
    group_count = GroupNotification.objects.filter(
        recipient=request.user,
        read_at__isnull=True,
    ).count()
    return {
        "unread_notification_count": project_count + group_count
    }
