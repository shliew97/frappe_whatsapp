# Copyright (c) 2025, Byondwave Innovations Sdn Bhd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import enqueue
from pywebpush import webpush, WebPushException
import json

class PushNotificationLog(Document):
    def after_insert(self):
        enqueue(method=send_push_notification, doc=self, is_async=True)

def send_push_notification(doc):
    push_notification_subscription = frappe.get_doc("Push Notification Subscription", doc.push_notification_subscription)

    options = {
        "body": doc.message,
        "url": doc.url,
        "id": doc.name
    }

    result = trigger_push_notification(
        push_notification_subscription.endpoint,
        push_notification_subscription.p256dh,
        push_notification_subscription.auth,
        doc.title,
        options
    )

    if result:
        doc.sent = True
        doc.save(ignore_permissions=True)

def trigger_push_notification(endpoint, p256dh, auth, title, options):
    try:
        webpush_settings = frappe.get_doc("Web Push Settings")
        response = webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {
                    "p256dh": p256dh,
                    "auth": auth
                }
            },
            data=json.dumps({"title": title, "options": options}),
            vapid_private_key=webpush_settings.get_password("private_key"),
            vapid_claims={
                "sub": "mailto:{}".format("senghan.liew@byondwave.com")
            }
        )
        return response.ok
    except WebPushException as ex:
        print(ex)
        return False