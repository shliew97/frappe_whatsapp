import frappe

@frappe.whitelist()
def subscribe_push_notification():
    if frappe.form_dict.permission == "granted":
        push_notification_subscriptions = frappe.db.get_all("Push Notification Subscription", filters={"p256dh": frappe.form_dict.p256dh}, fields=["name", "user"])
        if push_notification_subscriptions:
            if push_notification_subscriptions[0].user != frappe.form_dict.user:
                push_notification_subscription_doc = frappe.get_doc("Push Notification Subscription", push_notification_subscriptions[0].name)
                push_notification_subscription_doc.user = frappe.form_dict.user
                push_notification_subscription_doc.save(ignore_permissions=True)
            return

        push_notification_subscription_doc = frappe.new_doc("Push Notification Subscription")
        push_notification_subscription_doc.user = frappe.form_dict.user
        push_notification_subscription_doc.permission = frappe.form_dict.permission
        push_notification_subscription_doc.endpoint = frappe.form_dict.endpoint
        push_notification_subscription_doc.p256dh = frappe.form_dict.p256dh
        push_notification_subscription_doc.auth = frappe.form_dict.auth
        push_notification_subscription_doc.insert(ignore_permissions=True)

def send_push_notification(user, title, message, url=None):
    push_notification_subscriptions = frappe.db.get_all("Push Notification Subscription", filters={"user": user}, pluck="name")
    for push_notification_subscription in push_notification_subscriptions:
        push_notification = frappe.new_doc("Push Notification Log")
        push_notification.push_notification_subscription = push_notification_subscription
        push_notification.title = title
        push_notification.message = message
        push_notification.url = url
        push_notification.insert(ignore_permissions=True)
