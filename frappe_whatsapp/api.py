import frappe
from frappe.utils import get_datetime
from frappe.integrations.utils import make_post_request
import json
from crm.api.whatsapp import get_lead_or_deal_from_number
from frappe.utils.background_jobs import enqueue
import time
import json

@frappe.whitelist()
def enqueue_send_whatsapp_template(whatsapp_message_template, whatsapp_template_queues):
    whatsapp_template_queues = json.loads(whatsapp_template_queues)
    whatsapp_template_queues = frappe.db.get_all("WhatsApp Template Queue", filters={"status": "Pending", "name": ["in", whatsapp_template_queues]}, fields=["name", "phone_number", "customer_name", "outlet"])
    for whatsapp_template_queue in whatsapp_template_queues:
        frappe.db.set_value("WhatsApp Template Queue", whatsapp_template_queue.name, "status", "In Queue", update_modified=False)
    frappe.db.commit()
    enqueue(method=schedule_send_whatsapp_template, whatsapp_message_template=whatsapp_message_template, whatsapp_template_queues=whatsapp_template_queues, queue="long", timeout=7200, is_async=True)

def schedule_send_whatsapp_template(whatsapp_message_template, whatsapp_template_queues):
    whatsapp_message_template_doc = frappe.get_doc("WhatsApp Message Templates", whatsapp_message_template)
    settings = frappe.get_single("WhatsApp Settings")
    token = settings.get_password("token")

    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }

    for whatsapp_template_queue in whatsapp_template_queues:
        parameters = [{
            "type": "text",
            "parameter_name": whatsapp_message_template_parameter.parameter_name,
            "text": whatsapp_template_queue.get(whatsapp_message_template_parameter.parameter_name) or ("dear customer" if whatsapp_message_template_parameter.parameter_name == "customer_name" else "")
        } for whatsapp_message_template_parameter in whatsapp_message_template_doc.whatsapp_message_template_parameters]
        try:
            reference_name, doctype = get_lead_or_deal_from_number(whatsapp_template_queue.phone_number)
            if not reference_name:
                crm_lead_doc = frappe.new_doc("CRM Lead")
                crm_lead_doc.lead_name = whatsapp_template_queue.customer_name or whatsapp_template_queue.phone_number
                crm_lead_doc.first_name = whatsapp_template_queue.customer_name or whatsapp_template_queue.phone_number
                crm_lead_doc.last_name = ""
                crm_lead_doc.mobile_no = whatsapp_template_queue.phone_number
                crm_lead_doc.insert(ignore_permissions=True)
                reference_name = crm_lead_doc.name
            data = {
                "messaging_product": "whatsapp",
                "to": whatsapp_template_queue.phone_number,
                "type": "template",
                "template": {
                    "name": whatsapp_message_template_doc.name,
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": parameters
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
                    "message": whatsapp_message_template_doc.message.format(customer_name=whatsapp_template_queue.customer_name or "dear customer", outlet=whatsapp_template_queue.outlet or ""),
                    "content_type": "text",
                    "to": whatsapp_template_queue.phone_number,
                    "message_id": message_id,
                    "status": "Success",
                    "timestamp": get_datetime(),
                    "whatsapp_message_templates": whatsapp_message_template_doc.name
                }
            )
            doc.flags.is_template_queue = True
            doc.insert(ignore_permissions=True)
            frappe.db.set_value("WhatsApp Template Queue", whatsapp_template_queue.name, "status", "Sent")
            frappe.db.commit()
            time.sleep(3)

        except Exception as e:
            frappe.db.set_value("WhatsApp Template Queue", whatsapp_template_queue.name, "status", "Failed")
            frappe.db.commit()
            frappe.log_error(title="Error", message=str(e))