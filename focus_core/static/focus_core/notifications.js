(function () {
    const script = document.currentScript || document.querySelector('script[src*="/notifications.js"]');
    if (!script || !script.dataset.pollUrl) {
        return;
    }

    const pollUrl = script.dataset.pollUrl;
    const pollInterval = Number(script.dataset.pollInterval || 15000);
    const navLink = document.getElementById("notification-nav-link");
    const toastRegion = document.getElementById("notification-toast-region");
    const announcer = document.getElementById("notification-announcer");
    let latestProjectId = Number(script.dataset.initialProjectId || 0);
    let latestGroupId = Number(script.dataset.initialGroupId || 0);
    let polling = false;
    let toastTimer = null;
    let announcementTimer = null;
    const announcementQueue = [];

    function updateNavCount(unreadCount) {
        if (!navLink || typeof unreadCount !== "number") {
            return;
        }
        navLink.textContent = unreadCount ? `Notifications (${unreadCount} unread)` : "Notifications";
    }

    function showToast(message) {
        if (!toastRegion) {
            return;
        }
        window.clearTimeout(toastTimer);
        toastRegion.textContent = message;
        toastRegion.classList.add("notification-toast-region--visible");
        toastTimer = window.setTimeout(function () {
            toastRegion.classList.remove("notification-toast-region--visible");
            toastRegion.textContent = "";
        }, 6000);
    }

    function announceNext() {
        if (!announcer || !announcementQueue.length) {
            announcementTimer = null;
            return;
        }
        announcer.textContent = "";
        const message = announcementQueue.shift();
        window.setTimeout(function () {
            announcer.textContent = `New notification: ${message}`;
        }, 100);
        announcementTimer = window.setTimeout(announceNext, 2500);
    }

    function queueAnnouncement(message) {
        announcementQueue.push(message);
        if (!announcementTimer) {
            announceNext();
        }
    }

    function notificationUrl() {
        const url = new URL(pollUrl, window.location.origin);
        url.searchParams.set("after_project_id", String(latestProjectId));
        url.searchParams.set("after_group_id", String(latestGroupId));
        return url;
    }

    async function poll() {
        if (polling) {
            return;
        }
        polling = true;
        try {
            const response = await window.fetch(notificationUrl(), {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            if (!response.ok) {
                return;
            }
            const data = await response.json();
            latestProjectId = Number(data.latest_project_id || latestProjectId);
            latestGroupId = Number(data.latest_group_id || latestGroupId);
            updateNavCount(data.unread_count);
            (data.notifications || []).forEach(function (notification) {
                showToast(notification.message);
                queueAnnouncement(notification.message);
            });
        } finally {
            polling = false;
        }
    }

    window.setInterval(poll, pollInterval);
})();
