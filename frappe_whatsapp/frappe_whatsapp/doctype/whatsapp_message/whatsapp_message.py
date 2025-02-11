# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import json
import frappe
from frappe.utils import get_datetime, getdate, flt, cint
from frappe.model.document import Document
from frappe.integrations.utils import make_post_request
import random
import hashlib
from crm.api.whatsapp import get_lead_or_deal_from_number
import requests
# from frappe.utils.background_jobs import enqueue

PAYMENT_STATUS_MAPPING = {
    "00": "Completed",
    "11": "Failed",
    "22": "Pending"
}

TERMS_AND_CONDITION_1 = "✏️ Please acknowledge with the terms and condition below before making a purchase ✏️\n\n📌 Treatment are redeemable at ALL HL outlets, except HealthLand Malacca, Sunway Velocity 2, Puchong Jaya, Premium HealthLand and Royals HealthLand.\n📌 Any voucher sold is strictly not cancellable or refundable.\n📌 Any add-ons other than the services listed herein (including any products, i.e. Balm, Aroma Oil, Herbal Patch, Steamy Eyemask) are payable by the customer.\n📌 This package is redeemable on weekday and weekend, including Public Holiday."
TERMS_AND_CONDITION_2 = "📌 Package A is valid for 270 days from the date of purchase and Package B is valid for 365 days from the date of purchase.\n📌 No extension request will be granted.\n📌 Booking in advance is required through Booking Center at +6019-3199126 or +60199002633 (WhatsApp message only).\n📌 HealthLand reserves the right to change, amend, add, or delete any of the Terms and Conditions without prior notice, and the customers shall be bound by such changes. In the event of any disputes, HealthLand reserves the right to take any action necessary to resolve the dispute, and such action taken by HealthLand shall be binding, final, and conclusive."
TERMS_AND_CONDITION_BUTTON = [
    {
        "type": "reply",
        "reply": {
            "id": "accept-tnc",
            "title": "Accept T&C" 
        }
    }
]

CHOOSE_PRODUCT_MESSAGE = "Please click the button below to choose your voucher! 😊"
CHOOSE_PRODUCT_BUTTON = [
    {
        "type": "reply",
        "reply": {
            "id": "voucher-book-a",
            "title": "Voucher Book A" 
        }
    },
    {
        "type": "reply",
        "reply": {
            "id": "voucher-book-b",
            "title": "Voucher Book B" 
        }
    }
]

MAKE_PAYMENT_MESSAGE = "Tap the button below to make payment."

CHOOSE_VOUCHER_TEXT = "Please click the button below to select a Voucher"

COMPLETED_BOOKING_MESSAGE = "🎉 Thank you for your purchase! Your transaction has been successfully completed. ✨\n\n🌟 How to Redeem ? 🌟\n\n1️⃣ Send a WhatsApp message to 019-626 6399 and let us know how many vouchers you'd like to redeem. 📲\n2️⃣ Our system will guide you steps through the process. 🛠️\n3️⃣ Don't worry, the process is safe and secure! 🔒😊"

REDEEM_VOUCHER_CONFIRMATION_MESSAGE = """You're about to redeem {0} vouchers! 🎉\n\nPlease reply "Yes" if everything looks good. 👍\nIf you'd like to change the number, just reply "No" and we'll update it! ✨"""
NO_VOUCHER_MESSAGE = "Hi there! It looks like we don't have any voucher balance recorded under your phone number.\nIf you believe this is an error, please feel free to reach out to our customer support via Facebook Messenger. 📩\nand show us your redeemption histroy 😊\nFacebook customer support: https://m.me/my.healthland"
ENTER_VOUCHER_COUNT_MESSAGE = "How many vouchers would you like to redeem today? 😊\nJust enter a number, like 1, 2, and so on! 🎉"
INVALID_VOUCHER_COUNT_MESSAGE = "Oops! Something went wrong. 😬\nCan you please enter the number in digits? For example, 1, 2, 3, etc."
INSUFFICIENT_VOUCHER_COUNT_MESSAGE = "Opps you do not have enough vouchers, please key in again in digits.\ne.g. 1, 2, 3, ..."
REDEEMED_VOUCHER_MESSAGE = "Your code has been successfully redeemed! 🎉\nThank you so much for your visit. We hope to see you again soon! 😊✨"
FOLLOW_UP_MESSAGE = "Hi Mr./Ms.\nThank you for being such a valued member of our HealthLand family! 🌟 How was your visit today? We hope you had a wonderful experience! 😊"

DO_NOT_UNDERSTAND_MESSAGE = "Opps I cannot understand you."


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
        if self.type == "Incoming" and self.reference_doctype == "CRM Lead" and self.reference_name:
            # whatsapp_message_reply = frappe.new_doc("WhatsApp Message")
            # whatsapp_message_reply.type = "Outgoing"
            # whatsapp_message_reply.to = self.get("from")
            # whatsapp_message_reply.message_type = "Manual"
            # whatsapp_message_reply.content_type = "text"
            # whatsapp_message_reply.reference_doctype = self.reference_doctype
            # whatsapp_message_reply.reference_name = self.reference_name
            # if frappe.db.count('WhatsApp Message') % 100 == 0:
            #     whatsapp_message_reply.message = "Congratulations 🎉 you have won the grand prize !!!"
            # else:
            #     random_replies = frappe.db.get_all("Random Reply", pluck="message")
            #     whatsapp_message_reply.message = random.choice(random_replies)
            # whatsapp_message_reply.insert(ignore_permissions=True)

            if self.type == "Incoming" and self.reference_doctype == "CRM Lead" and self.reference_name and self.content_type == "text":
                handle_text_message(self.message, self.get("from"), self.from_name)
            elif self.type == "Incoming" and self.reference_doctype == "CRM Lead" and self.reference_name and self.content_type == "flow":
                handle_interactive_message(self.interactive_id, self.get("from"), self.from_name)

            crm_lead_doc = frappe.get_doc("CRM Lead", self.reference_name)
            if crm_lead_doc.conversation_status == "Completed":
                crm_lead_doc.conversation_status = "New"
                crm_lead_doc.conversation_start_at = get_datetime()
                crm_lead_doc.save(ignore_permissions=True)
                frappe.publish_realtime("new_leads", {})

        if self.type == "Outgoing" and self.reference_doctype == "CRM Lead" and self.reference_name:
            crm_lead_doc = frappe.get_doc("CRM Lead", self.reference_name)
            crm_lead_doc.last_reply_by_user = frappe.session.user
            crm_lead_doc.save(ignore_permissions=True)

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

def generate_payment_url(crm_lead_doc, whatsapp_product_id):
    fiuu_settings = frappe.get_single("Fiuu Settings")
    whatsapp_product_doc = frappe.get_doc("Whatsapp Product", whatsapp_product_id)

    whatsapp_order_doc = frappe.new_doc("Whatsapp Order")
    whatsapp_order_doc.crm_lead = crm_lead_doc.name
    whatsapp_order_doc.date = getdate()

    amount = flt(1) * flt(whatsapp_product_doc.price)

    whatsapp_order_doc.append("whatsapp_order_product", {
        "doctype": "Whatsapp Order Product",
        "whatsapp_product": whatsapp_product_doc.name,
        "quantity": flt(1),
        "amount": amount,
    })

    whatsapp_order_doc.grand_total = amount
    whatsapp_order_doc.insert(ignore_permissions=True)

    payment_url = "https://pay.merchant.razer.com/RMS/pay/{0}/".format(fiuu_settings.merchant_id)
    payment_url += "?amount={0}".format(whatsapp_order_doc.grand_total)
    payment_url += "&orderid={0}".format(whatsapp_order_doc.name)
    payment_url += "&bill_desc={0}".format("""Payment for booking {0}""".replace(" ", """%20""").format(whatsapp_order_doc.name))
    payment_url += "&bill_name={0}".format(crm_lead_doc.first_name.replace(" ", """%20"""))
    payment_url += "&bill_mobile={0}".format(crm_lead_doc.mobile_no)
    payment_url += "&bill_email={0}".format("")
    payment_url += "&country={0}".format("MY")
    payment_url += "&currency={0}".format("MYR")
    payment_url += "&guess_checkout={0}".format("1")

    verify_key = fiuu_settings.get_password("verify_key")
    vcode = calculate_md5(str(whatsapp_order_doc.grand_total) + fiuu_settings.merchant_id + whatsapp_order_doc.name + verify_key)
    payment_url += "&vcode={0}".format(vcode)

    return payment_url

def calculate_md5(input_string):
    # Create an MD5 hash object
    md5_hash = hashlib.md5()
    # Update the hash object with the bytes-like object of the input string
    md5_hash.update(input_string.encode('utf-8'))
    # Get the hexadecimal representation of the hash
    md5_result = md5_hash.hexdigest()
    return md5_result

def handle_text_message(message, whatsapp_id, customer_name):
    crm_lead_doc = get_crm_lead(whatsapp_id, customer_name)

    if "I want to purchase" in message:
        crm_lead_doc.action = ""
        crm_lead_doc.save(ignore_permissions=True)
        send_message(crm_lead_doc, whatsapp_id, TERMS_AND_CONDITION_1)
        send_interactive_message(crm_lead_doc, whatsapp_id, TERMS_AND_CONDITION_2, TERMS_AND_CONDITION_BUTTON)
    elif "Hi, I want to redeem" in message:
        customer_vouchers = get_customer_vouchers(crm_lead_doc.name)

        if not customer_vouchers:
            crm_lead_doc.action = ""
            crm_lead_doc.save(ignore_permissions=True)
            send_message(crm_lead_doc, whatsapp_id, NO_VOUCHER_MESSAGE)
        else:
            crm_lead_doc.action = "Redeem Voucher"
            crm_lead_doc.save(ignore_permissions=True)
            send_message(crm_lead_doc, whatsapp_id, ENTER_VOUCHER_COUNT_MESSAGE)
    elif not message.isdigit() and crm_lead_doc.action == "Redeem Voucher":
        send_message(crm_lead_doc, whatsapp_id, INVALID_VOUCHER_COUNT_MESSAGE)
    elif message.isdigit() and crm_lead_doc.action == "Redeem Voucher":
        customer_vouchers = get_customer_vouchers(crm_lead_doc.name)

        if len(customer_vouchers) >= int(message):
            redeem_voucher_confirmation_button = [
                {
                    "type": "reply",
                    "reply": {
                        "id": "confirm-redeem-{0}".format(message),
                        "title": "Yes" 
                    }
                },
                {
                    "type": "reply",
                    "reply": {
                        "id": "cancel-redeem",
                        "title": "No" 
                    }
                }
            ]
            send_interactive_message(crm_lead_doc, whatsapp_id, REDEEM_VOUCHER_CONFIRMATION_MESSAGE.format(message), redeem_voucher_confirmation_button)
        else:
            send_message(crm_lead_doc, whatsapp_id, INSUFFICIENT_VOUCHER_COUNT_MESSAGE)

def handle_interactive_message(interactive_id, whatsapp_id, customer_name):
    crm_lead_doc = get_crm_lead(whatsapp_id, customer_name)

    if interactive_id == "accept-tnc":
        crm_lead_doc.action = ""
        crm_lead_doc.save(ignore_permissions=True)
        send_interactive_message(crm_lead_doc, whatsapp_id, CHOOSE_PRODUCT_MESSAGE, CHOOSE_PRODUCT_BUTTON)
    elif "voucher" in interactive_id:
        payment_url = generate_payment_url(crm_lead_doc, interactive_id.replace("-", " ").capitalize())
        send_interactive_cta_message(crm_lead_doc, whatsapp_id, MAKE_PAYMENT_MESSAGE, payment_url)
    elif "confirm-redeem-" in interactive_id and crm_lead_doc.action == "Redeem Voucher":
        customer_vouchers = get_customer_vouchers(crm_lead_doc.name)

        voucher_list_message = "Here's your code! 🎉\nPlease show it to our front desk to redeem your hours. 😊\nFor your security, kindly keep the code private and don't share it with others. 🔒\n\n"

        for i in range(int(interactive_id.replace("confirm-redeem-", ""))):
            voucher_list_message += customer_vouchers[i].code
            if i != (int(interactive_id.replace("confirm-redeem-", "")) - 1):
                voucher_list_message += "\n"

        crm_lead_doc.action = ""
        crm_lead_doc.save(ignore_permissions=True)
        send_message(crm_lead_doc, whatsapp_id, voucher_list_message)
    elif interactive_id == "cancel-redeem" and crm_lead_doc.action == "Redeem Voucher":
        send_message(crm_lead_doc, whatsapp_id, ENTER_VOUCHER_COUNT_MESSAGE)
    else:
        send_message(crm_lead_doc, whatsapp_id, DO_NOT_UNDERSTAND_MESSAGE)

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

def send_interactive_cta_message(crm_lead_doc, whatsapp_id, text, url):
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
                    "display_text": "Make Payment",
                    "url": url
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
                "message": "{0}\n\n{1}".format(text, url),
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

def get_customer_vouchers(crm_lead):
    values = {
        "crm_lead": crm_lead
    }

    return frappe.db.sql("""
        SELECT
            wv.code
        FROM `tabWhatsapp Order` wo
        JOIN `tabWhatsapp Voucher` wv
        ON wv.whatsapp_order = wo.name
        WHERE wo.crm_lead = %(crm_lead)s
        AND wv.status = "Issued"
        ORDER BY wv.code
    """,values=values, as_dict=1)

@frappe.whitelist(allow_guest=True)
def fiuu_callback():
    if not validate_webhook_request():
        return

    whatsapp_orders = frappe.db.get_all("Whatsapp Order", filters={"name": frappe.form_dict.orderid}, pluck="name")
    if not whatsapp_orders:
        return

    whatsapp_order_doc = frappe.get_doc("Whatsapp Order", whatsapp_orders[0])
    whatsapp_order_doc.payment_status = PAYMENT_STATUS_MAPPING[frappe.form_dict.status]
    whatsapp_order_doc.transaction_id = frappe.form_dict.tranID
    whatsapp_order_doc.save(ignore_permissions=True)
    if whatsapp_order_doc.payment_status == "Completed":
        issue_voucher(whatsapp_order_doc)
        crm_lead_doc = frappe.get_doc("CRM Lead", whatsapp_order_doc.crm_lead)
        send_message(crm_lead_doc, crm_lead_doc.mobile_no, COMPLETED_BOOKING_MESSAGE)

@frappe.whitelist(allow_guest=True)
def fiuu_notification():
    if not validate_webhook_request():
        return

    whatsapp_orders = frappe.db.get_all("Whatsapp Order", filters={"name": frappe.form_dict.orderid}, pluck="name")
    if not whatsapp_orders:
        return

    whatsapp_order_doc = frappe.get_doc("Whatsapp Order", whatsapp_orders[0])
    whatsapp_order_doc.payment_status = PAYMENT_STATUS_MAPPING[frappe.form_dict.status]
    whatsapp_order_doc.transaction_id = frappe.form_dict.tranID
    whatsapp_order_doc.save(ignore_permissions=True)
    if whatsapp_order_doc.payment_status == "Completed":
        issue_voucher(whatsapp_order_doc)
        crm_lead_doc = frappe.get_doc("CRM Lead", whatsapp_order_doc.crm_lead)
        send_message(crm_lead_doc, crm_lead_doc.mobile_no, COMPLETED_BOOKING_MESSAGE)

def validate_webhook_request():
    fiuu_settings = frappe.get_single("Fiuu Settings")

    secret_key = fiuu_settings.get_password("secret_key")
    tranID = frappe.form_dict.tranID
    orderid = frappe.form_dict.orderid
    status = frappe.form_dict.status
    domain = frappe.form_dict.domain
    amount = frappe.form_dict.amount
    currency = frappe.form_dict.currency
    appcode = frappe.form_dict.appcode
    paydate = frappe.form_dict.paydate
    skey = frappe.form_dict.skey

    key_1 = calculate_md5(tranID + orderid + status + domain + amount + currency)
    key_2 = calculate_md5(paydate + domain + key_1 + appcode + secret_key)

    if skey != key_2:
        return False
    
    return True

def issue_voucher(whatsapp_order_doc):
    for whatsapp_order_product in whatsapp_order_doc.whatsapp_order_product:
        whatsapp_product_doc = frappe.get_doc("Whatsapp Product", whatsapp_order_product.whatsapp_product)
        quantity_to_issue = whatsapp_order_product.quantity * whatsapp_product_doc.voucher_count
        issue_whatsapp_voucher(whatsapp_order_doc, whatsapp_order_product.whatsapp_product, quantity_to_issue)

def issue_whatsapp_voucher(whatsapp_order_doc, whatsapp_product, quantity):
    whatsapp_vouchers = frappe.db.get_all("Whatsapp Voucher", filters={"status": "Available", "whatsapp_product": whatsapp_product}, pluck="name", limit=cint(quantity))
    if len(whatsapp_vouchers) != cint(quantity):
        frappe.throw("Not enough voucher for product {0}".format(whatsapp_product))
    for whatsapp_voucher in whatsapp_vouchers:
        whatsapp_voucher_doc = frappe.get_doc("Whatsapp Voucher", whatsapp_voucher)
        whatsapp_voucher_doc.status = "Issued"
        whatsapp_voucher_doc.whatsapp_order = whatsapp_order_doc.name
        whatsapp_voucher_doc.save(ignore_permissions=True)

@frappe.whitelist()
def redeem_whatsapp_vouchers():
    frappe.response["success"] = False
    vouchers_redeemed = []
    customer_names = []
    try:
        if frappe.form_dict.vouchers_to_redeem["codes"]:
            for code in frappe.form_dict.vouchers_to_redeem["codes"]:
                whatsapp_vouchers = frappe.db.get_all("Whatsapp Voucher", filters={"name": code}, fields=["name", "status"])
                if whatsapp_vouchers:
                    if whatsapp_vouchers[0].status == "Issued":
                        whatsapp_voucher = frappe.get_doc("Whatsapp Voucher", whatsapp_vouchers[0].name)
                        whatsapp_voucher.status = "Redeemed"
                        whatsapp_voucher.redeemed_at = get_datetime()
                        whatsapp_voucher.save(ignore_permissions=True)
                        if whatsapp_voucher.whatsapp_order:
                            customer_name = frappe.db.get_value("Whatsapp Order", whatsapp_voucher.whatsapp_order, "whatsapp_customer")
                            if customer_name not in customer_names:
                                customer_names.append(customer_name)
                        vouchers_redeemed.append(code)
            for customer_name in customer_names:
                send_message(customer_name, REDEEMED_VOUCHER_MESSAGE)
            frappe.response["success"] = True
            frappe.response["message"] = "successfully updated whatsapp vouchers listed in array vouchers_redeemed"
            frappe.response["vouchers_redeemed"] = vouchers_redeemed
        else:
            frappe.response["message"] = "missing codes in request body"
            frappe.response["vouchers_redeemed"] = vouchers_redeemed
    except KeyError:
        frappe.response["message"] = "invalid request body"
        frappe.response["vouchers_redeemed"] = vouchers_redeemed
    except TypeError:
        frappe.response["message"] = "invalid request body"
        frappe.response["vouchers_redeemed"] = vouchers_redeemed

@frappe.whitelist()
def refund_whatsapp_vouchers():
    frappe.response["success"] = False
    vouchers_refunded = []
    try:
        if frappe.form_dict.vouchers_to_refund["codes"]:
            for code in frappe.form_dict.vouchers_to_refund["codes"]:
                whatsapp_vouchers = frappe.db.get_all("Whatsapp Voucher", filters={"name": code}, fields=["name", "status"])
                if whatsapp_vouchers:
                    if whatsapp_vouchers[0].status == "Redeemed":
                        whatsapp_voucher = frappe.get_doc("Whatsapp Voucher", whatsapp_vouchers[0].name)
                        whatsapp_voucher.status = "Issued"
                        whatsapp_voucher.redeemed_at = ""
                        whatsapp_voucher.save(ignore_permissions=True)
                        vouchers_refunded.append(code)
            frappe.response["success"] = True
            frappe.response["message"] = "successfully updated Whatsapp Vouchers listed in array vouchers_refunded"
            frappe.response["vouchers_refunded"] = vouchers_refunded
        else:
            frappe.response["message"] = "missing codes in request body"
            frappe.response["vouchers_refunded"] = vouchers_refunded
    except KeyError:
        frappe.response["message"] = "invalid request body"
        frappe.response["vouchers_refunded"] = vouchers_refunded
    except TypeError:
        frappe.response["message"] = "invalid request body"
        frappe.response["vouchers_refunded"] = vouchers_refunded

def send_follow_up_message():
    whatsapp_vouchers_to_follow_up = frappe.db.sql("""
        SELECT
            wv.name AS voucher_name, cl.name AS crm_lead_id, cl.first_name AS customer_name
        FROM `tabWhatsapp Voucher` wv
        JOIN `tabWhatsapp Order` wo
        ON wv.whatsapp_order = wo.name
        JOIN `tabCRM Lead` cl
        ON wo.crm_lead = cl.name
        WHERE wv.redeemed_at <= DATE_SUB(NOW(), INTERVAL 2 HOUR)
        AND done_follow_up = 0
    """, as_dict=1)

    for whatsapp_voucher in whatsapp_vouchers_to_follow_up:
        crm_lead_doc = frappe.get_doc("CRM Lead", whatsapp_voucher.crm_lead_id)
        send_message(crm_lead_doc, whatsapp_voucher.customer_name, FOLLOW_UP_MESSAGE)
        whatsapp_voucher_doc = frappe.get_doc("Whatsapp Voucher", whatsapp_voucher.voucher_name)
        whatsapp_voucher_doc.done_follow_up = 1
        whatsapp_voucher_doc.save(ignore_permissions=True)