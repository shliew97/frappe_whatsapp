# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import json
import frappe
from frappe.utils import get_datetime, getdate, flt, cint, add_to_date
from frappe.model.document import Document
from frappe.integrations.utils import make_post_request
import random
import datetime
import time
import hashlib
from crm.api.whatsapp import get_lead_or_deal_from_number
import requests
from frappe.utils.background_jobs import enqueue
from frappe.core.doctype.file.utils import find_file_by_url
import re
import base64
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.agents.rag_chain import (
    get_rag_chain,
    is_booking_details_message,
    has_booking_intent,
    detect_booking_intent_from_recent_context,
    extract_booking_details,
    get_pending_booking_data,
    save_pending_booking_data,
    clear_pending_booking_data,
    format_missing_fields_message,
    generate_smart_missing_fields_prompt,
    validate_booking_timeslot,
    validate_and_correct_outlet_info
)
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.handle_api_calls import (
    handle_booking_api,
    handle_booking_api_mock,
    handle_update_booking_api_mock,
    handle_cancel_booking_api_mock,
    handle_register_staff_face_api,
    handle_leave_application_api
)
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.ai_utils import (
    is_confirmation_message,
    is_change_request,
    is_general_question,
    analyze_confirmation_response_intent,
)

OUT_OF_WORKING_HOURS_MESSAGE = "Hello! 😊 Thanks for reaching out!\n\n📅 Our working hours: 9 AM - 5 PM (Monday - Friday). While we're currently unavailable, drop us a message, and we'll get back to you ASAP!\n\n💡 Want to check out our latest deals or make a purchase? Click the link below for exciting offers! 🎉👇\n\nhttps://book.healthland.com.my/privatelink/nojokepwp\n\nThank you for your patience & support! 💜"
OUT_OF_BOOKING_HOURS_MESSAGE = "📢 This is an automated message\n\nHello! 😊 Thanks for reaching out!\n\n📅 Our booking hours: 10 AM - 9 PM. While we're currently unavailable, leave us a message, and we'll get back to you ASAP!\n\n💡 Need to book now? Try our Online Booking System for a fast & hassle-free experience! 🚀\n👉 Book here: https://book.healthland.com.my/booking/selectshop \n\nThank you for your patience & understanding! 💜"
OUT_OF_BOOKING_HOURS_FOLLOW_UP_MESSAGE = "🌞 Good morning!\nThank you for reaching out to HealthLand 💜\n\nOur WhatsApp is for package/voucher redemption bookings only 💆‍♀️💆‍♂️\nFor walk-in or non-package customers, we recommend booking online to enjoy:\n✅ Enjoy better rates compared to walk-in\n✅ Secure your slot in advance\n👉 https://book.healthland.com.my/booking/selectshop \n\n✨ Have you booked online yet?\nIf not, no worries — just fill in the form below and we'll help you make the booking:\n\n• Name\n• Contact No.\n• Date & Time\n• Outlet\n• No. of Pax\n• Treatment (Foot / Thai / Oil)\n• Duration (60 / 90 / 120 min)\n• Preferred Masseur (Male / Female)\n• Voucher / Package\n\n🕒 Filling in the form helps us secure your slot faster and avoid delays.\nWe look forward to serving you soon! 💚"

CHAT_CLOSING_MESSAGE = "🌟 Hello Dear Customer! 🌟\n\nJust a quick reminder — our chat will automatically close in 24 hours if there's no reply from you. 💬\n\nWe'd love to assist you, so feel free to reply anytime. Have any questions about making a purchase? We're here for you! 😊💜\n\nLooking forward to hearing from you soon! 💬✨"

SUCCESSFULLY_NOTIFIED_CUSTOMER_MESSAGE = "✅ Noted!\nThe booking message has been successfully sent to the customer.\n\n👉 To send to another customer, simply submit a new phone number.\nYou can send phone numbers anytime!\n\nThank you! 🙏"
PLEASE_KEY_IN_VALID_MOBILE_NO_MESSAGE = "Hi! So sorry — the phone number you entered seems to be invalid 😅\nKindly re-enter the number using the correct format:\n\n📌 Example:\n🇲🇾 Malaysia: 6012XXXXXXX\n🇸🇬 Singapore: 65XXXXXXX\n\nThank you for your cooperation! 🙏"

REQUEST_MEMBERSHIP_RATE_ENDPOINT = "/api/method/healthland_pos.api.request_membership_rate"
FREE_MEMBERSHIP_REDEMPTION_ENDPOINT = "/api/method/healthland_pos.api.redeem_free_membership"
CHECKOUT_LOGIN_ENDPOINT = "/api/method/healthland_pos.api.whatsapp_login"
REGISTRATION_ENDPOINT = "/api/method/healthland_pos.api.whatsapp_registration"
RESET_PASSWORD_ENDPOINT = "/api/method/healthland_pos.api.whatsapp_reset_password"

PDPA_MESSAGE = "Thank you for joining SOMA Wellness Membership 🌸\n\nBefore we continue, please acknowledge the following:\n• Your details will be used for membership, booking and service updates.\n• You agree to receive wellness tips, exclusive offers and promotions from SOMA Wellness.\n• Your data is protected under the PDPA and will not be shared with others.\n\nBy replying “Agree”, you agree to the above Terms & Conditions."
PDPA_BUTTON = [
    {
        "type": "reply",
        "reply": {
            "id": "agree-pdpa",
            "title": "Agree" 
        }
    }
]
PDPA_ACCEPTED_REPLY = "You're now registered as a SOMA Wellness Member 🌿"

CLOCK_IN_ENDPOINT = "/api/method/healthland_pos.api.clock_in"

class WhatsAppMessage(Document):
    """Send whats app messages."""

    def before_insert(self):
        if self.message_id and self.status == "Success":
            return
        """Send message."""
        if self.type == "Outgoing":
            self.timestamp = get_datetime()
        if self.type == "Outgoing" and self.message_type != "Template":
            if self.attach and not self.attach.startswith("http"):
                link = frappe.utils.get_url() + "/" + self.attach
            else:
                link = self.attach

            data = {
                "messaging_product": "whatsapp",
                "to": self.format_number(self.to),
                "type": self.content_type,
            }
            if self.is_reply and self.reply_to_message_id:
                data["context"] = {"message_id": self.reply_to_message_id}
            if self.content_type in ["document", "image", "video"]:
                data[self.content_type.lower()] = {
                    "link": link,
                    "caption": self.message,
                }
            elif self.content_type == "reaction":
                data["reaction"] = {
                    "message_id": self.reply_to_message_id,
                    "emoji": self.message,
                }
            elif self.content_type == "text":
                data["text"] = {"preview_url": True, "body": self.message}

            elif self.content_type == "audio":
                data["text"] = {"link": link}

            try:
                self.notify(data)
                self.status = "Success"
            except Exception as e:
                self.status = "Failed"
                frappe.throw(f"Failed to send message {str(e)}")
        elif self.type == "Outgoing" and self.message_type == "Template" and not self.message_id:
            self.send_template()

    def after_insert(self):
        crm_lead_doc = frappe.get_doc("CRM Lead", self.reference_name)
        if self.type == "Incoming" and self.reference_doctype == "CRM Lead" and self.reference_name:
            is_button_reply = self.content_type == "button" and self.is_reply and self.reply_to_message_id
            # Check if message should be debounced
            from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.message_debouncer import should_debounce_message, queue_message

            # Unpack tuple return from should_debounce_message()
            should_debounce, is_incomplete = should_debounce_message(self)

            print("Result: ")
            print(should_debounce)
            print(is_incomplete)
            # Handle button replies first - before any other routing
            # This ensures template button clicks (e.g. "Proceed" for pending messages) are always processed
            if is_button_reply:
                handle_template_message_reply(self.get("from"), self.get("from_name"), self.get("message"), self.reply_to_message_id, crm_lead_doc)
            elif crm_lead_doc.is_outlet_staff:
                print("Outlet staff handling HR Flow")
                handle_outlet_staff_hr(self, crm_lead_doc)
            elif should_debounce:
                # Queue the message for batched processing
                # Pass is_incomplete flag to use appropriate timeout
                print("Reached here")
                queue_message(self, is_incomplete=is_incomplete)
            elif crm_lead_doc.is_outlet_frontdesk:
                handle_outlet_frontdesk(self.message, self.get("from"), crm_lead_doc)
            else:
                if crm_lead_doc.is_special_attention:
                    create_crm_lead_assignment(crm_lead_doc.name, "BookingHL", "New")
                if self.content_type == "text":
                    handle_text_message(self.message, self.get("from"), self.get("from_name"), crm_lead_doc)
                    print("Ignored queue message")
                    handle_text_message_ai(self.message, self.get("from"), self.get("from_name"), crm_lead_doc)
                elif self.content_type == "flow":
                    handle_interactive_message(self.interactive_id, self.get("from"), self.get("from_name"), crm_lead_doc)
                elif self.content_type == "list_reply":
                    handle_interactive_list_reply(self.get("from"), self.get("from_name"), self.interactive_id, self.message, crm_lead_doc)
                else:
                    if not crm_lead_doc.last_reply_at or crm_lead_doc.last_reply_at < add_to_date(get_datetime(), days=-1) or crm_lead_doc.closed:
                        text_auto_replies = frappe.db.get_all("Text Auto Reply", filters={"disabled": 0, "name": "automated_message"}, fields=["*"])
                        if text_auto_replies:
                            frappe.flags.update_conversation_start_at = True
                            frappe.flags.skip_lead_status_update = True
                            create_crm_lead_assignment(crm_lead_doc.name, text_auto_replies[0].whatsapp_message_templates)
                            create_crm_tagging_assignment(crm_lead_doc.name, "Unknown")
                            if text_auto_replies[0].reply_if_button_clicked:
                                if text_auto_replies[0].reply_image:
                                    enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=self.get("from"), text=text_auto_replies[0].reply_if_button_clicked, image=text_auto_replies[0].reply_image, queue="short", is_async=True)
                                else:
                                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=self.get("from"), text=text_auto_replies[0].reply_if_button_clicked, queue="short", is_async=True)
                            if text_auto_replies[0].reply_2_if_button_clicked:
                                if text_auto_replies[0].reply_image_2:
                                    enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=self.get("from"), text=text_auto_replies[0].reply_2_if_button_clicked, image=text_auto_replies[0].reply_image_2, queue="short", is_async=True)
                                else:
                                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=self.get("from"), text=text_auto_replies[0].reply_2_if_button_clicked, queue="short", is_async=True)
                            if text_auto_replies[0].whatsapp_interaction_message_templates:
                                enqueue(method=send_interaction_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=self.get("from"), whatsapp_interaction_message_template=text_auto_replies[0].whatsapp_interaction_message_templates, queue="short", is_async=True)
                            if text_auto_replies[0].send_out_of_working_hours_message and is_not_within_operating_hours():
                                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=self.get("from"), text=OUT_OF_WORKING_HOURS_MESSAGE, queue="short", is_async=True)
                            if text_auto_replies[0].send_out_of_booking_hours_message and is_not_within_booking_hours():
                                if not frappe.db.exists("Booking Follow Up", {"crm_lead": crm_lead_doc.name}):
                                    frappe.get_doc({
                                        "doctype": "Booking Follow Up",
                                        "whatsapp_id": self.get("from"),
                                        "crm_lead": crm_lead_doc.name
                                    }).insert(ignore_permissions=True)
                                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=self.get("from"), text=OUT_OF_BOOKING_HOURS_MESSAGE, queue="short", is_async=True)

            crm_lead_doc_dict = {
                "last_reply_at": get_datetime(),
                "chat_close_at": add_to_date(get_datetime(), hours=22),
                "last_message_from_me": False,
                "sent_chat_closing_reminder": False,
                "closed": 0,
                "latest_whatsapp_message_templates": None,
                "latest_whatsapp_interaction_message_templates": None,
            }

            if frappe.flags.agree_pdpa:
                crm_lead_doc_dict["agree_pdpa"] = 1

            if frappe.flags.update_conversation_start_at or not crm_lead_doc.conversation_start_at:
                crm_lead_doc_dict["conversation_start_at"] = get_datetime()

            crm_lead_doc.db_set(crm_lead_doc_dict, notify=True)

            published=False

            if (not is_button_reply and self.content_type != "flow" and not frappe.flags.skip_lead_status_update):
                publish = False
                existing_open_assignments = frappe.db.get_all("CRM Lead Assignment", filters={"crm_lead": crm_lead_doc.name, "status": ["!=", "Case Closed"]}, fields=["*"])

                for existing_open_assignment in existing_open_assignments:
                    if existing_open_assignment.status == "Completed":
                        publish = True
                        frappe.db.set_value("CRM Lead Assignment", existing_open_assignment.name, {
                            "status": "New",
                            "accepted_by": None
                        })

                if not self.flags.is_template_queue and publish:
                    published = True
                    frappe.publish_realtime("new_leads", {})

            if not published and crm_lead_doc.is_special_attention:
                frappe.publish_realtime("new_leads", {})

        if self.type == "Outgoing" and self.reference_doctype == "CRM Lead" and self.reference_name:
            crm_lead_doc_dict = {
                "last_reply_at": get_datetime(),
                "last_message_from_me": True,
                "closed": 0,
            }

            if (not crm_lead_doc.last_reply_by_user or (crm_lead_doc.last_reply_by_user and crm_lead_doc.last_reply_by_user != frappe.session.user)) and frappe.session.user != "Guest" and frappe.session.user != "Administrator":
                crm_lead_doc_dict["last_reply_by_user"] = frappe.session.user
                crm_lead_doc_dict["last_reply_by"] = frappe.db.get_value("User", frappe.session.user, "first_name")
            if not crm_lead_doc.conversation_start_at:
                crm_lead_doc_dict["conversation_start_at"] = get_datetime()

            crm_lead_doc.db_set(crm_lead_doc_dict, notify=True)

    def send_template(self):
        """Send template."""
        template = frappe.get_doc("WhatsApp Templates", self.template)
        data = {
            "messaging_product": "whatsapp",
            "to": self.format_number(self.to),
            "type": "template",
            "template": {
                "name": template.actual_name or template.template_name,
                "language": {"code": template.language_code},
                "components": [],
            },
        }

        if template.sample_values:
            field_names = template.field_names.split(",") if template.field_names else template.sample_values.split(",")
            parameters = []
            template_parameters = []

            ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
            for field_name in field_names:
                value = ref_doc.get_formatted(field_name.strip())

                parameters.append({"type": "text", "text": value})
                template_parameters.append(value)

            self.template_parameters = json.dumps(template_parameters)

            data["template"]["components"].append(
                {
                    "type": "body",
                    "parameters": parameters,
                }
            )

        if template.header_type and template.sample:
            field_names = template.sample.split(",")
            header_parameters = []
            template_header_parameters = []

            ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
            for field_name in field_names:
                value = ref_doc.get_formatted(field_name.strip())
                
                header_parameters.append({"type": "text", "text": value})
                template_header_parameters.append(value)

            self.template_header_parameters = json.dumps(template_header_parameters)

            data["template"]["components"].append({
                "type": "header",
                "parameters": header_parameters,
            })

        self.notify(data)

    def notify(self, data):
        """Notify."""
        settings = frappe.get_doc(
            "WhatsApp Settings",
            "WhatsApp Settings",
        )
        token = settings.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            response = make_post_request(
                f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )
            self.message_id = response["messages"][0]["id"]

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

            frappe.throw(msg=error_message, title=res.get("error_user_title", "Error"))

    def format_number(self, number):
        """Format number."""
        if number.startswith("+"):
            number = number[1 : len(number)]

        return number

# def queue_notify(doc, data):
#     doc.notify(data)

def on_doctype_update():
    frappe.db.add_index("WhatsApp Message", ["reference_doctype", "reference_name"])

@frappe.whitelist()
def send_template(to, reference_doctype, reference_name, template):
    try:
        doc = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "to": to,
            "type": "Outgoing",
            "message_type": "Template",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content_type": "text",
            "template": template
        })

        doc.save()
    except Exception as e:
        raise e

def handle_outlet_frontdesk(message, frontdesk_whatsapp_id, crm_lead_doc):
    front_desk_crm_lead_doc = get_crm_lead(frontdesk_whatsapp_id, frontdesk_whatsapp_id)
    customer_whatsapp_id = normalize_phone_number(message)
    if not validate_phone_number(customer_whatsapp_id):
        enqueue(method=send_message_with_delay, crm_lead_doc=front_desk_crm_lead_doc, whatsapp_id=frontdesk_whatsapp_id, text=PLEASE_KEY_IN_VALID_MOBILE_NO_MESSAGE, queue="short", is_async=True)
        return
    settings = frappe.get_single("WhatsApp Settings")
    token = settings.get_password("token")
    whatsapp_message_template_doc = frappe.get_doc("WhatsApp Message Templates", "outlet_frontdesk_request")
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    parameters = []
    try:
        reference_name, doctype = get_lead_or_deal_from_number(customer_whatsapp_id)
        if not reference_name:
            crm_lead_doc = frappe.new_doc("CRM Lead")
            crm_lead_doc.lead_name = customer_whatsapp_id
            crm_lead_doc.first_name = customer_whatsapp_id
            crm_lead_doc.last_name = ""
            crm_lead_doc.mobile_no = customer_whatsapp_id
            crm_lead_doc.insert(ignore_permissions=True)
            reference_name = crm_lead_doc.name
        else:
            crm_lead_doc = frappe.get_doc(doctype, reference_name)
            crm_lead_doc.save(ignore_permissions=True)

        create_crm_lead_assignment(crm_lead_doc.name, whatsapp_message_template_doc.name)
        create_crm_tagging_assignment(crm_lead_doc.name, whatsapp_message_template_doc.tagging)

        data = {
            "messaging_product": "whatsapp",
            "to": customer_whatsapp_id,
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
                "message": whatsapp_message_template_doc.message,
                "content_type": "text",
                "to": customer_whatsapp_id,
                "message_id": message_id,
                "status": "Success",
                "timestamp": get_datetime(),
                "whatsapp_message_templates": whatsapp_message_template_doc.name
            }
        )
        doc.flags.is_template_queue = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        enqueue(method=send_message_with_delay, crm_lead_doc=front_desk_crm_lead_doc, whatsapp_id=frontdesk_whatsapp_id, text=SUCCESSFULLY_NOTIFIED_CUSTOMER_MESSAGE, queue="short", is_async=True)

    except Exception as e:
        frappe.db.commit()
        frappe.log_error(title="Error", message=str(e))

def normalize_phone_number(number: str) -> str:
    """
    Remove all non-digit characters and optionally normalize.
    If number starts with '01', prepend '6'.
    """
    digits = re.sub(r'\D', '', number)
    
    if digits.startswith('01'):
        digits = '6' + digits

    return digits

def validate_phone_number(cleaned_number: str) -> bool:
    """
    Validates that the phone number has a valid length (10–15 digits).
    """
    return 10 <= len(cleaned_number) <= 15

def handle_clock_in_api(staff_doc, whatsapp_id, clock_details):
    """
    Handle clock in / clock out API call.

    Args:
        staff_doc: Staff document
        whatsapp_id: WhatsApp phone number (used as phone_number)
        clock_details: Dictionary containing clock information with keys:
            - latitude: float (optional)
            - longitude: float (optional)
            - image_base64: base64 image string (optional)
            - log_type: "IN" or "OUT"

    Returns:
        dict: Response from API or error dict
    """

    integration_settings = frappe.db.get_all(
        "Integration Settings",
        filters={"active": 1},
        pluck="name"
    )

    if not integration_settings:
        return {"success": False, "message": "No active integration settings found"}

    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc(
            "Integration Settings",
            integration_setting
        )

        url = integration_settings_doc.site_url + CLOCK_IN_ENDPOINT

        headers = {
            "Authorization": "Basic {0}".format(
                integration_settings_doc.get_password("access_token")
            ),
            "Content-Type": "application/json"
        }

        request_body = {
            "phone_number": whatsapp_id,
            "latitude": clock_details.get("latitude"),
            "longitude": clock_details.get("longitude"),
            "image_url": clock_details.get("image_url"),
            "log_type": clock_details.get("log_type", "IN")
        }

        try:
            response = requests.post(
                url,
                data=json.dumps(request_body, default=str),
                headers=headers,
                timeout=30
            )

            response.raise_for_status()
            response_data = response.json()

            return response_data

        except requests.Timeout:
            frappe.log_error("Clock API Timeout", "Clock in request timed out after 30 seconds")
            return {"success": False, "message": "Request timed out. Please try again."}

        except requests.RequestException as e:
            frappe.log_error("Clock API Error", f"Clock in API error: {str(e)}")
            return {"success": False, "message": f"API error: {str(e)}"}

    return {"success": False, "message": "Failed to process clock request"}

def get_clock_log_type(crm_lead_doc):
    """Get stored log type from cache (IN or OUT)."""
    try:
        cache_key = f"clock_log_type_{crm_lead_doc.name}"
        return frappe.cache().get_value(cache_key) or "IN"
    except Exception:
        return "IN"

def set_clock_log_type(crm_lead_doc, log_type):
    """Store log type in cache (expires in 10 minutes)."""
    try:
        cache_key = f"clock_log_type_{crm_lead_doc.name}"
        frappe.cache().set_value(cache_key, log_type, expires_in_sec=600)
    except Exception as e:
        frappe.log_error("Clock Log Type Cache Error", str(e))

def get_face_registration_mode(crm_lead_doc):
    """Check if user is in face registration mode."""
    try:
        cache_key = f"face_registration_mode_{crm_lead_doc.name}"
        return frappe.cache().get_value(cache_key) or False
    except Exception:
        return False

def set_face_registration_mode(crm_lead_doc, enabled=True):
    """Set face registration mode in cache (expires in 10 minutes)."""
    try:
        cache_key = f"face_registration_mode_{crm_lead_doc.name}"
        if enabled:
            frappe.cache().set_value(cache_key, True, expires_in_sec=600)
        else:
            frappe.cache().delete_value(cache_key)
    except Exception as e:
        frappe.log_error("Face Registration Mode Cache Error", str(e))

def get_leave_application_mode(crm_lead_doc):
    """Check if user is in leave application mode and get leave type."""
    try:
        cache_key = f"leave_application_mode_{crm_lead_doc.name}"
        return frappe.cache().get_value(cache_key) or None
    except Exception:
        return None

def set_leave_application_mode(crm_lead_doc, leave_type=None):
    """Set leave application mode in cache with leave type (expires in 10 minutes)."""
    try:
        cache_key = f"leave_application_mode_{crm_lead_doc.name}"
        if leave_type:
            frappe.cache().set_value(cache_key, leave_type, expires_in_sec=600)
        else:
            frappe.cache().delete_value(cache_key)
    except Exception as e:
        frappe.log_error("Leave Application Mode Cache Error", str(e))

def extract_leave_date_and_reason(message):
    """
    Extract leave date and reason from user's message using regex fallback and LLM.

    Args:
        message: User's message containing date and reason

    Returns:
        dict: {"date": "YYYY-MM-DD", "reason": "extracted reason"}
    """
    import re
    from datetime import datetime

    def parse_date_with_regex(text):
        """Try to extract date using regex patterns."""
        # Month name mapping
        month_map = {
            'jan': 1, 'january': 1,
            'feb': 2, 'february': 2,
            'mar': 3, 'march': 3,
            'apr': 4, 'april': 4,
            'may': 5,
            'jun': 6, 'june': 6,
            'jul': 7, 'july': 7,
            'aug': 8, 'august': 8,
            'sep': 9, 'sept': 9, 'september': 9,
            'oct': 10, 'october': 10,
            'nov': 11, 'november': 11,
            'dec': 12, 'december': 12
        }

        text_lower = text.lower()

        # Pattern: "16 Feb 2026" or "16 February 2026"
        pattern1 = r'(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\s*(\d{4})'
        match = re.search(pattern1, text_lower)
        if match:
            day = int(match.group(1))
            month = month_map.get(match.group(2))
            year = int(match.group(3))
            if month and 1 <= day <= 31:
                return f"{year}-{month:02d}-{day:02d}"

        # Pattern: "16/02/2026" or "16-02-2026"
        pattern2 = r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})'
        match = re.search(pattern2, text)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year}-{month:02d}-{day:02d}"

        # Pattern: "2026-02-16" (ISO format)
        pattern3 = r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})'
        match = re.search(pattern3, text)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year}-{month:02d}-{day:02d}"

        # Handle "tomorrow"
        if 'tomorrow' in text_lower:
            tomorrow = frappe.utils.add_days(frappe.utils.now_datetime(), 1)
            return tomorrow.strftime("%Y-%m-%d")

        return None

    def extract_reason(text, date_str):
        """Extract reason by removing the date part."""
        # Remove common date patterns from text
        reason = text
        # Remove patterns like "16 Feb 2026"
        reason = re.sub(r'\d{1,2}\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\s*\d{4}', '', reason, flags=re.IGNORECASE)
        # Remove patterns like "16/02/2026"
        reason = re.sub(r'\d{1,2}[/-]\d{1,2}[/-]\d{4}', '', reason)
        # Remove "tomorrow"
        reason = re.sub(r'\btomorrow\b', '', reason, flags=re.IGNORECASE)
        # Clean up separators and whitespace
        reason = re.sub(r'^[\s,\-:]+|[\s,\-:]+$', '', reason)
        reason = reason.strip()
        return reason if reason else "Personal leave"

    # First try regex extraction (fast and reliable for common formats)
    extracted_date = parse_date_with_regex(message)
    if extracted_date:
        reason = extract_reason(message, extracted_date)
        frappe.log_error("Leave Date Extraction (Regex)", f"Date: {extracted_date}, Reason: {reason}, Message: {message}")
        return {"date": extracted_date, "reason": reason}

    # Fallback to LLM for complex formats
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.prompts import ChatPromptTemplate

        # Get current date for context
        current_date = frappe.utils.now_datetime().strftime("%Y-%m-%d")

        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=frappe.conf.get("openai_api_key")
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are a date and reason extractor. Today's date is {current_date}.

Extract the leave date and reason from the user's message.

Rules:
1. Convert any date format to YYYY-MM-DD format
2. If only a day is mentioned (e.g., "15th"), assume the current or next month
3. If a date range is mentioned, extract the START date
4. Extract the reason - everything that explains WHY they need leave
5. If no clear reason is given, use "Personal leave" as default

Respond ONLY in this exact JSON format:
{{"date": "YYYY-MM-DD", "reason": "the reason"}}

Examples:
- "15 Feb 2025, family event" → {{"date": "2025-02-15", "reason": "family event"}}
- "tomorrow, not feeling well" → {{"date": "<tomorrow's date>", "reason": "not feeling well"}}
- "10-12 March for wedding" → {{"date": "2025-03-10", "reason": "for wedding"}}"""),
            ("human", "{message}")
        ])

        chain = prompt | llm
        response = chain.invoke({"message": message})

        # Parse the JSON response
        json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            frappe.log_error("Leave Date Extraction (LLM)", f"Result: {result}, Message: {message}")
            return result

        return {"date": None, "reason": message}

    except Exception as e:
        frappe.log_error("Extract Leave Date Error", f"Error: {str(e)}, Message: {message}")
        return {"date": None, "reason": message}


def send_staff_hr_menu(crm_lead_doc, whatsapp_id):
    """
    Send interactive list menu to staff with HR options.

    Menu includes:
    - Clock In
    - Clock Out
    - Annual Leave
    - Medical Leave

    Args:
        crm_lead_doc: CRM Lead document for the staff member
        whatsapp_id: Staff member's WhatsApp ID
    """
    sections = [
        {
            "title": "Attendance",
            "rows": [
                {
                    "id": "register_clock_in",
                    "title": "Register for Clock In",
                    "description": "Register your device for clock in"
                }
            ]
        },
        {
            "title": "Leave Requests",
            "rows": [
                {
                    "id": "leave_annual",
                    "title": "Annual Leave",
                    "description": "Apply for annual leave"
                },
                {
                    "id": "leave_medical",
                    "title": "Medical Leave",
                    "description": "Apply for medical leave (MC)"
                }
            ]
        }
    ]

    enqueue(
        method=send_interactive_list_message_with_delay,
        crm_lead_doc=crm_lead_doc,
        whatsapp_id=whatsapp_id,
        header_text="Staff Menu",
        body_text="Hi! Please select an option from the menu below:",
        footer_text="HR Self-Service",
        button_text="View Menu",
        sections=sections,
        queue="short",
        is_async=True
    )


def handle_outlet_staff_hr(whatsapp_message, crm_lead_doc):
    """
    Handle outlet staff HR clock in/out module.
    Forwards image/location to API which handles merging within 5-min window.

    Args:
        whatsapp_message: The WhatsApp Message document
        crm_lead_doc: The CRM Lead document for the outlet staff
    """
    print("Outlet staff Clock in module")

    content_type = whatsapp_message.content_type
    whatsapp_id = whatsapp_message.get("from")
    message_text = (whatsapp_message.message or "").strip().lower()

    # # Get current log type from cache
    # log_type = get_clock_log_type(crm_lead_doc)

    # Handle "clock in" or "clock out" text messages - send location request
    if content_type == "text":
        # Check if user is in leave application mode
        leave_type = get_leave_application_mode(crm_lead_doc)
        if leave_type:
            # User is providing date and reason for leave
            original_message = (whatsapp_message.message or "").strip()
            leave_type_display = leave_type.replace("_", " ").title()

            # Clear the leave application mode
            set_leave_application_mode(crm_lead_doc, None)

            # Extract date and reason from the message using LLM
            extracted_data = extract_leave_date_and_reason(original_message)
            leave_date = extracted_data.get("date")
            reason = extracted_data.get("reason", original_message)

            # Log the leave request
            frappe.log_error(
                title="Leave Application Request",
                message=f"Staff: {crm_lead_doc.lead_name or crm_lead_doc.first_name}\n"
                        f"WhatsApp ID: {whatsapp_id}\n"
                        f"Leave Type: {leave_type_display}\n"
                        f"Date: {leave_date}\n"
                        f"Reason: {reason}\n"
                        f"Original Message: {original_message}"
            )

            # Call leave application API
            if leave_date:
                try:
                    api_response = handle_leave_application_api(
                        whatsapp_id=whatsapp_id,
                        leave_date=leave_date,
                        reason=reason
                    )

                    # Check API response
                    msg_obj = api_response.get("message", {}) if api_response else {}
                    success = msg_obj.get("success", False) if isinstance(msg_obj, dict) else False
                    response_msg = msg_obj.get("message", "") if isinstance(msg_obj, dict) else str(msg_obj)

                    if success:
                        reply_text = (
                            f"✅ *Leave Request Submitted*\n\n"
                            f"*Type:* {leave_type_display} Leave\n"
                            f"*Date:* {leave_date}\n"
                            f"*Reason:* {reason}\n\n"
                            f"Your leave request has been submitted for approval. "
                            f"You will be notified once it is processed."
                        )
                    else:
                        reply_text = (
                            f"❌ *Leave Request Failed*\n\n"
                            f"{response_msg or 'Unable to submit leave request. Please try again.'}"
                        )
                except Exception as e:
                    frappe.log_error("Leave Application Error", str(e))
                    reply_text = (
                        f"❌ *Leave Request Failed*\n\n"
                        f"An error occurred while submitting your leave request. Please try again."
                    )
            else:
                # Could not extract date from message
                # Generate a sample future date (7 days from now)
                sample_date = frappe.utils.add_days(frappe.utils.now_datetime(), 7)
                sample_date_str = sample_date.strftime("%-d %b %Y")
                reply_text = (
                    f"❌ *Could not process your request*\n\n"
                    f"I couldn't identify the date from your message. "
                    f"Please try again with a clear date format.\n\n"
                    f"Example: '{sample_date_str}, need to attend family event'"
                )

            enqueue(
                method=send_message_with_delay,
                crm_lead_doc=crm_lead_doc,
                whatsapp_id=whatsapp_id,
                text=reply_text,
                queue="short",
                is_async=True
            )
            return
        
        else:
            # Send interactive list menu for any other text message
            send_staff_hr_menu(crm_lead_doc, whatsapp_id)
            return

    if content_type == "image":
        # Check if user is in face registration mode
        if get_face_registration_mode(crm_lead_doc):
            try:
                # Wait for file to be fully saved
                time.sleep(2)

                # Get the file using file_url from whatsapp_message.attach
                # file_url = whatsapp_message.attach
                # file_doc = frappe.get_doc("File", {"file_url": file_url})
                file_content = frappe.flags.file_data
                #print("File content: ", file_content)
                # Use get_content() from the File document
                # file_content = file_doc.get_content()

                # # Convert to base64
                # if isinstance(file_content, str):
                #     file_content = file_content.encode()
                # selfie_base64 = base64.b64encode(file_content).decode('utf-8')

                # print(f"Face registration - Got file content, base64 length: {len(selfie_base64)}")

                # Call face registration API with base64 image
                api_response = handle_register_staff_face_api(
                    mobile_no=whatsapp_id,
                    selfie_image=file_content
                )
                print("Face Registration API Response:", api_response)

                # Clear face registration mode after API call
                set_face_registration_mode(crm_lead_doc, enabled=False)

                # Handle API response
                msg_obj = api_response.get("message", {}) if api_response else {}
                success = msg_obj.get("success", False)
                response_msg = msg_obj.get("message", "Face registration processed.")

                reply_msg = f"{'✅' if success else '❌'} {response_msg}"

                enqueue(
                    method=send_message_with_delay,
                    crm_lead_doc=crm_lead_doc,
                    whatsapp_id=whatsapp_id,
                    text=reply_msg,
                    queue="short",
                    is_async=True
                )

            except Exception as e:
                frappe.log_error(title="Face Registration Error", message=str(e))
                print("Face registration failed:", e)

                # Clear face registration mode on error
                set_face_registration_mode(crm_lead_doc, enabled=False)

                enqueue(
                    method=send_message_with_delay,
                    crm_lead_doc=crm_lead_doc,
                    whatsapp_id=whatsapp_id,
                    text="❌ Failed to register face. Please try again with a clear selfie photo.",
                    queue="short",
                    is_async=True
                )

            return

        # User is not in face registration mode, show menu instead
        send_staff_hr_menu(crm_lead_doc, whatsapp_id)
        return

    # Handle list_reply (staff responded to an interactive list message)
    if content_type == "list_reply":
        handle_interactive_list_reply(
            whatsapp_id,
            crm_lead_doc.lead_name or crm_lead_doc.first_name,
            whatsapp_message.interactive_id,
            whatsapp_message.message,
            crm_lead_doc
        )
        return

def handle_text_message_ai(message, whatsapp_id, customer_name, crm_lead_doc=None):
    """
    Handle incoming text messages with AI-powered responses using RAG.
    Every message goes through the LangChain RAG chain for intelligent responses.
    """

    try:
        print(f"AI message handler started for message: {message[:50]}...", "WhatsApp AI Debug")

        # # Check if AI is enabled in site config
        # ai_enabled = frappe.conf.get("whatsapp_ai_enabled", False)
        # if not ai_enabled:
        #     return

        # Get or create CRM lead if not provided
        if not crm_lead_doc:
            frappe.log_error("Getting CRM lead", "WhatsApp AI Debug")
            crm_lead_doc = get_crm_lead(whatsapp_id, customer_name)

        # Get any pending booking data from previous messages
        pending_data = get_pending_booking_data(crm_lead_doc)

        # Get chat history for context-aware extraction
        from crm.api.whatsapp import get_whatsapp_messages
        chat_history = get_whatsapp_messages("CRM Lead", crm_lead_doc.name)

        # Log for debugging
        frappe.log_error("Booking Flow Debug", f"Pending data for {whatsapp_id}:\n{json.dumps(pending_data, indent=2, default=str)}\n\nChat history messages: {len(chat_history)}")

        # PRIORITY 0: Check if user is just acknowledging a confirmed booking
        # If booking was recently confirmed and user sends simple acknowledgment like "ok thanks", "tq", etc.
        # respond politely without repeating booking details
        booking_was_confirmed = pending_data.get('confirmed', False) if pending_data else False

        if booking_was_confirmed:
            # Use LLM to detect if this is a simple acknowledgment
            from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.agents.rag_chain import detect_yes_no_with_llm
            detection_result = detect_yes_no_with_llm(message)

            # Check if message is acknowledgment (not yes/no response, just thanks/ok)
            message_lower = message.lower().strip()
            acknowledgment_keywords = ['thank', 'thanks', 'tq', 'ty', 'noted', 'got it', 'terima kasih']
            is_acknowledgment = any(keyword in message_lower for keyword in acknowledgment_keywords)

            # If it's a simple acknowledgment or non-yes/no short message (under 20 chars)
            if is_acknowledgment or (detection_result == 'other' and len(message) <= 20):
                frappe.log_error(
                    "Simple Acknowledgment After Booking",
                    f"User sent simple acknowledgment after confirmed booking: {message}\n"
                    f"Detection: {detection_result}\n"
                    f"Sending polite response without repeating booking details"
                )

                # Send simple polite response
                polite_response = "You're welcome! If you need anything else or have questions, feel free to ask. Have a great day! 😊"
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=polite_response, queue="short", is_async=True)
                return

        # PRIORITY 1: Check for CANCEL intent (highest priority, immediate action)
        from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.agents.rag_chain import has_cancel_intent
        user_wants_to_cancel = has_cancel_intent(message)

        if user_wants_to_cancel and pending_data:
            # User wants to cancel their booking
            frappe.log_error("Booking Cancellation", f"User wants to cancel booking for {whatsapp_id}")

            try:
                # Get booking reference from pending data
                booking_reference = pending_data.get('booking_reference')

                # Call mock cancellation API
                cancel_response = handle_cancel_booking_api_mock(crm_lead_doc, whatsapp_id, booking_reference)
                frappe.log_error("Cancellation Response", f"Mock API response: {json.dumps(cancel_response, default=str)}")

                # Clear pending booking data
                clear_pending_booking_data(crm_lead_doc)

                # Send cancellation confirmation
                cancel_msg = f"""❌ Your booking has been cancelled successfully.

Booking Reference: {booking_reference or 'N/A'}
Cancelled At: {cancel_response['data']['cancelled_at']}

If you'd like to make a new booking in the future, just let us know! 💚"""

                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=cancel_msg, queue="short", is_async=True)
                return

            except Exception as cancel_error:
                frappe.log_error("Cancellation Error", f"Error cancelling booking: {str(cancel_error)}\n{frappe.get_traceback()}")
                error_msg = "❌ Sorry, there was an error cancelling your booking. Please contact our outlet directly. Thank you! 🙏"
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=error_msg, queue="short", is_async=True)
                return

        # PRIORITY 2: Check for UPDATE intent using LLM (before new booking flow)
        from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.agents.rag_chain import detect_update_intent_with_llm

        # Only check for update if there's existing confirmed booking data
        # Check if booking was previously confirmed (not just pending)
        booking_was_confirmed = pending_data.get('confirmed', False) if pending_data else False

        # First check if we're waiting for UPDATE confirmation
        if booking_was_confirmed and pending_data.get('awaiting_update_confirmation'):
            frappe.log_error(
                "Awaiting Update Confirmation",
                f"User previously requested update - checking their response\n"
                f"Message: {message}"
            )

            # FIRST: Check if user is asking a general question instead of confirming
            # This allows flexible conversation even while waiting for update confirmation
            if is_general_question(message):
                frappe.log_error(
                    "General Question During Update Confirmation",
                    f"User asked a question while awaiting update confirmation\n"
                    f"Message: {message}\n"
                    f"Keeping awaiting_update_confirmation=True and answering question via RAG"
                )
                # Skip all booking logic - fall through to RAG chain
                # The awaiting_update_confirmation flag stays True for next message

            elif is_confirmation_message(message, context='awaiting_update'):
                # User confirmed the update - proceed with API call
                frappe.log_error("Update Confirmed", f"User confirmed update for {whatsapp_id}")

                try:
                    pending_update_fields = pending_data.get('pending_update_fields', {})
                    booking_reference = pending_data.get('booking_reference')

                    # Apply the updates
                    updated_booking = pending_data.copy()
                    updated_booking.update(pending_update_fields)

                    # Call mock update API
                    update_response = handle_update_booking_api_mock(
                        crm_lead_doc,
                        whatsapp_id,
                        updated_booking,
                        booking_reference
                    )
                    frappe.log_error("Update API Response", f"Mock API response: {json.dumps(update_response, default=str)}")

                    # Save updated booking data
                    updated_booking['confirmed'] = True
                    updated_booking['booking_reference'] = booking_reference
                    updated_booking['awaiting_update_confirmation'] = False  # Clear the flag
                    del updated_booking['pending_update_fields']  # Remove pending fields
                    save_pending_booking_data(crm_lead_doc, updated_booking)

                    # Build success message
                    update_type_label = 'Updated'
                    update_summary = f"""✅ Booking {update_type_label} Successfully!

📋 Updated Booking Details:
- Booking Reference: {booking_reference or 'N/A'}
- Name: {updated_booking.get('customer_name')}
- Phone: {updated_booking.get('phone')}
- Outlet: {updated_booking.get('outlet')}
- Date: {updated_booking.get('booking_date')}
- Time: {updated_booking.get('timeslot')}
- Pax: {updated_booking.get('pax')}
- Treatment: {updated_booking.get('treatment_type')}
- Duration: {updated_booking.get('session')} minutes
- Preferred Masseur: {updated_booking.get('preferred_masseur')}

Thank you for updating your booking with HealthLand! 💚"""

                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=update_summary, queue="short", is_async=True)
                    return

                except Exception as update_error:
                    frappe.log_error("Update Error", f"Error updating booking: {str(update_error)}\n{frappe.get_traceback()}")
                    error_msg = "❌ Sorry, there was an error updating your booking. Please contact our outlet directly or try again. Thank you! 🙏"
                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=error_msg, queue="short", is_async=True)
                    return

            elif is_change_request(message, context='awaiting_update'):
                # User said no - cancel the update
                frappe.log_error("Update Cancelled", f"User cancelled update for {whatsapp_id}")

                # Clear the update flags
                pending_data['awaiting_update_confirmation'] = False
                if 'pending_update_fields' in pending_data:
                    del pending_data['pending_update_fields']
                save_pending_booking_data(crm_lead_doc, pending_data)

                cancel_msg = "No problem! Your booking update has been cancelled. Your original booking details remain unchanged. If you'd like to try updating again, just let me know! 😊"
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=cancel_msg, queue="short", is_async=True)
                return

            else:
                # Unclear response - remind them
                reminder_msg = "Please reply with 'Yes' to confirm the update, or 'No' to cancel. 🙏"
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=reminder_msg, queue="short", is_async=True)
                return

        if booking_was_confirmed:
            update_detection = detect_update_intent_with_llm(chat_history, message, pending_data)

            if update_detection['is_update']:
                # User wants to update their existing booking
                frappe.log_error(
                    "Booking Update",
                    f"User wants to update booking for {whatsapp_id}\n"
                    f"Update type: {update_detection['update_type']}\n"
                    f"Updated fields: {json.dumps(update_detection['updated_fields'], indent=2, default=str)}"
                )

                # Check if user specified WHAT to update
                updated_fields = update_detection.get('updated_fields', {})

                if not updated_fields or len(updated_fields) == 0:
                    # User wants to update but didn't specify what to change
                    # Ask them what they want to update
                    frappe.log_error(
                        "Update Request - No Fields Specified",
                        f"User said they want to update but didn't specify which fields\n"
                        f"Asking user what they want to update"
                    )

                    current_booking_summary = f"""Sure! I can help you update your booking.

📋 Your Current Booking:
- Booking Reference: {pending_data.get('booking_reference', 'N/A')}
- Name: {pending_data.get('customer_name')}
- Phone: {pending_data.get('phone')}
- Outlet: {pending_data.get('outlet')}
- Date: {pending_data.get('booking_date')}
- Time: {pending_data.get('timeslot')}
- Pax: {pending_data.get('pax')}
- Treatment: {pending_data.get('treatment_type')}
- Duration: {pending_data.get('session')} minutes
- Preferred Masseur: {pending_data.get('preferred_masseur')}

What would you like to change? You can update:
• Date and/or Time
• Number of people (Pax)
• Treatment type
• Duration
• Masseur preference
• Outlet location
• Or any other detail

Please let me know what you'd like to update! 😊"""

                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=current_booking_summary, queue="short", is_async=True)
                    return

                try:
                    # Merge existing booking with updated fields
                    updated_booking = pending_data.copy()
                    updated_booking.update(updated_fields)

                    # Validate timeslot if it was updated
                    if 'timeslot' in updated_fields:
                        timeslot_validation = validate_booking_timeslot(updated_booking.get('timeslot'))

                        if not timeslot_validation['valid']:
                            # Timeslot is outside operating hours - inform customer
                            frappe.log_error(
                                "Invalid Update Time",
                                f"Customer tried to update booking to {updated_booking.get('timeslot')} which is outside operating hours\n"
                                f"Sending message: {timeslot_validation['message']}"
                            )

                            # Send the validation error message
                            enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=timeslot_validation['message'], queue="short", is_async=True)
                            return

                    # Get booking reference
                    booking_reference = pending_data.get('booking_reference')

                    # Show confirmation of the UPDATE before calling API
                    # Build list of what changed
                    changes_list = []
                    for field, new_value in updated_fields.items():
                        old_value = pending_data.get(field)
                        field_labels = {
                            'booking_date': 'Date',
                            'timeslot': 'Time',
                            'outlet': 'Outlet',
                            'pax': 'Number of People',
                            'treatment_type': 'Treatment',
                            'session': 'Duration',
                            'preferred_masseur': 'Masseur Preference',
                            'customer_name': 'Name',
                            'phone': 'Phone'
                        }
                        field_label = field_labels.get(field, field)
                        changes_list.append(f"• {field_label}: {old_value} → {new_value}")

                    changes_summary = "\n".join(changes_list)

                    update_confirmation_msg = f"""📋 Please confirm your booking update:

🔄 Changes:
{changes_summary}

📋 Updated Booking Details:
- Booking Reference: {booking_reference or 'N/A'}
- Name: {updated_booking.get('customer_name')}
- Phone: {updated_booking.get('phone')}
- Outlet: {updated_booking.get('outlet')}
- Date: {updated_booking.get('booking_date')}
- Time: {updated_booking.get('timeslot')}
- Pax: {updated_booking.get('pax')}
- Treatment: {updated_booking.get('treatment_type')}
- Duration: {updated_booking.get('session')} minutes
- Preferred Masseur: {updated_booking.get('preferred_masseur')}

Is this correct? Please reply:
✅ *Yes* to confirm update
❌ *No* to remain unchanged"""

                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=update_confirmation_msg, queue="short", is_async=True)

                    # Save update intent for next message
                    updated_booking['awaiting_update_confirmation'] = True
                    updated_booking['pending_update_fields'] = updated_fields
                    save_pending_booking_data(crm_lead_doc, updated_booking)

                    frappe.log_error(
                        "Update Confirmation Shown",
                        f"Showing update confirmation to user\n"
                        f"Changes: {changes_summary}\n"
                        f"Waiting for user to confirm"
                    )
                    return

                except Exception as update_error:
                    frappe.log_error("Update Error", f"Error updating booking: {str(update_error)}\n{frappe.get_traceback()}")
                    error_msg = "❌ Sorry, there was an error updating your booking. Please contact our outlet directly or try again. Thank you! 🙏"
                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=error_msg, queue="short", is_async=True)
                    return

        # PRIORITY 3: Normal booking flow (new bookings)
        # Check if we should trigger booking flow:
        # 1. User expresses booking intent in LAST 3 MESSAGES (e.g., "I want to book")
        # 2. Message contains specific booking details
        # 3. We have pending booking data from previous interaction
        # 4. IMPORTANT: Allow users to ask general questions even with pending booking data

        # Use new intent detection that only checks last 3 messages to avoid false positives
        user_wants_to_book = detect_booking_intent_from_recent_context(chat_history, message)
        has_specific_details = is_booking_details_message(message)
        has_pending_data = bool(pending_data and len(pending_data) > 0 and not booking_was_confirmed)
        is_asking_question = is_general_question(message)

        frappe.log_error(
            "Booking Flow Debug",
            f"Booking triggers - Intent (last 3 msgs): {user_wants_to_book}, Details: {has_specific_details}, Pending: {has_pending_data}, Question: {is_asking_question}"
        )

        # If user is asking a general question, skip booking flow and let RAG handle it
        if is_asking_question and not user_wants_to_book and not has_specific_details:
            frappe.log_error(
                "General Question Detected",
                f"User is asking a general question while in booking flow\n"
                f"Message: {message}\n"
                f"Skipping booking flow, letting RAG chain handle it"
            )
            # DON'T trigger booking flow - fall through to RAG chain below

        # Trigger booking flow if ANY of the above conditions are met (and not asking general question)
        elif user_wants_to_book or has_specific_details or has_pending_data:
            print("Booking flow triggered - extracting from conversation history", "WhatsApp AI Debug")

            # Extract booking information using LLM + regex
            # LLM scans ENTIRE conversation history to find all mentioned fields
            # This allows "I want to book" to work even if details were mentioned earlier
            extraction_result = extract_booking_details(message, pending_data, chat_history)
            booking_data = extraction_result['data']
            missing_fields = extraction_result['missing_fields']
            is_complete = extraction_result['is_complete']

            frappe.log_error(
                "Booking Extraction Debug",
                f"Booking Intent: {user_wants_to_book}\n"
                f"Current Message: {message}\n"
                f"Extracted from conversation: {json.dumps(booking_data, indent=2, default=str)}\n"
                f"Missing fields: {missing_fields}\n"
                f"Complete: {is_complete}"
            )
            print(f"Extracted data from conversation: {json.dumps(booking_data, default=str)}", "WhatsApp Booking Debug")
            print(f"Missing fields: {missing_fields}", "WhatsApp Booking Debug")

            if is_complete:
                # All required fields are present
                # FIRST: Validate that the timeslot is within operating hours
                timeslot_validation = validate_booking_timeslot(booking_data.get('timeslot'))

                if not timeslot_validation['valid']:
                    # Timeslot is outside operating hours - inform customer and ask for new time
                    frappe.log_error(
                        "Invalid Booking Time",
                        f"Customer tried to book at {booking_data.get('timeslot')} which is outside operating hours\n"
                        f"Sending message: {timeslot_validation['message']}"
                    )

                    # Keep the booking data but mark timeslot as missing
                    booking_data.pop('timeslot', None)  # Remove invalid timeslot
                    save_pending_booking_data(crm_lead_doc, booking_data)

                    # Send the validation error message
                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=timeslot_validation['message'], queue="short", is_async=True)
                    return

                # CRITICAL: Detect if booking details have CHANGED (new booking vs existing booking)
                # If details changed, user needs to see NEW confirmation even if they confirmed before

                # Compare key booking fields to detect if this is a NEW booking
                booking_changed = False
                if pending_data and pending_data.get('awaiting_confirmation'):
                    # Check if critical fields have changed
                    key_fields = ['booking_date', 'timeslot', 'outlet', 'pax', 'treatment_type', 'session']
                    for field in key_fields:
                        old_value = pending_data.get(field)
                        new_value = booking_data.get(field)
                        if old_value != new_value:
                            booking_changed = True
                            frappe.log_error(
                                "Booking Details Changed",
                                f"Field '{field}' changed:\n"
                                f"Old: {old_value}\n"
                                f"New: {new_value}\n"
                                f"This is a NEW booking - must show confirmation again!"
                            )
                            break

                # Determine if we're waiting for confirmation
                # If booking details changed, RESET awaiting_confirmation to False
                if booking_changed:
                    awaiting_confirmation = False  # NEW booking - need to show confirmation
                    frappe.log_error(
                        "New Booking Detected",
                        f"Booking details changed - resetting awaiting_confirmation to False\n"
                        f"User MUST confirm the new booking details"
                    )
                else:
                    awaiting_confirmation = pending_data.get('awaiting_confirmation', False) if pending_data else False

                frappe.log_error(
                    "Booking Confirmation Flow - IMPORTANT",
                    f"⭐ Booking is COMPLETE! ⭐\n"
                    f"Booking changed since last confirmation: {booking_changed}\n"
                    f"awaiting_confirmation flag: {awaiting_confirmation}\n"
                    f"Current message: {message}\n"
                    f"Will show confirmation?: {not awaiting_confirmation}\n"
                    f"Will check user response?: {awaiting_confirmation}"
                )

                # FIRST: Check if user is asking a general question instead of confirming
                # This allows flexible conversation even while waiting for confirmation
                if is_general_question(message) and awaiting_confirmation:
                    frappe.log_error(
                        "General Question During Confirmation",
                        f"User asked a question while awaiting confirmation\n"
                        f"Message: {message}\n"
                        f"Keeping awaiting_confirmation=True and answering question via RAG"
                    )
                    # Skip all booking logic - fall through to RAG chain
                    # The awaiting_confirmation flag stays True for next message

                elif awaiting_confirmation:
                    # We already showed details, now check user's response
                    # IMPORTANT: Only treat as confirmation/change if message is CLEARLY about booking
                    # Not if it's asking about something else (e.g., "ya share me the link")

                    message_lower = message.lower().strip()

                    # Check if message is clearly about something OTHER than booking confirmation
                    # e.g., "ya share me the link", "yes tell me about packages", etc.
                    non_confirmation_patterns = [
                        'link', 'share', 'send', 'tell me', 'what', 'where', 'how', 'when',
                        'outlet', 'location', 'address', 'price', 'package', 'promotion',
                        'discount', 'treatment', 'massage', 'service', 'available'
                    ]

                    # If message contains these keywords, it's likely NOT a booking confirmation
                    is_about_other_topic = any(pattern in message_lower for pattern in non_confirmation_patterns)

                    if is_about_other_topic:
                        # User is asking about something else, not confirming booking
                        # Treat as general question and answer via RAG
                        frappe.log_error(
                            "Non-Confirmation Message During Confirmation Wait",
                            f"User said something that's not a booking confirmation: {message}\n"
                            f"Treating as general question and keeping awaiting_confirmation=True"
                        )
                        # Skip all booking confirmation logic - fall through to RAG chain
                        # awaiting_confirmation stays True for when they're ready to confirm

                    else:
                        # Message is NOT about other topics, so check if it's a confirmation response
                        if is_confirmation_message(message, context='awaiting_confirmation'):
                            # User confirmed - proceed with booking
                            frappe.log_error("Booking Confirmation", f"User confirmed booking for {whatsapp_id}")

                            # CRITICAL SAFETY CHECK: Verify that awaiting_confirmation was previously set
                            # This ensures confirmation was shown before calling API
                            if not pending_data or not pending_data.get('awaiting_confirmation'):
                                frappe.log_error(
                                    "⚠️ API CALL BLOCKED ⚠️",
                                    f"CRITICAL: Attempted to call API without proper confirmation!\n"
                                    f"awaiting_confirmation flag not found in pending_data\n"
                                    f"Showing confirmation now to be safe"
                                )
                                # Show confirmation now as safety measure
                                booking_data['awaiting_confirmation'] = True
                                save_pending_booking_data(crm_lead_doc, booking_data)

                                safety_msg = f"""📋 Please confirm your booking details before we proceed:

- Name: {booking_data.get('customer_name')}
- Phone: {booking_data.get('phone')}
- Outlet: {booking_data.get('outlet')}
- Date: {booking_data.get('booking_date')}
- Time: {booking_data.get('timeslot')}
- Pax: {booking_data.get('pax')}
- Treatment: {booking_data.get('treatment_type')}
- Duration: {booking_data.get('session')} minutes
- Preferred Masseur: {booking_data.get('preferred_masseur')}

Is everything correct? Please reply:
✅ *Yes* to confirm
❌ *No* to make changes"""

                                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=safety_msg, queue="short", is_async=True)
                                return

                            # Log booking details one more time before API call for verification
                            frappe.log_error(
                                "📋 CALLING API - BOOKING DETAILS",
                                f"User has confirmed. Calling booking API with:\n"
                                f"{json.dumps(booking_data, indent=2, default=str)}\n"
                                f"Confirmation was previously shown: {pending_data.get('awaiting_confirmation') == True}"
                            )

#                             # Send "Processing..." message showing booking details one more time
#                             processing_msg = f"""⏳ Processing your booking...

# 📋 Booking Details Being Submitted:
# - Name: {booking_data.get('customer_name')}
# - Phone: {booking_data.get('phone')}
# - Outlet: {booking_data.get('outlet')}
# - Date: {booking_data.get('booking_date')}
# - Time: {booking_data.get('timeslot')}
# - Pax: {booking_data.get('pax')}
# - Treatment: {booking_data.get('treatment_type')}
# - Duration: {booking_data.get('session')} minutes

# Please wait while we confirm your appointment..."""

#                             enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=processing_msg, queue="short", is_async=True)

#                             # Small delay to ensure processing message is sent before API call
#                             import time
#                             time.sleep(1)

                            try:
                                # Call the MOCK booking API (for POC/simulation)
                                api_response = handle_booking_api_mock(crm_lead_doc, whatsapp_id, booking_data)
                                print(f"Mock Booking API response: {json.dumps(api_response, default=str)}", "WhatsApp Booking Debug")

                                # Get booking reference from mock API response
                                booking_reference = api_response.get('data', {}).get('booking_reference', f"BKG{frappe.utils.now_datetime().strftime('%Y%m%d%H%M%S')}")

                                # Save confirmed booking data (don't clear it - needed for update/cancel)
                                confirmed_booking_data = booking_data.copy()
                                confirmed_booking_data['confirmed'] = True
                                confirmed_booking_data['booking_reference'] = booking_reference
                                confirmed_booking_data['confirmed_at'] = frappe.utils.now_datetime().isoformat()
                                save_pending_booking_data(crm_lead_doc, confirmed_booking_data)

                                # Always build booking details summary to show to customer
                                booking_summary = f"""📋 Your Booking Details:
- Booking Reference: {booking_reference}
- Name: {booking_data.get('customer_name')}
- Phone: {booking_data.get('phone')}
- Outlet: {booking_data.get('outlet')}
- Date: {booking_data.get('booking_date')}
- Time: {booking_data.get('timeslot')}
- Pax: {booking_data.get('pax')}
- Treatment: {booking_data.get('treatment_type')}
- Duration: {booking_data.get('session')} minutes
- Preferred Masseur: {booking_data.get('preferred_masseur')}
- 3rd Party Voucher: {booking_data.get('third_party_voucher', 'N/A')}
- Using Package: {booking_data.get('using_package', 'N/A')}"""

                                # Combine API response message (if any) with booking details
                                if api_response and api_response.get('message'):
                                    confirmation_msg = f"✅ Booking confirmed!\n\n{booking_summary}\n\n{api_response.get('message')}\n\n💡 Tip: You can update or cancel your booking anytime by sending us a message!\n\nThank you for choosing HealthLand! 💚"
                                else:
                                    confirmation_msg = f"✅ Your booking has been submitted!\n\n{booking_summary}\n\nWe'll confirm your appointment shortly.\n\n💡 Tip: You can update or cancel your booking anytime by sending us a message!\n\nThank you! 💚"

                                print(confirmation_msg)
                                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=str(confirmation_msg), queue="short", is_async=True)

                                frappe.log_error(
                                    "WhatsApp Booking Success",
                                    f"Booking processed successfully\nCustomer: {customer_name}\nDetails: {json.dumps(booking_data, indent=2, default=str)}"
                                )
                                return  # Exit after handling booking

                            except Exception as booking_error:
                                frappe.log_error(
                                    "WhatsApp Booking Error",
                                    f"Booking API error: {str(booking_error)}\nTraceback: {frappe.get_traceback()}"
                                )
                                # Clear pending data on error
                                clear_pending_booking_data(crm_lead_doc)
                                # Send error message to customer
                                error_msg = "❌ Sorry, there was an error processing your booking. Please contact our outlet directly or try again later. Thank you for your patience! 🙏"
                                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=error_msg, queue="short", is_async=True)
                                return

                        elif is_change_request(message, context='awaiting_confirmation'):
                            # User's response contains change-related keywords (e.g., "no", "change", "wrong")
                            # Use LLM to intelligently determine if they want to:
                            # 1. Update specific fields with provided values (e.g., "no change name to duxton"), OR
                            # 2. Make changes but haven't specified what (e.g., just "no")

                            frappe.log_error("Booking Confirmation", f"Analyzing user intent for change request: {whatsapp_id}")

                            intent_analysis = analyze_confirmation_response_intent(message, booking_data)
                            intent = intent_analysis.get('intent')
                            field_updates = intent_analysis.get('field_updates', {})

                            if intent == 'update_fields' and field_updates:
                                # User provided specific field updates - apply them automatically
                                frappe.log_error(
                                    "Field Updates Detected",
                                    f"User wants to update fields:\n{json.dumps(field_updates, indent=2)}"
                                )

                                # Apply field updates to booking data
                                for field, new_value in field_updates.items():
                                    if field in booking_data:
                                        old_value = booking_data.get(field)
                                        booking_data[field] = new_value
                                        frappe.log_error(
                                            "Field Updated",
                                            f"Updated {field}: '{old_value}' → '{new_value}'"
                                        )

                                # Keep awaiting_confirmation = True, show updated details
                                booking_data['awaiting_confirmation'] = True
                                save_pending_booking_data(crm_lead_doc, booking_data)

                                # Show updated booking details
                                updated_summary = f"""✅ Updated! Here are your revised booking details:

- Name: {booking_data.get('customer_name')}
- Phone: {booking_data.get('phone')}
- Outlet: {booking_data.get('outlet')}
- Date: {booking_data.get('booking_date')}
- Time: {booking_data.get('timeslot')}
- Pax: {booking_data.get('pax')}
- Treatment: {booking_data.get('treatment_type')}
- Duration: {booking_data.get('session')} minutes
- Preferred Masseur: {booking_data.get('preferred_masseur')}
- 3rd Party Voucher: {booking_data.get('third_party_voucher', 'N/A')}
- Using Package: {booking_data.get('using_package', 'N/A')}

Is everything correct now? Please reply:
✅ *Yes* to confirm
❌ *No* to make more changes"""

                                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=updated_summary, queue="short", is_async=True)
                                return

                            else:
                                # User wants to make changes but didn't provide specific field values
                                # Ask them what they'd like to change
                                frappe.log_error("Booking Confirmation", f"User wants to make changes (no specific updates provided) for {whatsapp_id}")
                                booking_data['awaiting_confirmation'] = False
                                save_pending_booking_data(crm_lead_doc, booking_data)

                                change_msg = "No problem! What would you like to change? Please let me know which details need to be updated. 😊"
                                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=change_msg, queue="short", is_async=True)
                                return

                else:
                    # First time all fields are complete - show details and ask for confirmation
                    # ⭐ CRITICAL: This is where we ask user to confirm BEFORE calling API
                    frappe.log_error(
                        "⭐ SHOWING CONFIRMATION TO USER ⭐",
                        f"ALL FIELDS COMPLETE - ASKING USER TO CONFIRM\n"
                        f"WhatsApp ID: {whatsapp_id}\n"
                        f"Customer will see confirmation message asking Yes/No\n"
                        f"API will NOT be called until user replies 'Yes'\n"
                        f"Setting awaiting_confirmation = True"
                    )

                    booking_summary = f"""📋 Please confirm your booking details:

- Name: {booking_data.get('customer_name')}
- Phone: {booking_data.get('phone')}
- Outlet: {booking_data.get('outlet')}
- Date: {booking_data.get('booking_date')}
- Time: {booking_data.get('timeslot')}
- Pax: {booking_data.get('pax')}
- Treatment: {booking_data.get('treatment_type')}
- Duration: {booking_data.get('session')} minutes
- Preferred Masseur: {booking_data.get('preferred_masseur')}
- 3rd Party Voucher: {booking_data.get('third_party_voucher', 'N/A')}
- Using Package: {booking_data.get('using_package', 'N/A')}

Is everything correct? Please reply:
✅ *Yes* to confirm
❌ *No* to make changes"""

                    # Save with awaiting_confirmation flag - THIS PREVENTS CALLING API UNTIL USER CONFIRMS
                    booking_data['awaiting_confirmation'] = True
                    save_pending_booking_data(crm_lead_doc, booking_data)

                    frappe.log_error(
                        "Confirmation Message Queued",
                        f"Sending confirmation message to {whatsapp_id}\n"
                        f"Message: {booking_summary}\n"
                        f"Awaiting_confirmation saved: True"
                    )

                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=booking_summary, queue="short", is_async=True)
                    return  # EXIT WITHOUT CALLING API - Wait for user response

            else:
                # Some fields are missing - save data and ask for missing fields
                save_pending_booking_data(crm_lead_doc, booking_data)
                
                # Generate intelligent message asking for missing fields using LLM
                missing_msg = generate_smart_missing_fields_prompt(
                    chat_history=chat_history,
                    current_message=message,
                    extracted_data=booking_data,
                    missing_fields=missing_fields
                )
                frappe.log_error("WhatsApp Booking Debug", f"Missing fields: {missing_fields}\nGenerated prompt:\n{missing_msg}")
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=missing_msg, queue="short", is_async=True)
                return

        # Initialize RAG chain and get chat history
        print("Calling get_rag_chain()", "WhatsApp AI Debug")
        rag_chain, chat_history = get_rag_chain(crm_lead_doc.name)
        print("RAG chain retrieved successfully", "WhatsApp AI Debug")

        # Get AI response with conversation history
        print(f"Invoking RAG chain with message: {message[:50]}...", "WhatsApp AI Debug")
        response = rag_chain.invoke({"input": message, "chat_history": chat_history})
        ai_answer = response.get("answer", "")
        print(f"AI response received: {ai_answer[:100]}...", "WhatsApp AI Debug")

        if ai_answer:
            # Clean markdown formatting and remove duplicate links
            from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.agents.rag_chain import (
                clean_message_formatting,
                detect_and_remove_hallucinated_addresses,
                remove_soma_mentions
            )
            ai_answer = clean_message_formatting(ai_answer)
            print(f"AI response cleaned (formatting): {ai_answer[:100]}...", "WhatsApp AI Debug")

            # Remove any hallucinated addresses, phone numbers, or specific details
            ai_answer = detect_and_remove_hallucinated_addresses(ai_answer)
            print(f"AI response cleaned (hallucinations): {ai_answer[:100]}...", "WhatsApp AI Debug")

            # Remove SOMA mentions unless user asked about SOMA
            ai_answer = remove_soma_mentions(ai_answer, message)
            print(f"AI response cleaned (SOMA filter): {ai_answer[:100]}...", "WhatsApp AI Debug")

            # Validate and correct outlet information against outlet_data.json
            ai_answer = validate_and_correct_outlet_info(ai_answer)
            print(f"AI response validated (outlet info): {ai_answer[:100]}...", "WhatsApp AI Debug")

            # Send AI response back to user
            print("Enqueuing AI response", "WhatsApp AI Debug")
            enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=ai_answer, queue="short", is_async=True)

            # Removed: Confirmation reminder after answering questions
            # Users don't want to be reminded about pending bookings after asking questions

            # Log the interaction for monitoring
            print(
                f"User: {message}\nAI: {ai_answer}",
                "WhatsApp AI Conversation"
            )
        else:
            print("AI response was empty", "WhatsApp AI Debug")

    except Exception as e:
        # Log error but don't break the flow
        print(
            f"Error in AI message handling: {str(e)}\nMessage: {message}\nTraceback: {frappe.get_traceback()}",
            "WhatsApp AI Error"
        )

def handle_text_message(message, whatsapp_id, customer_name, crm_lead_doc=None):
    if not crm_lead_doc:
        crm_lead_doc = get_crm_lead(whatsapp_id, customer_name)

    integration_keyword_settings = frappe.get_single("Integration Keyword Settings")

    if integration_keyword_settings.register_as_member_keyword and integration_keyword_settings.register_as_member_keyword in message and not crm_lead_doc.agree_pdpa:
        create_crm_lead_assignment(crm_lead_doc.name, "BookingHL", "Completed")
        send_interactive_message(crm_lead_doc, whatsapp_id, PDPA_MESSAGE, PDPA_BUTTON)

    if integration_keyword_settings.request_membership_rate_keyword and integration_keyword_settings.request_membership_rate_keyword in message:
        handle_membership_rate_request(crm_lead_doc, whatsapp_id)
    elif integration_keyword_settings.free_membership_redemption_keyword and integration_keyword_settings.free_membership_redemption_keyword in message:
        handle_free_membership_redemption(crm_lead_doc, whatsapp_id, message)
    elif integration_keyword_settings.checkout_login_keyword and integration_keyword_settings.checkout_login_keyword in message:
        handle_checkout_login(crm_lead_doc, whatsapp_id, message)
    elif message.isdigit() and len(message) == 6:
        handle_checkout_login(crm_lead_doc, whatsapp_id, message)
    elif integration_keyword_settings.registration_keyword and integration_keyword_settings.registration_keyword in message:
        handle_registration(crm_lead_doc, whatsapp_id, message)
    elif integration_keyword_settings.reset_password_keyword and integration_keyword_settings.reset_password_keyword in message:
        handle_reset_password(crm_lead_doc, whatsapp_id, message)
    elif message.isdigit() and crm_lead_doc.latest_whatsapp_message_templates:
        whatsapp_message_template_doc = frappe.get_doc("WhatsApp Message Templates", crm_lead_doc.latest_whatsapp_message_templates)
        for whatsapp_message_template_button in whatsapp_message_template_doc.whatsapp_message_template_buttons:
            if message == whatsapp_message_template_button.button_label:
                frappe.flags.skip_lead_status_update = True
                create_crm_lead_assignment(crm_lead_doc.name, whatsapp_message_template_doc.name)
                create_crm_tagging_assignment(crm_lead_doc.name, whatsapp_message_template_doc.tagging)
                if whatsapp_message_template_button.reply_if_button_clicked:
                    if whatsapp_message_template_button.reply_image:
                        enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_message_template_button.reply_if_button_clicked, image=whatsapp_message_template_button.reply_image, queue="short", is_async=True)
                    else:
                        enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_message_template_button.reply_if_button_clicked, queue="short", is_async=True)
                if whatsapp_message_template_button.reply_2_if_button_clicked:
                    if whatsapp_message_template_button.reply_image_2:
                        enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_message_template_button.reply_2_if_button_clicked, image=whatsapp_message_template_button.reply_image_2, queue="short", is_async=True)
                    else:
                        enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_message_template_button.reply_2_if_button_clicked, queue="short", is_async=True)
                if whatsapp_message_template_button.reply_whatsapp_interaction_if_button_clicked:
                    enqueue(method=send_interaction_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, whatsapp_interaction_message_template=whatsapp_message_template_button.reply_whatsapp_interaction_if_button_clicked, queue="short", is_async=True)
                return
    elif message.isdigit() and crm_lead_doc.latest_whatsapp_interaction_message_templates:
        whatsapp_interaction_message_template_doc = frappe.get_doc("WhatsApp Interaction Message Templates", crm_lead_doc.latest_whatsapp_interaction_message_templates)
        for whatsapp_interaction_message_template_button in whatsapp_interaction_message_template_doc.whatsapp_interaction_message_template_buttons:
            if message == whatsapp_interaction_message_template_button.button_label:
                frappe.flags.skip_lead_status_update = True
                create_crm_lead_assignment(crm_lead_doc.name, whatsapp_interaction_message_template_button.whatsapp_message_templates)
                create_crm_tagging_assignment(crm_lead_doc.name, whatsapp_interaction_message_template_button.tagging)
                if whatsapp_interaction_message_template_button.reply_if_button_clicked and (whatsapp_interaction_message_template_button.reply_id != "book-appointment" or (whatsapp_interaction_message_template_button.reply_id == "book-appointment" and not is_not_within_booking_hours())):
                    if whatsapp_interaction_message_template_button.reply_image:
                        enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_interaction_message_template_button.reply_if_button_clicked, image=whatsapp_interaction_message_template_button.reply_image, queue="short", is_async=True)
                    else:
                        enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_interaction_message_template_button.reply_if_button_clicked, queue="short", is_async=True)
                if whatsapp_interaction_message_template_button.reply_2_if_button_clicked:
                    if whatsapp_interaction_message_template_button.reply_image_2:
                        enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_interaction_message_template_button.reply_2_if_button_clicked, image=whatsapp_interaction_message_template_button.reply_image_2, queue="short", is_async=True)
                    else:
                        enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_interaction_message_template_button.reply_2_if_button_clicked, queue="short", is_async=True)
                if whatsapp_interaction_message_template_button.send_out_of_working_hours_message and is_not_within_operating_hours():
                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=OUT_OF_WORKING_HOURS_MESSAGE, queue="short", is_async=True)
                if whatsapp_interaction_message_template_button.send_out_of_booking_hours_message and is_not_within_booking_hours():
                    if not frappe.db.exists("Booking Follow Up", {"crm_lead": crm_lead_doc.name}):
                        frappe.get_doc({
                            "doctype": "Booking Follow Up",
                            "whatsapp_id": whatsapp_id,
                            "crm_lead": crm_lead_doc.name
                        }).insert(ignore_permissions=True)
                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=OUT_OF_BOOKING_HOURS_MESSAGE, queue="short", is_async=True)
                return
    else:
        text_auto_replies = frappe.db.get_all("Text Auto Reply", filters={"disabled": 0, "keyword": message}, fields=["*"])
        if not text_auto_replies:
            keywords = [
                "book" in message.lower(),
                "slot" in message.lower(),
                "date" in message.lower() and "time" in message.lower(),
                "cancel" in message.lower(),
            ]
            unknown_and_promotion_taggings = frappe.db.get_all("CRM Lead Tagging", filters={"crm_lead": crm_lead_doc.name, "tagging": ["in", ["Unknown", "Promotion"]], "status": "Open"}, pluck="name")
            if not crm_lead_doc.last_reply_at or crm_lead_doc.last_reply_at < add_to_date(get_datetime(), days=-1) or crm_lead_doc.closed:
                text_auto_replies = frappe.db.get_all("Text Auto Reply", filters={"disabled": 0, "name": "BookingHL"}, fields=["*"])
        if text_auto_replies:
            frappe.flags.update_conversation_start_at = True
            frappe.flags.skip_lead_status_update = True
            create_crm_lead_assignment(crm_lead_doc.name, text_auto_replies[0].whatsapp_message_templates)
            create_crm_tagging_assignment(crm_lead_doc.name, text_auto_replies[0].tagging)
            if text_auto_replies[0].reply_if_button_clicked and (text_auto_replies[0].name != "BookingHL" or (text_auto_replies[0].name == "BookingHL" and not is_not_within_booking_hours())):
                if text_auto_replies[0].reply_image:
                    enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=text_auto_replies[0].reply_if_button_clicked, image=text_auto_replies[0].reply_image, queue="short", is_async=True)
                else:
                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=text_auto_replies[0].reply_if_button_clicked, queue="short", is_async=True)
            if text_auto_replies[0].reply_2_if_button_clicked:
                if text_auto_replies[0].reply_image_2:
                    enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=text_auto_replies[0].reply_2_if_button_clicked, image=text_auto_replies[0].reply_image_2, queue="short", is_async=True)
                else:
                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=text_auto_replies[0].reply_2_if_button_clicked, queue="short", is_async=True)
            if text_auto_replies[0].whatsapp_interaction_message_templates:
                enqueue(method=send_interaction_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, whatsapp_interaction_message_template=text_auto_replies[0].whatsapp_interaction_message_templates, queue="short", is_async=True)
            if text_auto_replies[0].send_out_of_working_hours_message and is_not_within_operating_hours():
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=OUT_OF_WORKING_HOURS_MESSAGE, queue="short", is_async=True)
            if text_auto_replies[0].send_out_of_booking_hours_message and is_not_within_booking_hours():
                if not frappe.db.exists("Booking Follow Up", {"crm_lead": crm_lead_doc.name}):
                    frappe.get_doc({
                        "doctype": "Booking Follow Up",
                        "whatsapp_id": whatsapp_id,
                        "crm_lead": crm_lead_doc.name
                    }).insert(ignore_permissions=True)
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=OUT_OF_BOOKING_HOURS_MESSAGE, queue="short", is_async=True)
        elif not crm_lead_doc.last_reply_at or crm_lead_doc.last_reply_at < add_to_date(get_datetime(), days=-1) or crm_lead_doc.closed:
            text_auto_replies = frappe.db.get_all("Text Auto Reply", filters={"disabled": 0, "name": "automated_message"}, fields=["*"])
            if text_auto_replies:
                frappe.flags.update_conversation_start_at = True
                frappe.flags.skip_lead_status_update = True
                create_crm_lead_assignment(crm_lead_doc.name, text_auto_replies[0].whatsapp_message_templates)
                create_crm_tagging_assignment(crm_lead_doc.name, "Unknown")
                if text_auto_replies[0].reply_if_button_clicked:
                    if text_auto_replies[0].reply_image:
                        enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=text_auto_replies[0].reply_if_button_clicked, image=text_auto_replies[0].reply_image, queue="short", is_async=True)
                    else:
                        enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=text_auto_replies[0].reply_if_button_clicked, queue="short", is_async=True)
                if text_auto_replies[0].reply_2_if_button_clicked:
                    if text_auto_replies[0].reply_image_2:
                        enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=text_auto_replies[0].reply_2_if_button_clicked, image=text_auto_replies[0].reply_image_2, queue="short", is_async=True)
                    else:
                        enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=text_auto_replies[0].reply_2_if_button_clicked, queue="short", is_async=True)
                if text_auto_replies[0].whatsapp_interaction_message_templates:
                    enqueue(method=send_interaction_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, whatsapp_interaction_message_template=text_auto_replies[0].whatsapp_interaction_message_templates, queue="short", is_async=True)
                if text_auto_replies[0].send_out_of_working_hours_message and is_not_within_operating_hours():
                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=OUT_OF_WORKING_HOURS_MESSAGE, queue="short", is_async=True)
                if text_auto_replies[0].send_out_of_booking_hours_message and is_not_within_booking_hours():
                    if not frappe.db.exists("Booking Follow Up", {"crm_lead": crm_lead_doc.name}):
                        frappe.get_doc({
                            "doctype": "Booking Follow Up",
                            "whatsapp_id": whatsapp_id,
                            "crm_lead": crm_lead_doc.name
                        }).insert(ignore_permissions=True)
                    enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=OUT_OF_BOOKING_HOURS_MESSAGE, queue="short", is_async=True)

def handle_interactive_message(interactive_id, whatsapp_id, customer_name, crm_lead_doc=None):
    if not crm_lead_doc:
        crm_lead_doc = get_crm_lead(whatsapp_id, customer_name)

    if interactive_id == "agree-pdpa":
        frappe.flags.agree_pdpa = True
        enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=PDPA_ACCEPTED_REPLY, queue="short", is_async=True)

    whatsapp_interaction_message_template_buttons = frappe.db.get_all("WhatsApp Interaction Message Template Buttons", filters={"reply_id": interactive_id}, fields=["*"])

    if whatsapp_interaction_message_template_buttons:
        create_crm_lead_assignment(crm_lead_doc.name, whatsapp_interaction_message_template_buttons[0].whatsapp_message_templates)
        create_crm_tagging_assignment(crm_lead_doc.name, whatsapp_interaction_message_template_buttons[0].tagging)
        if whatsapp_interaction_message_template_buttons[0].reply_if_button_clicked and (interactive_id != "book-appointment" or (interactive_id == "book-appointment" and not is_not_within_booking_hours())):
            if whatsapp_interaction_message_template_buttons[0].reply_image:
                enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_interaction_message_template_buttons[0].reply_if_button_clicked, image=whatsapp_interaction_message_template_buttons[0].reply_image, queue="short", is_async=True)
            else:
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_interaction_message_template_buttons[0].reply_if_button_clicked, queue="short", is_async=True)
        if whatsapp_interaction_message_template_buttons[0].reply_2_if_button_clicked:
            if whatsapp_interaction_message_template_buttons[0].reply_image_2:
                enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_interaction_message_template_buttons[0].reply_2_if_button_clicked, image=whatsapp_interaction_message_template_buttons[0].reply_image_2, queue="short", is_async=True)
            else:
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_interaction_message_template_buttons[0].reply_2_if_button_clicked, queue="short", is_async=True)
        if whatsapp_interaction_message_template_buttons[0].send_out_of_working_hours_message and is_not_within_operating_hours():
            enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=OUT_OF_WORKING_HOURS_MESSAGE, queue="short", is_async=True)
        if whatsapp_interaction_message_template_buttons[0].send_out_of_booking_hours_message and is_not_within_booking_hours():
            if not frappe.db.exists("Booking Follow Up", {"crm_lead": crm_lead_doc.name}):
                frappe.get_doc({
                    "doctype": "Booking Follow Up",
                    "whatsapp_id": whatsapp_id,
                    "crm_lead": crm_lead_doc.name
                }).insert(ignore_permissions=True)
            enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=OUT_OF_BOOKING_HOURS_MESSAGE, queue="short", is_async=True)

def handle_template_message_reply(whatsapp_id, customer_name, message, reply_to_message_id, crm_lead_doc=None):
    reply_to_messages = frappe.db.get_all("WhatsApp Message", filters={"message_id": reply_to_message_id}, fields=["name", "whatsapp_message_templates", "replied"])
    if reply_to_messages and reply_to_messages[0].whatsapp_message_templates and not reply_to_messages[0].replied:
        frappe.db.set_value("WhatsApp Message", reply_to_messages[0].name, "replied", 1)
        whatsapp_message_template_doc = frappe.get_doc("WhatsApp Message Templates", reply_to_messages[0].whatsapp_message_templates)

        # Special handling for pending notification template
        if whatsapp_message_template_doc.is_pending_notification_template:
            if not crm_lead_doc:
                crm_lead_doc = get_crm_lead(whatsapp_id, customer_name)
            enqueue(
                method=send_pending_messages_for_lead,
                crm_lead_name=crm_lead_doc.name,
                whatsapp_id=whatsapp_id,
                queue="short",
                is_async=True
            )
            return

        for whatsapp_message_template_button in whatsapp_message_template_doc.whatsapp_message_template_buttons:
            if message == whatsapp_message_template_button.button_label:
                if not crm_lead_doc:
                    crm_lead_doc = get_crm_lead(whatsapp_id, customer_name)
                create_crm_lead_assignment(crm_lead_doc.name, whatsapp_message_template_doc.name)
                create_crm_tagging_assignment(crm_lead_doc.name, whatsapp_message_template_doc.tagging)
                if whatsapp_message_template_button.reply_if_button_clicked:
                    if whatsapp_message_template_button.reply_image:
                        enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_message_template_button.reply_if_button_clicked, image=whatsapp_message_template_button.reply_image, queue="short", is_async=True)
                    else:
                        enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_message_template_button.reply_if_button_clicked, queue="short", is_async=True)
                if whatsapp_message_template_button.reply_2_if_button_clicked:
                    if whatsapp_message_template_button.reply_image_2:
                        enqueue(method=send_image_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_message_template_button.reply_2_if_button_clicked, image=whatsapp_message_template_button.reply_image_2, queue="short", is_async=True)
                    else:
                        enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=whatsapp_message_template_button.reply_2_if_button_clicked, queue="short", is_async=True)
                if whatsapp_message_template_button.reply_whatsapp_interaction_if_button_clicked:
                    enqueue(method=send_interaction_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, whatsapp_interaction_message_template=whatsapp_message_template_button.reply_whatsapp_interaction_if_button_clicked, queue="short", is_async=True)
                break

def send_pending_messages_for_lead(crm_lead_name, whatsapp_id):
    """Send all pending WhatsApp messages for a CRM Lead in creation order (background job)."""
    pending_messages = frappe.db.get_all(
        "Pending WhatsApp Message",
        filters={
            "status": "Pending",
            "reference_doctype": "CRM Lead",
            "reference_name": crm_lead_name
        },
        fields=["name", "message", "content_type", "attach", "use_template", "template",
                "template_parameters", "template_header_parameters"],
        order_by="creation asc"
    )

    if not pending_messages:
        return

    crm_lead_doc = frappe.get_doc("CRM Lead", crm_lead_name)

    for pending in pending_messages:
        try:
            if pending.use_template and pending.template:
                whatsapp_msg = frappe.new_doc("WhatsApp Message")
                whatsapp_msg.type = "Outgoing"
                whatsapp_msg.to = whatsapp_id
                whatsapp_msg.message_type = "Template"
                whatsapp_msg.content_type = pending.content_type or "text"
                whatsapp_msg.message = pending.message
                whatsapp_msg.use_template = 1
                whatsapp_msg.template = pending.template
                whatsapp_msg.template_parameters = pending.template_parameters
                whatsapp_msg.template_header_parameters = pending.template_header_parameters
                whatsapp_msg.reference_doctype = "CRM Lead"
                whatsapp_msg.reference_name = crm_lead_name
                whatsapp_msg.insert(ignore_permissions=True)
            else:
                if pending.content_type in ("image", "video", "document") and pending.attach:
                    whatsapp_msg = frappe.new_doc("WhatsApp Message")
                    whatsapp_msg.type = "Outgoing"
                    whatsapp_msg.to = whatsapp_id
                    whatsapp_msg.message_type = "Manual"
                    whatsapp_msg.content_type = pending.content_type
                    whatsapp_msg.message = pending.message
                    whatsapp_msg.attach = pending.attach
                    whatsapp_msg.reference_doctype = "CRM Lead"
                    whatsapp_msg.reference_name = crm_lead_name
                    whatsapp_msg.insert(ignore_permissions=True)
                else:
                    send_message(crm_lead_doc, whatsapp_id, pending.message)

            frappe.db.set_value("Pending WhatsApp Message", pending.name, "status", "Completed", update_modified=False)
            frappe.db.commit()
            time.sleep(2)
        except Exception as e:
            frappe.log_error(title="Send Pending WhatsApp Message Error", message=f"Pending message {pending.name}: {str(e)}")

    frappe.db.set_value("CRM Lead", crm_lead_name, "notified_for_pending_message", 0, update_modified=False)
    frappe.db.commit()

def send_message_with_delay(crm_lead_doc, whatsapp_id, text):
    time.sleep(2)
    send_message(crm_lead_doc, whatsapp_id, text)

def send_image_with_delay(crm_lead_doc, whatsapp_id, text, image):
    time.sleep(2)
    send_image(crm_lead_doc, whatsapp_id, text, image)

def send_image(crm_lead_doc, whatsapp_id, text, image):
    whatsapp_message_reply = frappe.new_doc("WhatsApp Message")
    whatsapp_message_reply.type = "Outgoing"
    whatsapp_message_reply.to = whatsapp_id
    whatsapp_message_reply.message_type = "Manual"
    whatsapp_message_reply.content_type = "image"
    whatsapp_message_reply.reference_doctype = crm_lead_doc.doctype
    whatsapp_message_reply.reference_name = crm_lead_doc.name
    whatsapp_message_reply.message = text
    whatsapp_message_reply.attach = image
    whatsapp_message_reply.insert(ignore_permissions=True)

def send_interaction_with_delay(crm_lead_doc, whatsapp_id, whatsapp_interaction_message_template):
    time.sleep(2)
    whatsapp_interaction_message_template_doc = frappe.get_doc("WhatsApp Interaction Message Templates", whatsapp_interaction_message_template)
    settings = frappe.get_single("WhatsApp Settings")
    token = settings.get_password("token")
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }

    buttons = []

    for button in whatsapp_interaction_message_template_doc.whatsapp_interaction_message_template_buttons:
        buttons.append({
            "type": "reply",
            "reply": {
                "id": button.reply_id,
                "title": button.button_label 
            }
        })

    interactive = {
        "type": "button",
        "body": {
            "text": whatsapp_interaction_message_template_doc.message
        },
        "action": {
            "buttons": buttons
        }
    }

    if whatsapp_interaction_message_template_doc.header_image:
        interactive["header"] = {
            "type": "image",
            "image": {
                "link": frappe.utils.get_url() + "/" + whatsapp_interaction_message_template_doc.header_image
            }
        }

    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": whatsapp_id,
        "type": "interactive",
        "interactive": interactive
    }
    response = make_post_request(
        f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
        headers=headers,
        data=json.dumps(data),
    )
    message_id = response["messages"][0]["id"]
    frappe.db.set_value("CRM Lead", crm_lead_doc.name, "latest_whatsapp_interaction_message_templates", whatsapp_interaction_message_template)
    doc = frappe.new_doc("WhatsApp Message")
    doc.update(
        {
            "reference_doctype": "CRM Lead",
            "reference_name": crm_lead_doc.name,
            "message_type": "Manual",
            "message": whatsapp_interaction_message_template_doc.message,
            "content_type": "text",
            "to": whatsapp_id,
            "message_id": message_id,
            "status": "Success",
            "timestamp": get_datetime(),
        }
    )
    doc.flags.is_template_queue = True
    doc.insert(ignore_permissions=True)

def send_message(crm_lead_doc, whatsapp_id, text):
    whatsapp_message_reply = frappe.new_doc("WhatsApp Message")
    whatsapp_message_reply.type = "Outgoing"
    whatsapp_message_reply.to = whatsapp_id
    whatsapp_message_reply.message_type = "Manual"
    whatsapp_message_reply.content_type = "text"
    whatsapp_message_reply.reference_doctype = crm_lead_doc.doctype
    whatsapp_message_reply.reference_name = crm_lead_doc.name
    whatsapp_message_reply.message = text
    whatsapp_message_reply.insert(ignore_permissions=True)

def send_interactive_message(crm_lead_doc, whatsapp_id, text, buttons):
    whatsapp_settings = frappe.get_single("WhatsApp Settings")

    WHATSAPP_SEND_MESSAGE_URL = "{0}/{1}/{2}/messages".format(whatsapp_settings.url, whatsapp_settings.version, whatsapp_settings.phone_id)
    BEARER_TOKEN = whatsapp_settings.get_password("token")

    request_body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": whatsapp_id,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": text
            },
            "action": {
                "buttons": buttons
            }
        }
    }

    headers = {
        "authorization": f"Bearer {BEARER_TOKEN}",
        "content-type": "application/json",
    }

    try:
        response = requests.post(WHATSAPP_SEND_MESSAGE_URL, data=json.dumps(request_body), headers=headers, timeout=5)
        message_id = response.json()["messages"][0]["id"]
        doc = frappe.new_doc("WhatsApp Message")
        doc.update(
            {
                "reference_doctype": "CRM Lead",
                "reference_name": crm_lead_doc.name,
                "message_type": "Manual",
                "message": text,
                "content_type": "text",
                "to": whatsapp_id,
                "message_id": message_id,
                "status": "Success",
                "timestamp": get_datetime(),
                "type": "Outgoing"
            }
        )
        doc.insert(ignore_permissions=True)
    except:
        return False

    if response.ok:
        return True

    return False

def send_location_request_message(crm_lead_doc, whatsapp_id, text):
    """
    Send a location request message to the user.
    The user will see a button to share their current location.

    Based on WhatsApp Cloud API:
    https://developers.facebook.com/docs/whatsapp/cloud-api/messages/location-request-messages
    """
    whatsapp_settings = frappe.get_single("WhatsApp Settings")

    WHATSAPP_SEND_MESSAGE_URL = "{0}/{1}/{2}/messages".format(
        whatsapp_settings.url, whatsapp_settings.version, whatsapp_settings.phone_id
    )
    BEARER_TOKEN = whatsapp_settings.get_password("token")

    request_body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": whatsapp_id,
        "type": "interactive",
        "interactive": {
            "type": "location_request_message",
            "body": {
                "text": text
            },
            "action": {
                "name": "send_location"
            }
        }
    }

    headers = {
        "authorization": f"Bearer {BEARER_TOKEN}",
        "content-type": "application/json",
    }

    try:
        response = requests.post(
            WHATSAPP_SEND_MESSAGE_URL,
            data=json.dumps(request_body),
            headers=headers,
            timeout=5
        )
        response_data = response.json()
        message_id = response_data["messages"][0]["id"]

        doc = frappe.new_doc("WhatsApp Message")
        doc.update(
            {
                "reference_doctype": "CRM Lead",
                "reference_name": crm_lead_doc.name,
                "message_type": "Manual",
                "message": text,
                "content_type": "text",
                "to": whatsapp_id,
                "message_id": message_id,
                "status": "Success",
                "timestamp": get_datetime(),
                "type": "Outgoing"
            }
        )
        doc.insert(ignore_permissions=True)
        return True
    except Exception as e:
        frappe.log_error("Location Request Message Error", str(e))
        return False

def send_location_request_message_with_delay(crm_lead_doc, whatsapp_id, text):
    """Send location request message with a delay."""
    time.sleep(1)
    send_location_request_message(crm_lead_doc, whatsapp_id, text)

def send_interactive_cta_message_with_delay(crm_lead_doc, whatsapp_id, text, cta_label, cta_url):
    time.sleep(2)
    send_interactive_cta_message(crm_lead_doc, whatsapp_id, text, cta_label, cta_url)

def send_interactive_cta_message(crm_lead_doc, whatsapp_id, text, cta_label, cta_url):
    whatsapp_settings = frappe.get_single("WhatsApp Settings")

    WHATSAPP_SEND_MESSAGE_URL = "{0}/{1}/{2}/messages".format(whatsapp_settings.url, whatsapp_settings.version, whatsapp_settings.phone_id)
    BEARER_TOKEN = whatsapp_settings.get_password("token")

    request_body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": whatsapp_id,
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {
                "text": text
            },
            "action": {
                "name": "cta_url",
                "parameters": {
                    "display_text": cta_label,
                    "url": cta_url
                }
            }
        }
    }

    headers = {
        "authorization": f"Bearer {BEARER_TOKEN}",
        "content-type": "application/json",
    }

    try:
        response = requests.post(WHATSAPP_SEND_MESSAGE_URL, data=json.dumps(request_body), headers=headers, timeout=5)
        message_id = response.json()["messages"][0]["id"]
        doc = frappe.new_doc("WhatsApp Message")
        doc.update(
            {
                "reference_doctype": "CRM Lead",
                "reference_name": crm_lead_doc.name,
                "message_type": "Manual",
                "message": "{0}\n\n{1}".format(text, cta_url),
                "content_type": "text",
                "to": whatsapp_id,
                "message_id": message_id,
                "status": "Success",
                "timestamp": get_datetime(),
                "type": "Outgoing"
            }
        )
        doc.insert(ignore_permissions=True)
    except:
        return False

    if response.ok:
        return True

    return False

def send_interactive_list_message_with_delay(crm_lead_doc, whatsapp_id, header_text, body_text, footer_text, button_text, sections):
    """
    Send interactive list message with a delay.

    Args:
        crm_lead_doc: CRM Lead document
        whatsapp_id: Recipient WhatsApp ID
        header_text: Header text for the list message
        body_text: Body text for the list message
        footer_text: Footer text (optional, can be None)
        button_text: Text displayed on the button to open the list
        sections: List of sections, each containing title and rows
            Example:
            [
                {
                    "title": "Section 1",
                    "rows": [
                        {"id": "row_1", "title": "Option 1", "description": "Description 1"},
                        {"id": "row_2", "title": "Option 2", "description": "Description 2"}
                    ]
                }
            ]
    """
    time.sleep(2)
    return send_interactive_list_message(crm_lead_doc, whatsapp_id, header_text, body_text, footer_text, button_text, sections)

def send_interactive_list_message(crm_lead_doc, whatsapp_id, header_text, body_text, footer_text, button_text, sections):
    """
    Send an interactive list message via WhatsApp Cloud API.

    Interactive list messages allow users to select from a list of options.
    Based on WhatsApp Cloud API:
    https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-list-messages

    Args:
        crm_lead_doc: CRM Lead document for reference
        whatsapp_id: Recipient WhatsApp ID (with country code)
        header_text: Header text for the list message (max 60 chars)
        body_text: Body text explaining the list options (max 1024 chars)
        footer_text: Optional footer text (max 60 chars), can be None
        button_text: Text displayed on the button to open the list (max 20 chars)
        sections: List of sections containing rows
            Each section: {"title": "Section Title", "rows": [...]}
            Each row: {"id": "unique_id", "title": "Row Title", "description": "Optional description"}
            - Max 10 sections
            - Max 10 rows per section
            - Row title max 24 chars
            - Row description max 72 chars

    Returns:
        dict: Response containing success status and message_id if successful
    """
    whatsapp_settings = frappe.get_single("WhatsApp Settings")

    WHATSAPP_SEND_MESSAGE_URL = "{0}/{1}/{2}/messages".format(
        whatsapp_settings.url, whatsapp_settings.version, whatsapp_settings.phone_id
    )
    BEARER_TOKEN = whatsapp_settings.get_password("token")

    # Build the interactive list message payload
    interactive = {
        "type": "list",
        "body": {
            "text": body_text
        },
        "action": {
            "button": button_text,
            "sections": sections
        }
    }

    # Add optional header
    if header_text:
        interactive["header"] = {
            "type": "text",
            "text": header_text
        }

    # Add optional footer
    if footer_text:
        interactive["footer"] = {
            "text": footer_text
        }

    request_body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": whatsapp_id,
        "type": "interactive",
        "interactive": interactive
    }

    headers = {
        "authorization": f"Bearer {BEARER_TOKEN}",
        "content-type": "application/json",
    }

    try:
        response = requests.post(
            WHATSAPP_SEND_MESSAGE_URL,
            data=json.dumps(request_body),
            headers=headers,
            timeout=5
        )
        response_data = response.json()

        if not response.ok:
            frappe.log_error(
                title="Interactive List Message Error",
                message=f"Failed to send list message: {response_data}"
            )
            return {"success": False, "error": response_data}

        message_id = response_data["messages"][0]["id"]

        # Create WhatsApp Message record
        doc = frappe.new_doc("WhatsApp Message")
        doc.update({
            "reference_doctype": crm_lead_doc.doctype if crm_lead_doc else "CRM Lead",
            "reference_name": crm_lead_doc.name if crm_lead_doc else None,
            "message_type": "Manual",
            "message": f"{header_text}\n\n{body_text}" if header_text else body_text,
            "content_type": "text",
            "to": whatsapp_id,
            "message_id": message_id,
            "status": "Success",
            "timestamp": get_datetime(),
            "type": "Outgoing"
        })
        doc.insert(ignore_permissions=True)

        return {"success": True, "message_id": message_id}

    except Exception as e:
        frappe.log_error(
            title="Interactive List Message Exception",
            message=f"Exception sending list message: {str(e)}"
        )
        return {"success": False, "error": str(e)}

def handle_interactive_list_reply(whatsapp_id, customer_name, list_reply_id, list_reply_title, crm_lead_doc=None):
    """
    Handle response when staff/masseur selects an option from an interactive list message.

    This function is called when an incoming message of type 'interactive' with 'list_reply' is received.

    Args:
        whatsapp_id: The WhatsApp ID of the staff member who responded
        customer_name: Name of the staff member
        list_reply_id: The ID of the selected row (set when creating the list)
        list_reply_title: The title of the selected row
        crm_lead_doc: Optional CRM Lead document for the staff member

    Returns:
        bool: True if handled successfully, False otherwise
    """
    try:
        if not crm_lead_doc:
            crm_lead_doc = get_crm_lead(whatsapp_id, customer_name)

        print(f"Staff {customer_name} selected list option: {list_reply_id} - {list_reply_title}")

        # Handle different list reply IDs based on your business logic
        # Example patterns for staff/masseur actions:

        if list_reply_id == "register_clock_in":
            # Handle face registration for clock in
            set_face_registration_mode(crm_lead_doc, enabled=True)
            enqueue(
                method=send_message_with_delay,
                crm_lead_doc=crm_lead_doc,
                whatsapp_id=whatsapp_id,
                text="📸 *Face Registration*\n\nPlease take a clear selfie photo of your face and send it here to register for clock in.\n\nMake sure:\n• Your face is clearly visible\n• Good lighting\n• No sunglasses or face coverings",
                queue="short",
                is_async=True
            )
            return True

        elif list_reply_id.startswith("clock_"):
            # Handle clock in/out options
            action = list_reply_id.replace("clock_", "")
            if action == "in":
                set_clock_log_type(crm_lead_doc, "IN")
                enqueue(
                    method=send_location_request_message_with_delay,
                    crm_lead_doc=crm_lead_doc,
                    whatsapp_id=whatsapp_id,
                    text="Please share your location to complete clock in.",
                    queue="short",
                    is_async=True
                )
            elif action == "out":
                set_clock_log_type(crm_lead_doc, "OUT")
                enqueue(
                    method=send_location_request_message_with_delay,
                    crm_lead_doc=crm_lead_doc,
                    whatsapp_id=whatsapp_id,
                    text="Please share your location to complete clock out.",
                    queue="short",
                    is_async=True
                )
            return True

        elif list_reply_id.startswith("leave_"):
            # Handle leave request options
            leave_type = list_reply_id.replace("leave_", "")
            # Set leave application mode with the leave type
            set_leave_application_mode(crm_lead_doc, leave_type)

            leave_type_display = leave_type.replace("_", " ").title()

            # Generate sample future dates for examples
            sample_date_1 = frappe.utils.add_days(frappe.utils.now_datetime(), 7)
            sample_date_2 = frappe.utils.add_days(frappe.utils.now_datetime(), 10)
            sample_date_1_str = sample_date_1.strftime("%-d %b %Y")
            sample_date_2_str = f"{sample_date_1.strftime('%-d')}-{sample_date_2.strftime('%-d %b %Y')}"

            leave_prompt = (
                f"📅 *{leave_type_display} Leave Application*\n\n"
                f"Please provide the date and reason for your leave.\n\n"
                f"*Examples:*\n"
                f"• _{sample_date_1_str}, family wedding_\n"
                f"• _{sample_date_2_str}, going for vacation_\n"
                f"• _tomorrow, not feeling well_\n\n"
                f"Reply with your date and reason in a single message."
            )

            enqueue(
                method=send_message_with_delay,
                crm_lead_doc=crm_lead_doc,
                whatsapp_id=whatsapp_id,
                text=leave_prompt,
                queue="short",
                is_async=True
            )
            return True

        elif list_reply_id.startswith("schedule_"):
            # Handle schedule viewing options
            schedule_option = list_reply_id.replace("schedule_", "")
            enqueue(
                method=send_message_with_delay,
                crm_lead_doc=crm_lead_doc,
                whatsapp_id=whatsapp_id,
                text=f"Fetching your {schedule_option} schedule...",
                queue="short",
                is_async=True
            )
            return True

        else:
            # Generic handler for other list replies
            # Log for debugging/tracking
            frappe.get_doc({
                "doctype": "WhatsApp Notification Log",
                "template": "List Reply",
                "meta_data": json.dumps({
                    "whatsapp_id": whatsapp_id,
                    "customer_name": customer_name,
                    "list_reply_id": list_reply_id,
                    "list_reply_title": list_reply_title,
                    "crm_lead": crm_lead_doc.name if crm_lead_doc else None
                })
            }).insert(ignore_permissions=True)

            return True

    except Exception as e:
        frappe.log_error(
            title="Interactive List Reply Handler Error",
            message=f"Error handling list reply: {str(e)}\nReply ID: {list_reply_id}\nWhatsApp ID: {whatsapp_id}"
        )
        return False

def get_crm_lead(whatsapp_id, customer_name):
    reference_name, doctype = get_lead_or_deal_from_number(whatsapp_id)
    if not reference_name:
        crm_lead_doc = frappe.new_doc("CRM Lead")
        crm_lead_doc.lead_name = customer_name
        crm_lead_doc.first_name = customer_name
        crm_lead_doc.last_name = ""
        crm_lead_doc.mobile_no = whatsapp_id
        crm_lead_doc.insert(ignore_permissions=True)
        reference_name = crm_lead_doc.name
    else:
        crm_lead_doc = frappe.get_doc("CRM Lead", reference_name)
    return crm_lead_doc

def is_not_within_operating_hours():
    current_datetime = get_datetime()

    # Define time range
    start = datetime.time(9, 0)   # 9:00 AM
    end = datetime.time(17, 0)    # 5:00 PM

    # Check if current time is within range
    if start <= current_datetime.time() <= end:
        return False

    return True

def is_not_within_booking_hours():
    current_datetime = get_datetime()

    # Define time range
    start = datetime.time(8, 45)   # 08:45 AM
    end = datetime.time(21, 0)    # 9:00 PM

    # Check if current time is within range
    if start <= current_datetime.time() <= end:
        return False

    return True

def send_booking_follow_up():
    booking_follow_ups = frappe.db.get_all("Booking Follow Up", fields=["whatsapp_id", "crm_lead"])
    for booking_follow_up in booking_follow_ups:
        enqueue(method=send_message_with_delay, crm_lead_doc=frappe.get_doc("CRM Lead", booking_follow_up.crm_lead), whatsapp_id=booking_follow_up.whatsapp_id, text=OUT_OF_BOOKING_HOURS_FOLLOW_UP_MESSAGE, queue="short", is_async=True)
    frappe.db.truncate("Booking Follow Up")

def send_chat_closing_reminder():
    unclosed_crm_leads = frappe.db.get_all("CRM Lead Assignment", filters={"whatsapp_message_templates": ["!=", "BookingHL"], "status": ["!=", "Case Closed"]}, pluck="crm_lead")
    unclosed_crm_leads = list(set(unclosed_crm_leads))
    crm_leads = frappe.db.get_all("CRM Lead", filters={"name": ["in", unclosed_crm_leads], "sent_chat_closing_reminder": 0, "last_message_from_me": 1, "chat_close_at": ["<=", get_datetime()]}, fields=["name", "mobile_no"])
    for crm_lead in crm_leads:
        if crm_lead.mobile_no:
            crm_lead_doc = frappe.get_doc("CRM Lead", crm_lead.name)
            enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=crm_lead.mobile_no, text=CHAT_CLOSING_MESSAGE, queue="short", is_async=True)
            crm_lead_doc.sent_chat_closing_reminder = True
            crm_lead_doc.save(ignore_permissions=True)

    unclosed_crm_leads = frappe.db.get_all("CRM Lead Assignment", filters={"status": "Completed"}, pluck="crm_lead")
    unclosed_crm_leads = list(set(unclosed_crm_leads))
    if unclosed_crm_leads:
        crm_leads = frappe.db.get_all("CRM Lead", filters={"name": ["in", unclosed_crm_leads], "chat_close_at": ["<=", add_to_date(get_datetime(), hours=-2)], "last_reply_at": ["<=", add_to_date(get_datetime(), days=-1)]}, pluck="name")
        if crm_leads:
            crm_leads_to_close = frappe.db.get_all("CRM Lead Assignment", filters={"crm_lead": ["in", crm_leads], "status": "Completed"}, pluck="name")
            for crm_lead_to_close in crm_leads_to_close:
                frappe.db.set_value("CRM Lead Assignment", crm_lead_to_close, {
                    "status": "Case Closed",
                    "accepted_by": None
                })
            taggings_to_close = frappe.db.get_all("CRM Lead Tagging", filters={"crm_lead": ["in", crm_leads], "status": "Open"}, pluck="name")
            for tagging_to_close in taggings_to_close:
                frappe.db.set_value("CRM Lead Tagging", tagging_to_close, {
                    "status": "Closed"
                })

    frappe.db.commit()
    crm_leads = frappe.db.get_all("CRM Lead", filters=[["CRM Lead", "chat_close_at", "is", "set"], ["CRM Lead", "chat_close_at", "<=", add_to_date(get_datetime(), hours=-2)]], pluck="name")
    for crm_lead in crm_leads:
        frappe.db.set_value("CRM Lead", crm_lead, {
            "conversation_start_at": None,
            "last_reply_by_user": None,
            "last_reply_by": None,
            "last_reply_at": None,
            "chat_close_at": None,
        })

def get_existing_crm_lead_assignments(crm_lead, whatsapp_message_templates):
    return frappe.db.get_all("CRM Lead Assignment", filters={"crm_lead": crm_lead, "whatsapp_message_templates": whatsapp_message_templates}, pluck="name")

def create_crm_lead_assignment(crm_lead, whatsapp_message_templates, status=None):
    if not whatsapp_message_templates:
        return
    is_crm_agent_template = frappe.db.get_value("WhatsApp Message Templates", whatsapp_message_templates, "is_crm_agent_template")
    existing_crm_lead_assignments = get_existing_crm_lead_assignments(crm_lead, whatsapp_message_templates)
    if existing_crm_lead_assignments:
        for existing_crm_lead_assignment in existing_crm_lead_assignments:
            frappe.db.set_value("CRM Lead Assignment", existing_crm_lead_assignment, {
                "status": status or ("New" if is_crm_agent_template else "Completed")
            })
    else:
        frappe.get_doc({
            "doctype": "CRM Lead Assignment",
            "crm_lead": crm_lead,
            "whatsapp_message_templates": whatsapp_message_templates,
            "status": status or ("New" if is_crm_agent_template else "Completed"),
        }).insert(ignore_permissions=True)

    if whatsapp_message_templates != "automated_message":
        frappe.db.delete("CRM Lead Assignment", filters={
            "crm_lead": crm_lead,
            "whatsapp_message_templates": "automated_message"
        })

def get_existing_crm_taggings(crm_lead, tagging):
    return frappe.db.get_all("CRM Lead Tagging", filters={"crm_lead": crm_lead, "tagging": tagging}, pluck="name")

def create_crm_tagging_assignment(crm_lead, tagging, status=None):
    if not tagging:
        return
    existing_crm_taggings = get_existing_crm_taggings(crm_lead, tagging)
    if existing_crm_taggings:
        for existing_crm_tagging in existing_crm_taggings:
            frappe.db.set_value("CRM Lead Tagging", existing_crm_tagging, "status", status if status else "Open")
    else:
        frappe.get_doc({
            "doctype": "CRM Lead Tagging",
            "crm_lead": crm_lead,
            "tagging": tagging
        }).insert(ignore_permissions=True)

    if tagging != "Unknown":
        frappe.db.delete("CRM Lead Tagging", filters={
            "crm_lead": crm_lead,
            "tagging": "Unknown"
        })

def handle_membership_rate_request(crm_lead_doc, whatsapp_id):
    frappe.flags.skip_lead_status_update = True
    create_crm_lead_assignment(crm_lead_doc.name, "BookingHL", "Completed")
    integration_settings = frappe.db.get_all("Integration Settings", filters={"active": 1}, pluck="name")
    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc("Integration Settings", integration_setting)
        url = integration_settings_doc.site_url + REQUEST_MEMBERSHIP_RATE_ENDPOINT

        headers = {
            "Authorization": "Basic {0}".format(integration_settings_doc.get_password("access_token")),
            "Content-Type": "application/json"
        }

        request_body = {
            "mobile": whatsapp_id,
        }

        try:
            response = requests.post(url, json=request_body, headers=headers, timeout=30)  # 30 seconds timeout
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("message"):
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=response_data["message"], queue="short", is_async=True)
        except requests.Timeout:
            frappe.throw("Request timed out after 30 seconds")
        except requests.RequestException as e:
            frappe.throw(f"An error occurred: {e}")

def handle_free_membership_redemption(crm_lead_doc, whatsapp_id, message):
    frappe.flags.skip_lead_status_update = True
    create_crm_lead_assignment(crm_lead_doc.name, "BookingHL", "Completed")
    free_member_subscription_id = message.split(":")[-1].strip().lower()
    integration_settings = frappe.db.get_all("Integration Settings", filters={"active": 1}, pluck="name")
    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc("Integration Settings", integration_setting)
        url = integration_settings_doc.site_url + FREE_MEMBERSHIP_REDEMPTION_ENDPOINT

        headers = {
            "Authorization": "Basic {0}".format(integration_settings_doc.get_password("access_token")),
            "Content-Type": "application/json"
        }

        request_body = {
            "mobile": whatsapp_id,
            "free_member_subscription_id": free_member_subscription_id,
        }

        try:
            response = requests.post(url, json=request_body, headers=headers, timeout=30)  # 30 seconds timeout
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("message"):
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=response_data["message"], queue="short", is_async=True)
            if response_data.get("message_2"):
                enqueue(method=send_message_with_delay, crm_lead_doc=crm_lead_doc, whatsapp_id=whatsapp_id, text=response_data["message_2"], queue="short", is_async=True)
            if response_data.get("shared_by") and response_data.get("message_shared_by"):
                reference_name, doctype = get_lead_or_deal_from_number(response_data["shared_by"])
                shared_by_crm_lead_doc = frappe.get_doc(doctype, reference_name)
                enqueue(method=send_message_with_delay, crm_lead_doc=shared_by_crm_lead_doc, whatsapp_id=response_data["shared_by"], text=response_data["message_shared_by"], queue="short", is_async=True)
        except requests.Timeout:
            frappe.throw("Request timed out after 30 seconds")
        except requests.RequestException as e:
            frappe.throw(f"An error occurred: {e}")

def handle_checkout_login(crm_lead_doc, whatsapp_id, message):
    frappe.flags.skip_lead_status_update = True
    create_crm_lead_assignment(crm_lead_doc.name, "BookingHL", "Completed")
    if "I would like to Login with this WhatsApp number" in message:
        parts = message.split("OTP:", 1)
        message = parts[1].strip() if len(parts) > 1 else "XXXXXX"

    integration_settings = frappe.db.get_all("Integration Settings", filters={"active": 1}, pluck="name")
    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc("Integration Settings", integration_setting)
        url = integration_settings_doc.site_url + CHECKOUT_LOGIN_ENDPOINT

        headers = {
            "Authorization": "Basic {0}".format(integration_settings_doc.get_password("access_token")),
            "Content-Type": "application/json"
        }

        request_body = {
            "mobile_no": whatsapp_id,
            "first_name": crm_lead_doc.lead_name,
            "otp": message,
            "outlet": integration_settings_doc.outlet,
        }

        try:
            response = requests.post(url, json=request_body, headers=headers, timeout=30)  # 30 seconds timeout
            response.raise_for_status()
            response_data = response.json()

            if response_data.get("message") and response_data.get("cta_url") and response_data.get("cta_label"):
                send_interactive_cta_message_with_delay(crm_lead_doc, whatsapp_id, response_data["message"], response_data["cta_label"], response_data["cta_url"])

        except requests.Timeout:
            frappe.throw("Request timed out after 30 seconds")
        except requests.RequestException as e:
            frappe.throw(f"An error occurred: {e}")

def handle_registration(crm_lead_doc, whatsapp_id, message):
    frappe.flags.skip_lead_status_update = True
    create_crm_lead_assignment(crm_lead_doc.name, "BookingHL", "Completed")
    parts = message.split("OTP:", 1)
    message = parts[1].strip() if len(parts) > 1 else "XXXXXX"

    integration_settings = frappe.db.get_all("Integration Settings", filters={"active": 1}, pluck="name")
    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc("Integration Settings", integration_setting)
        url = integration_settings_doc.site_url + REGISTRATION_ENDPOINT

        headers = {
            "Authorization": "Basic {0}".format(integration_settings_doc.get_password("access_token")),
            "Content-Type": "application/json"
        }

        request_body = {
            "mobile_no": whatsapp_id,
            "otp": message,
        }

        try:
            response = requests.post(url, json=request_body, headers=headers, timeout=30)  # 30 seconds timeout
            response.raise_for_status()
            response_data = response.json()

            if response_data.get("message") and response_data.get("cta_url") and response_data.get("cta_label"):
                send_interactive_cta_message_with_delay(crm_lead_doc, whatsapp_id, response_data["message"], response_data["cta_label"], response_data["cta_url"])

        except requests.Timeout:
            frappe.throw("Request timed out after 30 seconds")
        except requests.RequestException as e:
            frappe.throw(f"An error occurred: {e}")

def handle_reset_password(crm_lead_doc, whatsapp_id, message):
    frappe.flags.skip_lead_status_update = True
    create_crm_lead_assignment(crm_lead_doc.name, "BookingHL", "Completed")
    parts = message.split("OTP:", 1)
    message = parts[1].strip() if len(parts) > 1 else "XXXXXX"

    integration_settings = frappe.db.get_all("Integration Settings", filters={"active": 1}, pluck="name")
    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc("Integration Settings", integration_setting)
        url = integration_settings_doc.site_url + RESET_PASSWORD_ENDPOINT

        headers = {
            "Authorization": "Basic {0}".format(integration_settings_doc.get_password("access_token")),
            "Content-Type": "application/json"
        }

        request_body = {
            "mobile_no": whatsapp_id,
            "otp": message,
        }

        try:
            response = requests.post(url, json=request_body, headers=headers, timeout=30)  # 30 seconds timeout
            response.raise_for_status()
            response_data = response.json()

            if response_data.get("message") and response_data.get("cta_url") and response_data.get("cta_label"):
                send_interactive_cta_message_with_delay(crm_lead_doc, whatsapp_id, response_data["message"], response_data["cta_label"], response_data["cta_url"])

        except requests.Timeout:
            frappe.throw("Request timed out after 30 seconds")
        except requests.RequestException as e:
            frappe.throw(f"An error occurred: {e}")