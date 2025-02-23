import frappe

def send_noficiation_for_new_crm_leads():
    new_leads = frappe.db.get_all("CRM Lead", filters={"conversation_status": "New"}, pluck="name")
    if new_leads:
        send_push_notification("🎉 Yay! New Messages!", f"📩 You have {len(new_leads)} unread messages waiting for you! Tap to check them out!", url="https://crm.techmind.com.my/crm/leads/view")

def send_push_notification(title, message, url=None):
    push_notification_subscriptions = frappe.db.get_all("Push Notification Subscription", pluck="name")
    for push_notification_subscription in push_notification_subscriptions:
        push_notification = frappe.new_doc("Push Notification Log")
        push_notification.push_notification_subscription = push_notification_subscription
        push_notification.title = title
        push_notification.message = message
        push_notification.url = url
        push_notification.insert(ignore_permissions=True)