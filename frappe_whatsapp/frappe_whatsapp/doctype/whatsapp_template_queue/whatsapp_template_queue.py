# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class WhatsAppTemplateQueue(Document):
	def before_insert(self):
		self.phone_number = (self.phone_number or "").strip()
		self.customer_name = (self.customer_name or "").strip()
