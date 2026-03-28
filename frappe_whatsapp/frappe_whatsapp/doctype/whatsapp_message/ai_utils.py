import frappe
from datetime import datetime

def is_confirmation_message(message, context=None):
    """
    Check if the message is a confirmation (yes, confirm, correct, etc.) using LLM.

    Uses LLM to intelligently detect if user is saying "yes" in various forms.

    Args:
        message: User's message text
        context: Optional context ('awaiting_confirmation', 'awaiting_update', or None)

    Returns:
        bool: True if message is a confirmation
    """
    # First try simple keyword matching for common cases (faster)
    message_lower = message.lower().strip()
    simple_confirmations = ['yes', 'yup', 'yeah', 'yep', 'ok', 'okay', 'confirm', 'ya', 'betul', 'boleh']

    if message_lower in simple_confirmations:
        return True

    # For more complex cases, use LLM
    try:
        # Only use LLM for short messages (under 50 chars) to avoid processing long messages
        if len(message) > 50:
            return False

        from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.agents.rag_chain import detect_yes_no_with_llm
        result = detect_yes_no_with_llm(message, context=context)
        return result == 'yes'
    except Exception as e:
        frappe.log_error("Confirmation Detection Error", f"Error detecting confirmation with LLM: {str(e)}")
        # Fallback to keyword matching
        confirmation_keywords = [
            'yes', 'yup', 'yeah', 'yep', 'correct', 'right',
            'confirm', 'confirmed', 'ok', 'okay', 'proceed',
            'continue', 'good', 'looks good', 'all good',
            'betul', 'ya', 'okie', 'boleh'
        ]

        if len(message_lower) <= 30:
            for keyword in confirmation_keywords:
                if (message_lower.startswith(keyword + ' ') or
                    message_lower.startswith(keyword + ',') or
                    message_lower.startswith(keyword + '.') or
                    message_lower.startswith(keyword + '!')):
                    return True

        return False

def is_change_request(message, context=None):
    """
    Check if the message is requesting to change booking details using LLM.

    Uses LLM to intelligently detect if user is saying "no" or wants to make changes.

    Args:
        message: User's message text
        context: Optional context ('awaiting_confirmation', 'awaiting_update', or None)

    Returns:
        bool: True if message is requesting changes
    """
    # First try simple keyword matching for common cases (faster)
    message_lower = message.lower().strip()
    simple_rejections = ['no', 'nope', 'wrong', 'change', 'tidak', 'tak']

    if message_lower in simple_rejections:
        return True

    # For more complex cases, use LLM
    try:
        # Only use LLM for short messages (under 50 chars) to avoid processing long messages
        if len(message) > 50:
            return False

        from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.agents.rag_chain import detect_yes_no_with_llm
        result = detect_yes_no_with_llm(message, context=context)
        return result == 'no'
    except Exception as e:
        frappe.log_error("Change Request Detection Error", f"Error detecting change request with LLM: {str(e)}")
        # Fallback to keyword matching
        change_keywords = [
            'no', 'nope', 'wrong', 'change', 'edit', 'modify',
            'incorrect', 'not correct', 'mistake', 'error',
            'tidak', 'tak', 'salah', 'ubah', 'tukar'
        ]

        if len(message_lower) <= 30:
            for keyword in change_keywords:
                if (message_lower.startswith(keyword + ' ') or
                    message_lower.startswith(keyword + ',') or
                    message_lower.startswith(keyword + '.') or
                    message_lower.startswith(keyword + '!')):
                    return True

        return False


def is_general_question(message):
    """
    Check if the message is a general question (not booking-related).
    This allows users to ask questions even while in booking flow.

    Args:
        message: User's message text

    Returns:
        bool: True if message is a general question
    """
    # Question patterns - English
    question_keywords = [
        'what', 'when', 'where', 'how', 'why', 'who', 'which',
        'can you', 'could you', 'do you', 'does', 'is there', 'are there',
        'can i ask', 'can i know', 'may i know',
        'tell me', 'explain', 'what is', 'what are', 'how much', 'how long',
        'want to know', 'like to know', 'interested to know', 'curious',
        'any info', 'more info', 'information',
        'price', 'cost', 'location', 'outlet', 'operating hours', 'open',
        'available', 'offer', 'provide', 'difference', 'compare',
        'package', 'promotion', 'discount', 'membership',
        'recommend', 'suggestion', 'suggest',
        'how to book',  # asking about booking process, not actually booking
    ]

    # Question patterns - Malay
    malay_question_keywords = [
        'apa', 'bila', 'mana', 'macam mana', 'kenapa', 'siapa',
        'berapa', 'ada tak', 'ada ke', 'boleh tak', 'boleh ke',
        'nak tahu', 'nak tanya', 'tanya sikit',
    ]

    message_lower = message.lower().strip()

    # Check if it's a question
    has_question_mark = '?' in message
    has_question_word = any(keyword in message_lower for keyword in question_keywords)
    has_malay_question = any(keyword in message_lower for keyword in malay_question_keywords)

    # Exclude confirmation responses (yes/no)
    is_confirmation = is_confirmation_message(message) or is_change_request(message)

    return (has_question_mark or has_question_word or has_malay_question) and not is_confirmation


def analyze_confirmation_response_intent(message, pending_booking_data):
    """
    Use LLM to intelligently analyze user's response during booking confirmation.

    Detects whether the user wants to:
    1. Update specific fields with provided values (e.g., "no change name to john", "change time to 3pm")
    2. Make changes but hasn't specified what (e.g., just "no" or "change")

    Args:
        message: User's message text
        pending_booking_data: Current pending booking data

    Returns:
        dict: {
            'intent': 'update_fields' or 'wants_to_change',
            'field_updates': {field: value} if update_fields, or {} if wants_to_change
        }
    """
    from langchain_openai import ChatOpenAI
    import json

    # Initialize LLM
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=frappe.conf.get("openai_api_key")
    )

    # Create prompt for intent analysis
    prompt = f"""You are analyzing a user's response during a booking confirmation flow.

CONTEXT:
The user was shown their booking details and asked to confirm (Yes/No).
Current booking data:
{json.dumps(pending_booking_data, indent=2, default=str)}

USER'S RESPONSE:
"{message}"

YOUR TASK:
Determine the user's intent:

1. UPDATE_FIELDS INTENT - User wants to CHANGE specific booking fields and provides new values
   Examples:
   - "no change the name to duxton" → Update customer_name to "duxton"
   - "change time to 3pm" → Update timeslot to "3pm"
   - "update outlet to KLCC" → Update outlet to "KLCC"
   - "no change name to john and time to 2pm" → Update customer_name to "john" and timeslot to "2pm"
   - "the name should be david" → Update customer_name to "david"

2. WANTS_TO_CHANGE INTENT - User wants to make changes but hasn't specified what to update
   Examples: "no", "nope", "wrong", "incorrect", "not correct", "change", "edit"

IMPORTANT RULES:
- If the message contains specific field values to update, classify as UPDATE_FIELDS
- Only classify as WANTS_TO_CHANGE if the user is rejecting without providing new field information
- For UPDATE_FIELDS, extract all field updates mentioned
- CRITICAL FOR DATES: Today's date is {datetime.now().strftime('%Y-%m-%d')}. ALL relative date words ("tomorrow", "today", "next Monday", "this Friday", etc.) MUST be calculated relative to TODAY'S date, NOT relative to the existing booking date. For example, if today is 2026-03-28, "tomorrow" means 2026-03-29. Convert dates to YYYY-MM-DD format.
- For times: convert to 24-hour HH:MM:SS format (e.g., "2pm" → "14:00:00")

AVAILABLE FIELDS YOU CAN UPDATE:
- customer_name: Customer's name
- phone: Phone number
- outlet: Outlet location
- booking_date: Preferred date
- timeslot: Preferred time
- pax: Number of people
- treatment_type: Type of treatment
- session: Duration in minutes
- preferred_masseur: Preferred therapist

OUTPUT FORMAT (JSON only, no explanation):
{{
    "intent": "update_fields" or "wants_to_change",
    "field_updates": {{
        "field_name": "new_value",
        ...
    }}
}}

If intent is "wants_to_change", field_updates should be empty {{}}.
If intent is "update_fields", field_updates should contain the extracted updates.
"""

    try:
        # Call LLM
        response = llm.invoke(prompt)
        result_text = response.content.strip()

        # Parse JSON response
        # Remove markdown code blocks if present
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        result = json.loads(result_text)

        frappe.log_error(
            "Confirmation Response Intent Analysis",
            f"User message: {message}\n"
            f"LLM analysis:\n{json.dumps(result, indent=2)}"
        )

        return result

    except Exception as e:
        frappe.log_error(
            "Intent Analysis Error",
            f"Error analyzing confirmation response: {str(e)}\n"
            f"Message: {message}"
        )
        # Default to wants_to_change if analysis fails
        return {
            'intent': 'wants_to_change',
            'field_updates': {}
        }