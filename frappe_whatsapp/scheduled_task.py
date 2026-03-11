import frappe
import json
from frappe.utils import get_datetime, now_datetime, add_days
from frappe.utils.user import get_users_with_role
from frappe.integrations.utils import make_post_request
import requests

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

def check_pending_whatsapp_messages():
    """Check for pending WhatsApp messages and send template notification to customers."""
    # Get the pending notification template from WhatsApp Settings
    settings = frappe.get_single("WhatsApp Settings")
    pending_notification_template = settings.pending_whatsapp_template
    print(f"[Pending WA] Template found: {pending_notification_template}")
    if not pending_notification_template:
        frappe.logger().info("[Pending WA] No pending_whatsapp_template set in WhatsApp Settings. Exiting.")
        return

    pending_messages = frappe.db.get_all(
        "Pending WhatsApp Message",
        filters={"status": "Pending"},
        fields=["name", "to", "reference_doctype", "reference_name"],
        group_by="`to`"
    )
    print(f"[Pending WA] Pending messages found: {len(pending_messages)} - {pending_messages}")

    for pending in pending_messages:
        if pending.reference_doctype != "CRM Lead" or not pending.reference_name:
            print(f"[Pending WA] Skipping {pending.name}: reference_doctype={pending.reference_doctype}, reference_name={pending.reference_name}")
            continue

        crm_lead = frappe.get_doc("CRM Lead", pending.reference_name)
        if crm_lead.notified_for_pending_message:
            print(f"[Pending WA] Skipping {pending.name}: CRM Lead {crm_lead.name} already notified")
            continue

        try:
            print(f"[Pending WA] Sending template '{pending_notification_template}' to {pending.to} for lead {crm_lead.name}")
            send_pending_notification_template(crm_lead, pending_notification_template, pending.to)
            print(f"[Pending WA] Successfully sent to {pending.to}")
        except Exception as e:
            frappe.logger().error(f"[Pending WA] Error sending to {pending.to}: {str(e)}")
            frappe.log_error(title="Pending WhatsApp Message Notification Error", message=str(e))
        finally:
            # Always mark as notified to prevent resending every 5 minutes
            frappe.db.set_value("CRM Lead", crm_lead.name, "notified_for_pending_message", 1, update_modified=False)
            frappe.db.commit()

def send_pending_notification_template(crm_lead, whatsapp_message_template, phone_number):
    """Send a template message to notify customer about pending messages."""
    whatsapp_message_template_doc = frappe.get_doc("WhatsApp Message Templates", whatsapp_message_template)
    settings = frappe.get_single("WhatsApp Settings")
    token = settings.get_password("token")

    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }

    data = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "template",
        "template": {
            "name": whatsapp_message_template_doc.name,
            "language": {"code": "en"},
            "components": [],
        },
    }

    response = make_post_request(
        f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
        headers=headers,
        data=json.dumps(data),
    )

    message_id = response["messages"][0]["id"]
    doc = frappe.new_doc("WhatsApp Message")
    doc.update({
        "type": "Outgoing",
        "reference_doctype": "CRM Lead",
        "reference_name": crm_lead.name,
        "message_type": "Manual",
        "message": whatsapp_message_template_doc.message,
        "content_type": "text",
        "to": phone_number,
        "message_id": message_id,
        "status": "Success",
        "timestamp": get_datetime(),
        "whatsapp_message_templates": whatsapp_message_template_doc.name
    })
    doc.flags.is_template_queue = True
    doc.insert(ignore_permissions=True)

def cleanup_pending_whatsapp_messages():
    """Expire and clean up pending WhatsApp messages."""
    now = now_datetime()

    # Mark pending messages that have passed their expires_at as Expired
    expired_messages = frappe.db.get_all(
        "Pending WhatsApp Message",
        filters={"status": "Pending", "expires_at": ["<", now]},
        pluck="name"
    )
    for name in expired_messages:
        frappe.db.set_value("Pending WhatsApp Message", name, "status", "Expired", update_modified=False)
    if expired_messages:
        frappe.db.commit()

    # Delete expired messages where modified is older than 2 days
    two_days_ago = add_days(now, -2)
    messages_to_delete = frappe.db.get_all(
        "Pending WhatsApp Message",
        filters={"status": "Expired", "modified": ["<", two_days_ago]},
        fields=["name", "reference_doctype", "reference_name"]
    )

    # Collect CRM Leads to check after deletion
    crm_leads_to_check = set()
    for msg in messages_to_delete:
        if msg.reference_doctype == "CRM Lead" and msg.reference_name:
            crm_leads_to_check.add(msg.reference_name)
        frappe.delete_doc("Pending WhatsApp Message", msg.name, ignore_permissions=True)

    if messages_to_delete:
        frappe.db.commit()

    # For each affected CRM Lead, check if they still have pending messages
    for lead_name in crm_leads_to_check:
        has_pending = frappe.db.exists("Pending WhatsApp Message", {
            "reference_doctype": "CRM Lead",
            "reference_name": lead_name,
            "status": "Pending"
        })
        if not has_pending:
            frappe.db.set_value("CRM Lead", lead_name, "notified_for_pending_message", 0, update_modified=False)

    if crm_leads_to_check:
        frappe.db.commit()

def send_push_notification(user, title, message, url=None):
    push_notification_subscriptions = frappe.db.get_all("Push Notification Subscription", filters={"user": user}, pluck="name")
    for push_notification_subscription in push_notification_subscriptions:
        push_notification = frappe.new_doc("Push Notification Log")
        push_notification.push_notification_subscription = push_notification_subscription
        push_notification.title = title
        push_notification.message = message
        push_notification.url = url
        push_notification.insert(ignore_permissions=True)

def sync_outlets():
    integration_settings = frappe.db.get_all("Integration Settings", filters={"active": 1}, pluck="name")
    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc("Integration Settings", integration_setting)
        url = integration_settings_doc.site_url + "/api/method/healthland_pos.api.get_outlets"
        headers = {
            "Authorization": "Basic {0}".format(integration_settings_doc.get_password("access_token")),
            "Content-Type": "application/json"
        }
        try:
            response = requests.post(url, headers=headers, timeout=30)
            response.raise_for_status()
            response_json = response.json()
            outlets = response_json.get("outlets", [])

            frappe.db.truncate("Outlet")

            for outlet in outlets:
                frappe.get_doc({
                    "doctype": "Outlet",
                    "branch_code": outlet.get("name"),
                    "shop_full_name": outlet.get("shop_full_name"),
                }).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Sync Outlets Failed: {integration_setting}")