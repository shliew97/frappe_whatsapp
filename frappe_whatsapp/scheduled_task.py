import frappe
from frappe.utils import get_datetime
from frappe.integrations.utils import make_post_request
import json
from crm.api.whatsapp import get_lead_or_deal_from_number

CUSTOMER_FEEDBACK_TEMPLATE = "🌟 Good Morning {customer_name}! 🌟\n\nIt was a pleasure serving you at {outlet} yesterday! 😊\nDid you enjoy your time at HealthLand?\n\n💬 We'd love to hear your thoughts!\nLooking forward to your reply! 💜"

@frappe.whitelist()
def schedule_send_whatsapp_template():
    settings = frappe.get_doc(
        "WhatsApp Settings",
        "WhatsApp Settings",
    )
    token = settings.get_password("token")

    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }

    whatsapp_template_queues = frappe.db.get_all("WhatsApp Template Queue", filters={"status": "Pending"}, fields=["name", "phone_number", "customer_name", "outlet"], limit=5)
    for whatsapp_template_queue in whatsapp_template_queues:
        try:
            reference_name, doctype = get_lead_or_deal_from_number(whatsapp_template_queue.phone_number)
            if not reference_name:
                crm_lead_doc = frappe.new_doc("CRM Lead")
                crm_lead_doc.first_name = whatsapp_template_queue.customer_name
                crm_lead_doc.last_name = ""
                crm_lead_doc.mobile_no = whatsapp_template_queue.phone_number
                crm_lead_doc.insert(ignore_permissions=True)
                reference_name = crm_lead_doc.name
            data = {
                "messaging_product": "whatsapp",
                "to": whatsapp_template_queue.phone_number,
                "type": "template",
                "template": {
                    "name": "customer_feedback",
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {
                                    "type": "text",
                                    "parameter_name": "customer_name",
                                    "text": whatsapp_template_queue.customer_name
                                },
                                {
                                    "type": "text",
                                    "parameter_name": "outlet",
                                    "text": whatsapp_template_queue.outlet
                                }
                            ],
                        }
                    ],
                },
            }
            response = make_post_request(
                f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )
            message_id = response["messages"][0]["id"]
            doc = frappe.new_doc("WhatsApp Message")
            doc.update(
                {
                    "reference_doctype": "CRM Lead",
                    "reference_name": reference_name,
                    "message_type": "Manual",
                    "message": CUSTOMER_FEEDBACK_TEMPLATE.format(customer_name=whatsapp_template_queue.customer_name, outlet=whatsapp_template_queue.outlet),
                    "content_type": "text",
                    "to": whatsapp_template_queue.phone_number,
                    "message_id": message_id,
                    "status": "Success",
                    "timestamp": get_datetime()
                }
            )
            doc.insert(ignore_permissions=True)
            frappe.db.set_value("WhatsApp Template Queue", whatsapp_template_queue.name, "status", "Sent")
            frappe.db.commit()

        except Exception as e:
            res = frappe.flags.integration_request.json()["error"]
            error_message = res.get("Error", res.get("message"))
            frappe.get_doc(
                {
                    "doctype": "WhatsApp Notification Log",
                    "template": "Text Message",
                    "meta_data": frappe.flags.integration_request.json(),
                }
            ).insert(ignore_permissions=True)
            frappe.db.set_value("WhatsApp Template Queue", whatsapp_template_queue.name, "status", "Failed")
            frappe.db.commit()
            frappe.throw(msg=error_message, title=res.get("error_user_title", "Error"))