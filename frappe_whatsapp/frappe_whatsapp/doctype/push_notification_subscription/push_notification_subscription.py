# Copyright (c) 2025, Byondwave Innovations Sdn Bhd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PushNotificationSubscription(Document):
    def after_insert(self):
        if self.permission == "granted":
            push_notification = frappe.new_doc("Push Notification Log")
            push_notification.push_notification_subscription = self.name
            push_notification.title = "Successfully subscribed"
            push_notification.message = "You will now get notified on the latest updates"
            push_notification.insert(ignore_permissions=True)

    @frappe.whitelist()
    def send_push_notification(self, title, message, url=None):
        push_notification = frappe.new_doc("Push Notification Log")
        push_notification.push_notification_subscription = self.name
        push_notification.title = title
        push_notification.message = message
        push_notification.url = url
        push_notification.insert(ignore_permissions=True)