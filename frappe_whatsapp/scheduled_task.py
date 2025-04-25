import frappe
from frappe.utils.user import get_users_with_role

def send_noficiation_for_new_crm_leads():
    crm_agents = get_users_with_role("CRM Agent")
    for crm_agent in crm_agents:
        assigned_templates = frappe.db.get_all("User Permission", filters={"user": crm_agent, "allow": "WhatsApp Message Templates"}, pluck="for_value")
        if assigned_templates:
            uncompleted_assignments = frappe.db.get_all("CRM Lead Assignment", filters={
                "whatsapp_message_templates": ["in", assigned_templates],
                "status": "New"
            }, pluck="crm_lead")
            if uncompleted_assignments:
                send_push_notification(crm_agent, "🎉 Yay! New Messages!", f"📩 You have {len(uncompleted_assignments)} unread messages waiting for you! Tap to check them out!", url="https://crm.techmind.com.my/crm/leads/{0}#whatsapp".format(uncompleted_assignments[0]))
        else:
            uncompleted_assignments = frappe.db.get_all("CRM Lead Assignment", filters={
                "whatsapp_message_templates": ["!=", "automated_message"],
                "status": "New"
            }, pluck="crm_lead")
            if uncompleted_assignments:
                send_push_notification(crm_agent, "🎉 Yay! New Messages!", f"📩 You have {len(uncompleted_assignments)} unread messages waiting for you! Tap to check them out!", url="https://crm.techmind.com.my/crm/leads/{0}#whatsapp".format(uncompleted_assignments[0]))

def send_push_notification(user, title, message, url=None):
    push_notification_subscriptions = frappe.db.get_all("Push Notification Subscription", filters={"user": user}, pluck="name")
    for push_notification_subscription in push_notification_subscriptions:
        push_notification = frappe.new_doc("Push Notification Log")
        push_notification.push_notification_subscription = push_notification_subscription
        push_notification.title = title
        push_notification.message = message
        push_notification.url = url
        push_notification.insert(ignore_permissions=True)