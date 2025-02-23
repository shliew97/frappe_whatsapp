self.addEventListener("push", function (event) {
    const pushData = event.data.text();
    let data, title, options;
    try {
        data = JSON.parse(pushData);
        title = data.title;
        options = data.options;
    } catch (e) {
        title = "Untitled";
        options = {
            body: pushData,
            url: ""
        }
    }
    const notification_options = {
        body: options.body,
        icon: "",
        badge: "",
        requireInteraction: true,
        slient: false,
        data: {
            url: options.url,
            id: options.id
        }
    };

    event.waitUntil(
        self.registration.showNotification(title, notification_options)
    );
});

self.addEventListener("notificationclick", event => {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: "window" }).then(clientsArr => {
            // Check if the target URL is already open, if yes, focus on it
            for (const client of clientsArr) {
                if (client.url === event.notification.data.url) {
                    return client.focus();
                }
            }
            // If the target URL is not open, open it in a new window/tab
            if (clients.openWindow && event.notification.data.url) {
                return clients.openWindow(event.notification.data.url);
            }
        })
    );
});
