$(() => {
    //only allow for system user
    if (frappe.session.user == "Guest") {
        return
    }

    // skip if browser not supported
    if (!browserSupportPush()) {
        return;
    }

    // skip if user rejected earlier
    if (userRejectedEarlier()) {
        return;
    }

    // user accepted earlier
    if (permissionRequestedEarlier()) {
        userAccept();
        return
    }

    setTimeout(() => {
        frappe.confirm(__('Allow notification to get notified on latest update?<br><br><b>Note:</b><br>Click "Allow" in the next popup if you choose "Yes".'),
            () => {
                userAccept();
            }, () => {
                userReject();
            });
    }, 5000);
});

const userReject = () => {
    storeUserRejectTimestamp();
};

const userAccept = () => {
    requestNotificationPermission();
};

function browserSupportPush() {
    if (!("serviceWorker" in navigator)) {
        // Service Worker isn't supported on this browser, disable or hide UI.
        return false;
    }
    if (!("PushManager" in window)) {
        // Push isn't supported on this browser, disable or hide UI.
        return false;
    }
    return true;
}

function permissionRequestedEarlier() {
    if ("Notification" in window && (Notification.permission === "granted" || Notification.permission === "denied")) {
        return true;
    }
    else if ("Notification" in window && Notification.permission === "default") {
        return false;
    }
    return false;
}

function userRejectedEarlier() {
    const userRejectedAt = localStorage.getItem("userRejectedAt");
    if (userRejectedAt) {
        const currentTimestamp = Date.now();
        const elapsedMilliseconds = currentTimestamp - parseInt(userRejectedAt, 10);
        const hoursPassed = elapsedMilliseconds / (1000 * 60 * 60);
        if (hoursPassed < 24) {
            return true;
        }
        else {
            localStorage.removeItem("userRejectedAt");
            return false;
        }
    }
    return false;
}

// record user reject timestamp, don't ask anymore within 24 hours
function storeUserRejectTimestamp() {
    const currentTimestamp = Date.now();
    localStorage.setItem("userRejectedAt", currentTimestamp);
}

function requestNotificationPermission() {
    Notification.requestPermission().then(function (permission) {
        if (permission === "granted") {
            // User granted permission
            navigator.serviceWorker
                .register("/assets/frappe_whatsapp/js/push_notification/fw_sw.js")
                .then(function (registration) {
                    var serviceWorker;
                    if (registration.installing) {
                        serviceWorker = registration.installing;
                    } else if (registration.waiting) {
                        serviceWorker = registration.waiting;
                    } else if (registration.active) {
                        serviceWorker = registration.active;
                    }

                    if (serviceWorker) {
                        if (serviceWorker.state == "activated") {
                            // call API to subscribe
                            subscribeForPushNotification(registration);
                        }
                        serviceWorker.addEventListener("statechange", function (e) {
                            if (e.target.state == "activated") {
                                // call API to subscribe
                                subscribeForPushNotification(registration);
                            }
                        });
                    }
                })
                .catch(function (err) {
                    console.error("Unable to register service worker.", err);
                });
        }
        else {
            frappe.call({
                method: "frappe_whatsapp.web_push.subscribe_push_notification",
                args: {
                    user: frappe.session.user,
                    permission: permission
                }
            });
        }
    });
}

function subscribeForPushNotification(registration) {
    const subscribeOptions = {
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(
            "BORmMCw_esNI7PJkN_kGwInQERVraFEhZ9KOptJsvlAQa8hbiQJXQQbMq2Yvqfvjuocnqwkxz4ropwyEJ0vgXoc",
        ),
    };
    registration.pushManager
        .subscribe(subscribeOptions)
        .then(function (pushSubscription) {
            const pushSubscriptionObj = JSON.parse(JSON.stringify(pushSubscription))
            frappe.call({
                method: "frappe_whatsapp.web_push.subscribe_push_notification",
                args: {
                    user: frappe.session.user,
                    permission: "granted",
                    endpoint: pushSubscriptionObj.endpoint,
                    p256dh: pushSubscriptionObj.keys.p256dh,
                    auth: pushSubscriptionObj.keys.auth
                }
            });
        })
}

function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/\-/g, "+").replace(/_/g, "/");

    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}
