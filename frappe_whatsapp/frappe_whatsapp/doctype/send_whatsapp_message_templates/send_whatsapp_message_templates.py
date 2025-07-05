# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe_whatsapp.api import enqueue_send_whatsapp_template


class SendWhatsAppMessageTemplates(Document):
	@frappe.whitelist()
	def send_whatsapp_message_template(self):
		frappe.response["success"] = False
		mobile_nos = extract_hash_parts(self.mobile_no)
		whatsapp_template_queues = []
		for mobile_no in mobile_nos:
			wtq_doc = frappe.get_doc({
				"doctype": "WhatsApp Template Queue",
				"phone_number": mobile_no
			})
			wtq_doc.insert(ignore_permissions=True)
			whatsapp_template_queues.append(wtq_doc.name)
		enqueue_send_whatsapp_template(self.whatsapp_message_templates, whatsapp_template_queues)
		frappe.response["success"] = True

def extract_hash_parts(s):
	parts = s.split('#')
	if all(parts):
		return parts
	return None