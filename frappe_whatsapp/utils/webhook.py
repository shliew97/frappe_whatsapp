"""Webhook."""
import frappe
import json
import requests
import time
from werkzeug.wrappers import Response
import frappe.utils
from frappe.utils.background_jobs import enqueue
from datetime import datetime


@frappe.whitelist(allow_guest=True)
def webhook():
	"""Meta webhook."""
	if frappe.request.method == "GET":
		return get()
	enqueue(post, form_dict=dict(frappe.form_dict), queue="short", is_async=True)
	return


def get():
	"""Get."""
	hub_challenge = frappe.form_dict.get("hub.challenge")
	webhook_verify_token = frappe.db.get_single_value(
		"WhatsApp Settings", "webhook_verify_token"
	)

	if frappe.form_dict.get("hub.verify_token") != webhook_verify_token:
		frappe.throw("Verify token does not match")

	return Response(hub_challenge, status=200)

def post(form_dict):
	"""Post."""
	data = form_dict
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Webhook",
		"meta_data": json.dumps(data)
	}).insert(ignore_permissions=True)

	messages = []
	try:
		messages = data["entry"][0]["changes"][0]["value"].get("messages", [])
		contacts = data["entry"][0]["changes"][0]["value"].get("contacts", [])
	except KeyError:
		messages = data["entry"]["changes"][0]["value"].get("messages", [])
		contacts = data["entry"]["changes"][0]["value"].get("contacts", [])

	if messages:
		for message in messages:
			message_type = message['type']
			is_reply = True if message.get('context') and not message.get('context').get('forwarded') else False
			is_forwarded = True if message.get('context') and message.get('context').get('forwarded') else False
			reply_to_message_id = message['context']['id'] if is_reply else None
			if message_type == 'text':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['text']['body'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type": message_type,
					"from_name": contacts[0]["profile"]["name"],
					"timestamp": datetime.fromtimestamp(float(message['timestamp']) + 28800),
					"is_forwarded": is_forwarded,
				}).insert(ignore_permissions=True)
			elif message_type == 'reaction':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['reaction']['emoji'],
					"reply_to_message_id": message['reaction']['message_id'],
					"message_id": message['id'],
					"content_type": "reaction",
					"from_name": contacts[0]["profile"]["name"],
					"timestamp": datetime.fromtimestamp(float(message['timestamp']) + 28800),
					"is_forwarded": is_forwarded,
				}).insert(ignore_permissions=True)
			elif message_type == 'interactive':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['interactive']['button_reply']['title'],
					"interactive_id": message['interactive']['button_reply']['id'],
					"message_id": message['id'],
					"content_type": "flow",
					"from_name": contacts[0]["profile"]["name"],
					"timestamp": datetime.fromtimestamp(float(message['timestamp']) + 28800),
					"is_forwarded": is_forwarded,
				}).insert(ignore_permissions=True)
			elif message_type in ["image", "audio", "video", "document", "sticker"]:
				settings = frappe.get_doc(
							"WhatsApp Settings", "WhatsApp Settings",
						)
				token = settings.get_password("token")
				url = f"{settings.url}/{settings.version}/"


				media_id = message[message_type]["id"]
				headers = {
					'Authorization': 'Bearer ' + token

				}
				response = requests.get(f'{url}{media_id}/', headers=headers)

				if response.status_code == 200:
					media_data = response.json()
					media_url = media_data.get("url")
					mime_type = media_data.get("mime_type")
					file_extension = mime_type.split('/')[1]

					media_response = requests.get(media_url, headers=headers)
					if media_response.status_code == 200:

						file_data = media_response.content
						file_name = f"{frappe.generate_hash(length=10)}.{file_extension}"

						message_doc = frappe.get_doc({
							"doctype": "WhatsApp Message",
							"type": "Incoming",
							"from": message['from'],
							"message_id": message['id'],
							"reply_to_message_id": reply_to_message_id,
							"is_reply": is_reply,
							"message": message[message_type].get("caption",f"/files/{file_name}"),
							"content_type" : message_type,
							"from_name": contacts[0]["profile"]["name"],
							"timestamp": datetime.fromtimestamp(float(message['timestamp']) + 28800),
							"is_forwarded": is_forwarded,
						}).insert(ignore_permissions=True)

						file = frappe.get_doc(
							{
								"doctype": "File",
								"file_name": file_name,
								"attached_to_doctype": "WhatsApp Message",
								"attached_to_name": message_doc.name,
								"content": file_data,
								"attached_to_field": "attach"
							}
						).save(ignore_permissions=True)


						message_doc.attach = file.file_url
						message_doc.save()
			elif message_type == "button":
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['button']['text'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type": message_type,
					"from_name": contacts[0]["profile"]["name"],
					"timestamp": datetime.fromtimestamp(float(message['timestamp']) + 28800),
					"is_forwarded": is_forwarded,
				}).insert(ignore_permissions=True)
			else:
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message_id": message['id'],
					"message": message[message_type].get(message_type),
					"content_type" : message_type,
					"from_name": contacts[0]["profile"]["name"],
					"timestamp": datetime.fromtimestamp(float(message['timestamp']) + 28800),
					"is_forwarded": is_forwarded,
				}).insert(ignore_permissions=True)

	else:
		changes = None
		try:
			changes = data["entry"][0]["changes"][0]
		except KeyError:
			changes = data["entry"]["changes"][0]
		update_status(changes)
	return

def update_status(data):
	"""Update status hook."""
	if data.get("field") == "message_template_status_update":
		update_template_status(data['value'])

	elif data.get("field") == "messages":
		update_message_status(data['value'])

def update_template_status(data):
	"""Update template status."""
	frappe.db.sql(
		"""UPDATE `tabWhatsApp Templates`
		SET status = %(event)s
		WHERE id = %(message_template_id)s""",
		data
	)

def update_message_status(data):
	"""Update message status."""
	id = data['statuses'][0]['id']
	status = data['statuses'][0]['status']
	conversation = data['statuses'][0].get('conversation', {}).get('id')
	name = frappe.db.get_value("WhatsApp Message", filters={"message_id": id})

	if name:
		doc = frappe.get_doc("WhatsApp Message", name)
		if status == "failed":
			doc.flags.ignore_permissions = True
			doc.delete()
		else:
			doc.status = status
			if conversation:
				doc.conversation_id = conversation
			doc.save(ignore_permissions=True)

		whatsapp_api_settings = frappe.get_single("WhatsApp API Settings")
		if whatsapp_api_settings.current_callback_webhook:
			current_callback_webhook = frappe.get_doc("WhatsApp Message Callback Webhook", whatsapp_api_settings.current_callback_webhook)
			headers = {"Content-Type": "application/json"}  # Set headers if needed
			try:
				response = requests.post(current_callback_webhook.url, json=data, headers=headers, timeout=3)
				response.raise_for_status()  # Raise an error for HTTP errors (4xx, 5xx)
			except requests.Timeout:
				pass
			except requests.RequestException as e:
				pass