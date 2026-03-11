"""
Message Completeness Detector for WhatsApp Messages
Uses LLM to detect if a message is incomplete (user still typing)
"""

import frappe
import json
from crm.api.whatsapp import get_whatsapp_messages

# Import LangChain for LLM integration
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage
    LANGCHAIN_AVAILABLE = True
except ImportError as e:
    LANGCHAIN_AVAILABLE = False
    frappe.log_error(
        "LangChain Import Error",
        f"LangChain packages not installed. Message completeness detection disabled. Error: {str(e)}"
    )


def get_recent_chat_context(from_whatsapp_id, reference_doctype, reference_name, limit=3):
    """
    Get recent chat history for context

    Args:
        from_whatsapp_id: WhatsApp ID of sender
        reference_doctype: Reference doctype (usually "CRM Lead")
        reference_name: Reference name (CRM Lead ID)
        limit: Number of recent messages to fetch

    Returns:
        str: Formatted chat history
    """
    try:
        # Fetch recent messages
        messages = get_whatsapp_messages(reference_doctype, reference_name)

        if not messages:
            return ""

        # Get last N messages
        recent_messages = messages[-limit:] if len(messages) > limit else messages

        # Format chat history
        formatted_history = []
        for msg in recent_messages:
            sender = "User" if msg.get("type") == "Incoming" else "Assistant"
            text = msg.get("message", "")
            formatted_history.append(f"{sender}: {text}")

        return "\n".join(formatted_history)

    except Exception as e:
        frappe.log_error(
            "Chat Context Error",
            f"Error fetching chat context: {str(e)}\n{frappe.get_traceback()}"
        )
        return ""


def is_message_incomplete(message_text, from_whatsapp_id, reference_doctype, reference_name):
    """
    Use LLM to determine if a message is incomplete (user still typing)

    Args:
        message_text: The message text to analyze
        from_whatsapp_id: WhatsApp ID of sender
        reference_doctype: Reference doctype (usually "CRM Lead")
        reference_name: Reference name (CRM Lead ID)

    Returns:
        dict: {
            'is_incomplete': bool,
            'confidence': float,
            'reason': str
        }
    """
    # Default response (fail-safe: treat as complete)
    default_response = {
        'is_incomplete': False,
        'confidence': 0.0,
        'reason': 'LLM unavailable or error - defaulting to complete'
    }

    # Check if LangChain is available
    if not LANGCHAIN_AVAILABLE:
        return default_response

    try:
        # Get OpenAI API key from WhatsApp Settings
        settings = frappe.get_single("WhatsApp Settings")
        api_key = settings.get_password("openai_api_key")

        if not api_key:
            frappe.log_error(
                "Message Completeness",
                "OpenAI API key not configured in WhatsApp Settings"
            )
            return default_response

        # Get recent chat context
        chat_history = get_recent_chat_context(
            from_whatsapp_id,
            reference_doctype,
            reference_name,
            limit=3
        )

        # Initialize ChatOpenAI with GPT-4o-mini (fast, cost-effective)
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,  # Consistent results
            api_key=api_key,
            timeout=10,  # 10 second timeout
            max_retries=2
        )

        # Build prompt
        system_prompt = """You are analyzing WhatsApp messages to determine if a user is still typing or has completed their thought.

INCOMPLETE indicators:
- Mid-sentence: "I want to book...", "My name is"
- Trailing conjunctions: ending with "and", "but", "also", "or"
- Explicit continuations: "wait", "let me check", "one sec", "hold on"
- Incomplete questions: "Can I", "What about"
- Lists being built without conclusion

COMPLETE indicators:
- Full sentences with proper ending
- Questions with "?" indicating complete question
- Greetings/acknowledgments: "Hi", "Thanks", "Ok", "Yes", "No"
- Direct commands: "Book", "Cancel", "Confirm"
- Complete information blocks

Respond ONLY with JSON in this exact format:
{"is_incomplete": true/false, "confidence": 0.0-1.0, "reason": "brief explanation"}"""

        user_prompt = f"""Chat History:
{chat_history if chat_history else "(No previous messages)"}

Current Message:
"{message_text}"

Is this message INCOMPLETE (user likely sending more)? Respond with JSON only."""

        # Call LLM
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]

        response = llm.invoke(messages)
        response_text = response.content.strip()

        # Parse JSON response
        try:
            # Extract JSON from response (handle cases where LLM adds extra text)
            if '```json' in response_text:
                # Extract JSON from code block
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1
                response_text = response_text[json_start:json_end]
            elif '```' in response_text:
                # Remove code block markers
                response_text = response_text.replace('```', '').strip()

            result = json.loads(response_text)

            # Validate response structure
            if 'is_incomplete' not in result:
                raise ValueError("Missing 'is_incomplete' field in LLM response")

            # Ensure proper types
            result['is_incomplete'] = bool(result.get('is_incomplete', False))
            result['confidence'] = float(result.get('confidence', 0.0))
            result['reason'] = str(result.get('reason', 'No reason provided'))

            return result

        except (json.JSONDecodeError, ValueError) as e:
            frappe.log_error(
                "LLM Response Parse Error",
                f"Failed to parse LLM response as JSON:\n{response_text}\nError: {str(e)}"
            )
            return default_response

    except Exception as e:
        frappe.log_error(
            "Message Completeness Error",
            f"Error checking message completeness:\n"
            f"Message: {message_text}\n"
            f"Error: {str(e)}\n"
            f"{frappe.get_traceback()}"
        )
        return default_response
