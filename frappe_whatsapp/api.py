import frappe
from frappe.utils import get_datetime
from frappe.integrations.utils import make_post_request
import json
from crm.api.whatsapp import get_lead_or_deal_from_number
from frappe.utils.background_jobs import enqueue
import time
import json
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import send_message, send_image as _send_image

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
                crm_lead_doc.whatsapp_message_templates = whatsapp_message_template
                crm_lead_doc.insert(ignore_permissions=True)
                reference_name = crm_lead_doc.name
            else:
                crm_lead_doc = frappe.get_doc(doctype, reference_name)
                crm_lead_doc.whatsapp_message_templates = whatsapp_message_template
                crm_lead_doc.save(ignore_permissions=True)
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

@frappe.whitelist()
def send_test_message(mobile_no):
    try:
        frappe.response["success"] = False
        whatsapp_api_settings = frappe.get_single("WhatsApp API Settings")
        whitelisted_numbers = [whitelisted_number.mobile_no for whitelisted_number in whatsapp_api_settings.whitelisted_number]
        if mobile_no not in whitelisted_numbers:
            frappe.response["message"] = "You are not allowed to send message to this number."
            return
        reference_name, doctype = get_lead_or_deal_from_number(mobile_no)
        if not reference_name:
            crm_lead_doc = frappe.new_doc("CRM Lead")
            crm_lead_doc.lead_name = mobile_no
            crm_lead_doc.first_name = mobile_no
            crm_lead_doc.last_name = ""
            crm_lead_doc.mobile_no = mobile_no
            crm_lead_doc.whatsapp_message_templates = "hello_world_v2"
            crm_lead_doc.insert(ignore_permissions=True)
            reference_name = crm_lead_doc.name
        else:
            crm_lead_doc = frappe.get_doc(doctype, reference_name)
            crm_lead_doc.whatsapp_message_templates = "hello_world_v2"
            crm_lead_doc.save(ignore_permissions=True)

        whatsapp_message_template_doc = frappe.get_doc("WhatsApp Message Templates", "hello_world_v2")
        settings = frappe.get_single("WhatsApp Settings")
        token = settings.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }

        data = {
            "messaging_product": "whatsapp",
            "to": mobile_no,
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
        doc.update(
            {
                "reference_doctype": "CRM Lead",
                "reference_name": reference_name,
                "message_type": "Manual",
                "message": whatsapp_message_template_doc.message,
                "content_type": "text",
                "to": mobile_no,
                "message_id": message_id,
                "status": "Success",
                "timestamp": get_datetime(),
                "whatsapp_message_templates": whatsapp_message_template_doc.name
            }
        )
        doc.flags.is_template_queue = True
        doc.insert(ignore_permissions=True)
        frappe.response["success"] = True
        frappe.response["message"] = "Message successfully sent."
    except Exception as e:
        frappe.response["message"] = str(e)

@frappe.whitelist()
def send_template_message(mobile_no, template, parameters):
    if isinstance(parameters, str):
        parameters = json.loads(parameters)
    try:
        frappe.response["success"] = False
        reference_name, doctype = get_lead_or_deal_from_number(mobile_no)
        if not reference_name:
            crm_lead_doc = frappe.new_doc("CRM Lead")
            crm_lead_doc.lead_name = mobile_no
            crm_lead_doc.first_name = mobile_no
            crm_lead_doc.last_name = ""
            crm_lead_doc.mobile_no = mobile_no
            crm_lead_doc.whatsapp_message_templates = template
            crm_lead_doc.insert(ignore_permissions=True)
            reference_name = crm_lead_doc.name
        else:
            crm_lead_doc = frappe.get_doc(doctype, reference_name)
            crm_lead_doc.whatsapp_message_templates = template
            crm_lead_doc.save(ignore_permissions=True)

        whatsapp_message_template_doc = frappe.get_doc("WhatsApp Message Templates", template)
        settings = frappe.get_single("WhatsApp Settings")
        token = settings.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }

        components = []

        request_body_parameters = []

        if parameters:
            request_body_parameters = [{
                "type": "text",
                "parameter_name": parameter["parameter_name"],
                "text": parameter["text"],
            } for parameter in parameters]

            components = [
                {
                    "type": "body",
                    "parameters": request_body_parameters
                }
            ]

        data = {
            "messaging_product": "whatsapp",
            "to": mobile_no,
            "type": "template",
            "template": {
                "name": whatsapp_message_template_doc.name,
                "language": {"code": "en"},
                "components": components
            },
        }

        response = make_post_request(
            f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
            headers=headers,
            data=json.dumps(data),
        )

        message_id = response["messages"][0]["id"]

        param_dict = {p["parameter_name"]: p["text"] for p in parameters}

        message = whatsapp_message_template_doc.message.format(**param_dict)

        doc = frappe.new_doc("WhatsApp Message")
        doc.update(
            {
                "reference_doctype": "CRM Lead",
                "reference_name": reference_name,
                "message_type": "Manual",
                "message": message,
                "content_type": "text",
                "to": mobile_no,
                "message_id": message_id,
                "status": "Success",
                "timestamp": get_datetime(),
                "whatsapp_message_templates": whatsapp_message_template_doc.name
            }
        )
        doc.flags.is_template_queue = True
        doc.insert(ignore_permissions=True)
        frappe.response["success"] = True
        frappe.response["message"] = "Message successfully sent."
    except Exception as e:
        frappe.response["message"] = str(e)

@frappe.whitelist()
def send_message(mobile_no, message):
    try:
        frappe.response["success"] = False

        if len(message) >= 4096:
            frappe.response["message"] = "Maximum length of message is 4096 characters."
            return

        reference_name, doctype = get_lead_or_deal_from_number(mobile_no)

        if not reference_name:
            crm_lead_doc = frappe.new_doc("CRM Lead")
            crm_lead_doc.lead_name = mobile_no
            crm_lead_doc.first_name = mobile_no
            crm_lead_doc.last_name = ""
            crm_lead_doc.mobile_no = mobile_no
            crm_lead_doc.whatsapp_message_templates = "hl_tech"
            crm_lead_doc.insert(ignore_permissions=True)
            reference_name = crm_lead_doc.name
        else:
            crm_lead_doc = frappe.get_doc(doctype, reference_name)
            crm_lead_doc.whatsapp_message_templates = "hl_tech"
            crm_lead_doc.save(ignore_permissions=True)

        send_message(crm_lead_doc, mobile_no, message)

        frappe.response["success"] = True
        frappe.response["message"] = "Message successfully sent."
    except Exception as e:
        frappe.response["message"] = str(e)

@frappe.whitelist()
def send_image(mobile_no, image_url, caption):
    try:
        frappe.response["success"] = False

        if len(caption) >= 4096:
            frappe.response["message"] = "Maximum length of caption is 4096 characters."
            return

        reference_name, doctype = get_lead_or_deal_from_number(mobile_no)

        if not reference_name:
            crm_lead_doc = frappe.new_doc("CRM Lead")
            crm_lead_doc.lead_name = mobile_no
            crm_lead_doc.first_name = mobile_no
            crm_lead_doc.last_name = ""
            crm_lead_doc.mobile_no = mobile_no
            crm_lead_doc.whatsapp_message_templates = "hl_tech"
            crm_lead_doc.insert(ignore_permissions=True)
            reference_name = crm_lead_doc.name
        else:
            crm_lead_doc = frappe.get_doc(doctype, reference_name)
            crm_lead_doc.whatsapp_message_templates = "hl_tech"
            crm_lead_doc.save(ignore_permissions=True)

        _send_image(crm_lead_doc, mobile_no, caption, image_url)

        frappe.response["success"] = True
        frappe.response["message"] = "Message successfully sent."
    except Exception as e:
        frappe.response["message"] = str(e)