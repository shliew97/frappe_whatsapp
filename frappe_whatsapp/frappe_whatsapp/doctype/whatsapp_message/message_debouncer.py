"""
Message Debouncer for WhatsApp Messages
Handles batching of multiple rapid messages from the same user
"""

import frappe
import json
from frappe.utils import now
from frappe.utils.background_jobs import enqueue


DEBOUNCE_TIMEOUT = 2  # seconds - configurable via WhatsApp Settings
INCOMPLETE_MESSAGE_TIMEOUT = 5  # seconds - for incomplete messages


def get_debounce_timeout():
    """Get debounce timeout from settings or use default"""
    try:
        settings = frappe.get_single("WhatsApp Settings")
        return getattr(settings, "message_debounce_timeout", DEBOUNCE_TIMEOUT)
    except:
        return DEBOUNCE_TIMEOUT


def get_incomplete_message_timeout():
    """Get incomplete message timeout from settings or use default"""
    try:
        settings = frappe.get_single("WhatsApp Settings")
        return getattr(settings, "incomplete_message_timeout", INCOMPLETE_MESSAGE_TIMEOUT)
    except:
        return INCOMPLETE_MESSAGE_TIMEOUT


def get_redis_key(whatsapp_id):
    """Generate Redis key for storing queued messages"""
    return f"whatsapp_message_queue:{whatsapp_id}"


def get_processing_key(whatsapp_id):
    """Generate Redis key to track if processing is scheduled"""
    return f"whatsapp_processing_scheduled:{whatsapp_id}"


def queue_message(message_doc, is_incomplete=False):
    """
    Queue an incoming message for debounced processing

    Args:
        message_doc: WhatsApp Message document
        is_incomplete: If True, use longer timeout for incomplete messages
    """
    print(f"[DEBUG] queue_message CALLED for message: {message_doc.message}")

    redis = frappe.cache()
    whatsapp_id = message_doc.get("from")
    redis_key = get_redis_key(whatsapp_id)
    processing_key = get_processing_key(whatsapp_id)

    print(f"[DEBUG] WhatsApp ID: {whatsapp_id}")

    # Message data to queue
    message_data = {
        "message": message_doc.message,
        "content_type": message_doc.content_type,
        "from": message_doc.get("from"),
        "from_name": message_doc.get("from_name"),
        "timestamp": str(message_doc.timestamp or now()),
        "reference_name": message_doc.reference_name,
        "reference_doctype": message_doc.reference_doctype,
        "name": message_doc.name,
    }

    # Get existing queue or create new
    queued_messages = redis.get(redis_key)
    if queued_messages:
        queued_messages = json.loads(queued_messages)
    else:
        queued_messages = []

    # Add new message to queue
    queued_messages.append(message_data)

    # Store back in Redis with expiry (double the debounce timeout to be safe)
    # Always use 10 second timeout
    timeout = 10  # Fixed 10 seconds for all messages

    redis.setex(redis_key, timeout * 2, json.dumps(queued_messages))

    # Check if processing is already scheduled
    is_scheduled = redis.get(processing_key)
    print(f"[DEBUG] Is processing already scheduled? {is_scheduled}")

    if not is_scheduled:
        # Schedule processing after debounce timeout
        redis.setex(processing_key, timeout + 1, "1")  # Mark as scheduled
        print(f"[DEBUG] Marked as scheduled in Redis")

        # Enqueue delayed processing
        print(f"[DEBUG] About to enqueue background job for {whatsapp_id}...")
        job = enqueue(
            method="frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.message_debouncer.process_queued_messages",
            whatsapp_id=whatsapp_id,
            queue="short",
            is_async=True,
            timeout=300,  # Max execution time (not delay) - give plenty of time for sleep + processing
        )
        print(f"[DEBUG] Background job enqueued successfully! Job: {job}")
        print(
            f"[DEBUG] Scheduled processing for {whatsapp_id}. "
            f"Timeout: {timeout}. "
            f"Queue size: 1"
        )


        frappe.log_error(
            "Message Debouncer",
            f"Scheduled processing for {whatsapp_id} after {timeout}s. Queue size: 1"
        )
    else:

        print(
            f"Processing already scheduled for {whatsapp_id}. "
            f"Timeout: {timeout}. "
            f"Added to queue. New size: {len(queued_messages)}"
        )
        frappe.log_error(
            "Message Debouncer",
            f"Processing already scheduled for {whatsapp_id}. Added to queue. New size: {len(queued_messages)}"
        )


def process_queued_messages(whatsapp_id):
    """
    Process all queued messages for a user after debounce timeout

    Args:
        whatsapp_id: WhatsApp ID of the sender
    """
    import time

    print(f"[DEBUG] process_queued_messages STARTED for {whatsapp_id}")

    # Wait for debounce timeout - fixed 10 seconds
    timeout = 10
    print(f"[DEBUG] Sleeping for {timeout} seconds...")
    time.sleep(timeout)
    print(f"[DEBUG] Woke up after {timeout} seconds")

    redis = frappe.cache()
    redis_key = get_redis_key(whatsapp_id)
    processing_key = get_processing_key(whatsapp_id)

    # Get all queued messages
    queued_messages = redis.get(redis_key)
    print(f"[DEBUG] Retrieved from Redis: {queued_messages}")

    # Clear the queue and processing flag
    redis.delete(redis_key)
    redis.delete(processing_key)

    if not queued_messages:
        print(f"[DEBUG] NO MESSAGES in queue for {whatsapp_id}")
        frappe.log_error("Message Debouncer", f"No messages in queue for {whatsapp_id}")
        return

    queued_messages = json.loads(queued_messages)
    print(f"[DEBUG] Parsed {len(queued_messages)} message(s)")

    frappe.log_error(
        "Message Debouncer",
        f"Processing {len(queued_messages)} queued message(s) for {whatsapp_id}"
    )

    # Get CRM Lead document
    if not queued_messages:
        return

    first_message = queued_messages[0]
    crm_lead_doc = frappe.get_doc(first_message["reference_doctype"], first_message["reference_name"])

    # Process based on content type and lead type
    if crm_lead_doc.is_outlet_frontdesk:
        # For outlet frontdesk, process each message separately
        from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import handle_outlet_frontdesk
        for msg in queued_messages:
            handle_outlet_frontdesk(msg["message"], msg["from"], crm_lead_doc)
    else:
        # For regular chats, combine text messages and process together
        text_messages = [msg for msg in queued_messages if msg["content_type"] == "text"]
        print(f"[DEBUG] Found {len(text_messages)} text message(s)")

        if text_messages:
            # Combine all text messages with newlines
            combined_message = "\n".join([msg["message"] for msg in text_messages])
            print(f"[DEBUG] Combined message: {combined_message}")

            # Get data from the most recent message
            latest_message = text_messages[-1]

            # Import handlers
            from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
                handle_text_message,
                handle_text_message_ai
            )

            # Process the combined message
            print(f"[DEBUG] Calling handle_text_message_ai with combined message")
            frappe.log_error(
                "Message Debouncer",
                f"Processing combined message ({len(text_messages)} messages):\n{combined_message}"
            )

            handle_text_message(
                combined_message,
                latest_message["from"],
                latest_message["from_name"],
                crm_lead_doc
            )
            print(f"[DEBUG] handle_text_message completed")

            handle_text_message_ai(
                combined_message,
                latest_message["from"],
                latest_message["from_name"],
                crm_lead_doc
            )
            print(f"[DEBUG] handle_text_message_ai completed")

        # Process non-text messages separately (flows, buttons, etc.)
        non_text_messages = [msg for msg in queued_messages if msg["content_type"] != "text"]

        from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
            handle_interactive_message,
            handle_template_message_reply
        )

        for msg in non_text_messages:
            if msg["content_type"] == "flow":
                # Get interactive_id from the original message doc
                msg_doc = frappe.get_doc("WhatsApp Message", msg["name"])
                handle_interactive_message(
                    msg_doc.interactive_id,
                    msg["from"],
                    msg["from_name"],
                    crm_lead_doc
                )
            elif msg["content_type"] == "button":
                # Get reply details from the original message doc
                msg_doc = frappe.get_doc("WhatsApp Message", msg["name"])
                if msg_doc.is_reply and msg_doc.reply_to_message_id:
                    handle_template_message_reply(
                        msg["from"],
                        msg["from_name"],
                        msg["message"],
                        msg_doc.reply_to_message_id,
                        crm_lead_doc
                    )


def should_debounce_message(message_doc):
    """
    Determine if a message should be debounced

    Args:
        message_doc: WhatsApp Message document

    Returns:
        tuple: (should_debounce: bool, is_incomplete: bool)
    """
    print(f"[DEBUG] should_debounce_message called for: {message_doc.message}")
    print(f"[DEBUG] Message type: {message_doc.type}, content_type: {message_doc.content_type}")

    # Only debounce incoming text messages
    if message_doc.type != "Incoming":
        print(f"[DEBUG] Not incoming, skipping debounce")
        return (False, False)

    # # Check if debouncing is enabled in settings
    # try:
    #     settings = frappe.get_single("WhatsApp Settings")
    #     debounce_enabled = getattr(settings, "enable_message_debouncing", False)
    #     if not debounce_enabled:
    #         return (False, False)
    # except:
    #     return (False, False)

    # NEW: Add LLM-based completeness check for text messages
    # ALWAYS queue text messages, LLM just provides context for logging
    if message_doc.content_type in ["text"]:
        from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.message_completeness_detector import is_message_incomplete

        try:
            result = is_message_incomplete(
                message_doc.message,
                message_doc.get("from"),
                message_doc.reference_doctype,
                message_doc.reference_name
            )

            frappe.log_error(
                "Message Completeness Check",
                f"Status: {'INCOMPLETE' if result.get('is_incomplete') else 'COMPLETE'}\n"
                f"Reason: {result.get('reason', 'unknown')}\n"
                f"Confidence: {result.get('confidence', 0)}\n"
                f"Message: {message_doc.message}\n"
                f"Action: Queuing for 10s (all messages are queued)"
            )

            # Always queue text messages with 10s timeout
            print(f"[DEBUG] Returning (True, True) - will queue message")
            return (True, True)

        except Exception as e:
            # Still queue on error
            frappe.log_error(
                "Completeness Check Error",
                f"Error: {str(e)}\nStill queuing message"
            )
            return (True, True)

    return (True, False)  # Default debounce for non-text
