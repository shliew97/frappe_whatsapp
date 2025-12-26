
import frappe, requests, json, re
from datetime import datetime, timedelta
from crm.api.whatsapp import get_whatsapp_messages
# LangChain imports for AI/RAG functionality
try:
    from langchain_openai import OpenAIEmbeddings, ChatOpenAI
    from langchain_pinecone import PineconeVectorStore
    from langchain_classic.chains import create_retrieval_chain
    from langchain_classic.chains.combine_documents import create_stuff_documents_chain
    from langchain_core.prompts import ChatPromptTemplate
    LANGCHAIN_AVAILABLE = True
except ImportError as e:
    LANGCHAIN_AVAILABLE = False
    print(f"LangChain packages not installed. AI features will be disabled. Error: {str(e)}", "WhatsApp AI Import Error")
# Cache for RAG chain to avoid recreating on every message
_rag_chain_cache = None

def clear_rag_chain_cache():
    """
    Clear the RAG chain cache. Useful for resetting after configuration changes.
    Can be called from console: frappe.call('frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message.clear_rag_chain_cache')
    """
    global _rag_chain_cache
    _rag_chain_cache = None
    frappe.log_error("RAG chain cache cleared", "WhatsApp AI Debug")
    return "RAG chain cache cleared successfully"

def parse_relative_date(date_str):
    """
    Parse relative date strings like 'tomorrow', 'today', 'next week'.

    Args:
        date_str: String like 'tomorrow', 'today', etc.

    Returns:
        str: Date in YYYY-MM-DD format or None
    """
    date_str_lower = date_str.lower().strip()
    today = datetime.now().date()

    if date_str_lower in ['today', 'tdy']:
        return today.strftime('%Y-%m-%d')
    elif date_str_lower in ['tomorrow', 'tmr', 'tmrw']:
        return (today + timedelta(days=1)).strftime('%Y-%m-%d')
    elif date_str_lower in ['day after tomorrow', 'day after tmr']:
        return (today + timedelta(days=2)).strftime('%Y-%m-%d')

    return None

def parse_flexible_time(time_str):
    """
    Parse flexible time formats like '2pm', '1 PM', '14:00', '2'.

    Args:
        time_str: String representing time

    Returns:
        str: Time in HH:MM:SS format or None
    """
    time_str_clean = time_str.upper().strip().replace(' ', '')

    # Try to extract hour and AM/PM
    match = re.search(r'(\d{1,2})\s*([AP]M)?', time_str_clean)
    if match:
        hour = int(match.group(1))
        meridiem = match.group(2)

        if meridiem == 'PM' and hour != 12:
            hour += 12
        elif meridiem == 'AM' and hour == 12:
            hour = 0

        if 0 <= hour <= 23:
            return f"{hour:02d}:00:00"

    return None

def parse_duration(duration_str):
    """
    Parse duration strings like '1 hour', '90 min', '2 hours', '60', '90min'.

    Args:
        duration_str: String representing duration

    Returns:
        int: Duration in minutes or None
    """
    duration_str_lower = duration_str.lower().strip()

    # Map common phrases to minutes
    duration_map = {
        '1 hour': 60,
        '1hour': 60,
        '1hr': 60,
        '1 hr': 60,
        'one hour': 60,
        '90 min': 90,
        '90min': 90,
        '90 minutes': 90,
        'ninety minutes': 90,
        '2 hours': 120,
        '2hours': 120,
        '2hr': 120,
        '2 hrs': 120,
        'two hours': 120,
        '120 min': 120,
        '120min': 120,
    }

    if duration_str_lower in duration_map:
        return duration_map[duration_str_lower]

    # Try to extract number
    match = re.search(r'(\d+)', duration_str)
    if match:
        num = int(match.group(1))
        # If it's a common session duration, return it
        if num in [60, 90, 120]:
            return num

    return None

def extract_natural_language_booking(message):
    """
    Extract booking details from natural language messages.
    Examples:
    - "i want to make a booking tomorrow 1pm at kota damansara"
    - "book for tomorrow 2pm soma kd"
    - "can I book 3pm today at puchong outlet"

    Args:
        message: Natural language message

    Returns:
        dict: Extracted booking details
    """
    booking_info = {}
    message_lower = message.lower()

    # Map common outlet variations to standardized names
    outlet_mapping = {
        'kd': 'SOMA KD',
        'kota damansara': 'SOMA KD',
        'damansara': 'SOMA KD',
        'puchong': 'SOMA Puchong',
        'pj': 'SOMA PJ',
        'petaling jaya': 'SOMA PJ',
        'cheras': 'SOMA Cheras',
        'setapak': 'SOMA Setapak',
        'sunway': 'SOMA Sunway',
        'velocity': 'SOMA Velocity',
    }

    # Extract outlet/location from the message
    outlet_patterns = [
        r'at\s+([^\n,\.!?]+?)(?:\s+outlet)?(?=\s|$|,|\.|!|\?)',  # "at kota damansara", "at SOMA KD"
        r'outlet[:\s]+([^\n,\.!?]+?)(?=\s|$|,|\.|!|\?)',  # "outlet: kota damansara"
        r'(?:soma|healthland)\s+([^\n,\.!?]+?)(?=\s|$|,|\.|!|\?)',  # "soma kd", "healthland puchong"
        r'\b(kota damansara|kd|puchong|cheras|setapak|sunway|velocity|pj|petaling jaya)\b',  # Direct mention
    ]

    for pattern in outlet_patterns:
        outlet_match = re.search(pattern, message, re.IGNORECASE)
        if outlet_match:
            outlet_text = outlet_match.group(1).strip().lower()
            # Clean up common words
            outlet_text = re.sub(r'\b(the|branch|location|outlet|at|in)\b', '', outlet_text, flags=re.IGNORECASE).strip()

            # Check if it matches a known outlet variation
            if outlet_text in outlet_mapping:
                booking_info['outlet'] = outlet_mapping[outlet_text]
                break
            elif outlet_text:
                # Use as-is if not in mapping
                booking_info['outlet'] = outlet_text.title()
                break

    # Extract date - look for relative dates anywhere in the message
    date_patterns = [
        (r'\b(today|tdy)\b', lambda: datetime.now().date()),
        (r'\b(tomorrow|tmr|tmrw)\b', lambda: datetime.now().date() + timedelta(days=1)),
        (r'\bday after tomorrow\b', lambda: datetime.now().date() + timedelta(days=2)),
    ]

    for pattern, date_func in date_patterns:
        if re.search(pattern, message_lower):
            booking_info['booking_date'] = date_func().strftime('%Y-%m-%d')
            break

    # If no relative date found, try formatted dates
    if 'booking_date' not in booking_info:
        date_match = re.search(r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', message)
        if date_match:
            date_str = date_match.group(1)
            for fmt in ['%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%y', '%d/%m/%y']:
                try:
                    date_obj = datetime.strptime(date_str, fmt)
                    booking_info['booking_date'] = date_obj.strftime('%Y-%m-%d')
                    break
                except ValueError:
                    continue

    # Extract time - look for time patterns anywhere in the message
    time_patterns = [
        r'\b(\d{1,2})\s*pm\b',  # "1pm", "2 pm"
        r'\b(\d{1,2})\s*am\b',  # "9am", "10 am"
        r'\b(\d{1,2}):(\d{2})\s*(am|pm)\b',  # "1:30pm", "2:00 pm"
        r'\b(\d{1,2}):(\d{2})\b',  # "14:00", "15:30"
    ]

    for pattern in time_patterns:
        time_match = re.search(pattern, message_lower)
        if time_match:
            if 'pm' in pattern or 'am' in pattern:
                time_text = time_match.group(0)
                time = parse_flexible_time(time_text)
                if time:
                    booking_info['timeslot'] = time
                    break
            else:
                # 24-hour format or just hour:minute
                groups = time_match.groups()
                if len(groups) >= 2:
                    hour = int(groups[0])
                    minute = int(groups[1])
                    if 0 <= hour <= 23 and 0 <= minute <= 59:
                        booking_info['timeslot'] = f"{hour:02d}:{minute:02d}:00"
                        break
                else:
                    hour = int(groups[0])
                    if 0 <= hour <= 23:
                        booking_info['timeslot'] = f"{hour:02d}:00:00"
                        break

    # Extract pax (number of people)
    pax_patterns = [
        r'\b(\d+)\s+(?:people|person|pax|guest|guests)\b',  # "2 people", "3 pax"
        r'\bfor\s+(\d+)\b',  # "for 2"
        r'\bpax[:\s]+(\d+)\b',  # "pax: 2"
    ]

    for pattern in pax_patterns:
        pax_match = re.search(pattern, message_lower)
        if pax_match:
            pax = int(pax_match.group(1))
            if 1 <= pax <= 10:
                booking_info['pax'] = pax
                break

    # Extract treatment type
    treatment_keywords = {
        'thai massage': 'Thai Massage',
        'thai': 'Thai Massage',
        'oil massage': 'Oil Massage',
        'oil': 'Oil Massage',
        'aromatherapy': 'Aromatherapy',
        'aroma': 'Aromatherapy',
        'foot massage': 'Foot Massage',
        'foot': 'Foot Massage',
        'thera-p': 'Thera-P',
        'therap': 'Thera-P',
        'massage': 'Massage',
    }

    for keyword, treatment in treatment_keywords.items():
        if keyword in message_lower:
            booking_info['treatment_type'] = treatment
            break

    # Extract duration
    duration_patterns = [
        r'\b(60|90|120)\s*(?:min|minute|minutes)?\b',  # "90 min", "60 minutes"
        r'\b(1|2)\s*(?:hour|hours|hr|hrs)\b',  # "1 hour", "2 hours"
    ]

    for pattern in duration_patterns:
        duration_match = re.search(pattern, message_lower)
        if duration_match:
            value = duration_match.group(1)
            if value in ['60', '90', '120']:
                booking_info['session'] = int(value)
                break
            elif value == '1':
                booking_info['session'] = 60
                break
            elif value == '2':
                booking_info['session'] = 120
                break

    # Extract masseur preference
    if re.search(r'\b(male|man|men)\b', message_lower):
        booking_info['preferred_masseur'] = 'Male'
    elif re.search(r'\b(female|woman|women|lady)\b', message_lower):
        booking_info['preferred_masseur'] = 'Female'

    # Extract name (if mentioned)
    name_patterns = [
        r'(?:name is|i am|i\'m|my name is)\s+([A-Za-z\s]+?)(?:\.|,|$|\s+\d)',
        r'^([A-Za-z\s]{3,30}?)(?:,|\s+want|\s+would|\s+need)',
    ]

    for pattern in name_patterns:
        name_match = re.search(pattern, message, re.IGNORECASE)
        if name_match:
            name = name_match.group(1).strip()
            # Validate it's likely a name (not keywords)
            if not any(keyword in name.lower() for keyword in ['book', 'want', 'make', 'appointment', 'massage', 'outlet']):
                booking_info['customer_name'] = name
                break

    # Extract phone number
    phone_match = re.search(r'\b(\+?6?0?1[0-9]{8,9})\b', message)
    if phone_match:
        booking_info['phone'] = re.sub(r'[^\d+]', '', phone_match.group(1))

    return booking_info

def extract_generic_booking_details(message):
    """
    Extract booking details from unstructured/generic message format.
    Assumes data is provided line by line in expected order.

    Args:
        message: Message text with booking details

    Returns:
        dict: Extracted booking details
    """
    booking_info = {}
    lines = [line.strip() for line in message.split('\n') if line.strip()]

    # If message is too short (like "tomorrow 1pm"), try to extract what we can
    if len(lines) <= 2:
        for line in lines:
            # Try to find date
            rel_date = parse_relative_date(line)
            if rel_date and 'booking_date' not in booking_info:
                booking_info['booking_date'] = rel_date
                continue

            # Try to find time
            time = parse_flexible_time(line)
            if time and 'timeslot' not in booking_info:
                booking_info['timeslot'] = time
                continue

        return booking_info

    # For longer messages, try to parse in expected order
    idx = 0

    # Line 0: Name (if not starting with number or common outlet names)
    if idx < len(lines) and not re.match(r'^\d', lines[idx]) and not any(word in lines[idx].upper() for word in ['SOMA', 'HEALTHLAND']):
        booking_info['customer_name'] = lines[idx]
        idx += 1

    # Line 1: Phone number (starts with digits)
    if idx < len(lines) and re.search(r'\d{8,}', lines[idx]):
        phone = re.sub(r'[^\d+]', '', lines[idx])
        booking_info['phone'] = phone
        idx += 1

    # Line 2: Outlet (contains SOMA or HealthLand)
    if idx < len(lines) and any(word in lines[idx].upper() for word in ['SOMA', 'HEALTHLAND', 'KD', 'PUCHONG', 'CHERAS']):
        booking_info['outlet'] = lines[idx]
        idx += 1

    # Line 3: Date (relative or formatted date)
    if idx < len(lines):
        # Try relative date first
        rel_date = parse_relative_date(lines[idx])
        if rel_date:
            booking_info['booking_date'] = rel_date
            idx += 1
        else:
            # Try formatted date
            for fmt in ['%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d.%m.%Y']:
                try:
                    date_obj = datetime.strptime(lines[idx], fmt)
                    booking_info['booking_date'] = date_obj.strftime('%Y-%m-%d')
                    idx += 1
                    break
                except ValueError:
                    continue

    # Line 4: Time
    if idx < len(lines):
        time = parse_flexible_time(lines[idx])
        if time:
            booking_info['timeslot'] = time
            idx += 1

    # Line 5: Pax (single digit typically)
    if idx < len(lines) and lines[idx].isdigit() and int(lines[idx]) <= 10:
        booking_info['pax'] = int(lines[idx])
        idx += 1

    # Line 6: Treatment Type
    if idx < len(lines) and any(word in lines[idx].upper() for word in ['MASSAGE', 'THERA', 'OIL', 'THAI', 'TREATMENT', 'SPA']):
        booking_info['treatment_type'] = lines[idx]
        idx += 1

    # Line 7: Duration
    if idx < len(lines):
        duration = parse_duration(lines[idx])
        if duration:
            booking_info['session'] = duration
            idx += 1

    # Line 8: Preferred Masseur
    if idx < len(lines) and any(word in lines[idx].upper() for word in ['MALE', 'FEMALE', 'M', 'F', 'MAN', 'WOMAN']):
        booking_info['preferred_masseur'] = lines[idx]
        idx += 1

    # Line 9: 3rd party voucher
    if idx < len(lines) and any(word in lines[idx].upper() for word in ['YES', 'NO', 'Y', 'N']):
        booking_info['third_party_voucher'] = 'yes' if lines[idx].upper() in ['YES', 'Y'] else 'no'
        idx += 1

    # Line 10: Using package
    if idx < len(lines) and any(word in lines[idx].upper() for word in ['YES', 'NO', 'Y', 'N']):
        booking_info['using_package'] = 'yes' if lines[idx].upper() in ['YES', 'Y'] else 'no'
        idx += 1

    return booking_info

def format_chat_history(messages):
    """
    Format chat history to pair incoming (customer) and outgoing (agent) messages.

    Args:
        messages: List of message dictionaries with 'type', 'message', 'timestamp' fields

    Returns:
        Formatted string with conversation history
    """
    if not messages:
        return "No previous conversation history."

    # Sort messages by timestamp to ensure chronological order
    sorted_messages = sorted(messages, key=lambda x: x.get('timestamp', ''))

    formatted_history = []
    for msg in sorted_messages:
        msg_type = msg.get('type')
        message_text = msg.get('message', '')
        timestamp = msg.get('timestamp', '')

        if msg_type == 'Incoming':
            # Customer message
            formatted_history.append(f"Customer: {message_text}")
        elif msg_type == 'Outgoing':
            # Agent/AI response
            formatted_history.append(f"Agent: {message_text}")

    return "\n".join(formatted_history)

def has_booking_intent(message):
    """
    Check if a message expresses booking intent.
    Detects explicit booking phrases OR implied booking intent (e.g., "I want a foot massage tomorrow").

    Args:
        message: The message text to check

    Returns:
        bool: True if message expresses booking intent
    """
    booking_intent_keywords = [
        'want to book', 'want book', 'wanna book', 'would like to book',
        'need to book', 'need book', 'make a booking', 'make booking',
        'book appointment', 'book a slot', 'book slot',
        'can i book', 'can book', 'how to book', 'how do i book',
        'make appointment', 'make an appointment',
        'reserve', 'reservation', 'schedule', 'schedule appointment'
    ]

    message_lower = message.lower()

    # Check for explicit booking keywords
    if any(keyword in message_lower for keyword in booking_intent_keywords):
        return True

    # Check for IMPLIED booking intent: treatment type + date/time
    # Examples: "I want a foot massage tomorrow", "Need Thai massage on Friday"
    treatment_keywords = [
        'massage', 'foot massage', 'thai massage', 'oil massage',
        'aromatherapy', 'foot reflexology', 'thera-p', 'treatment', 'spa'
    ]

    date_time_keywords = [
        'tomorrow', 'today', 'tonight', 'tmr', 'tmrw',
        'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
        'next week', 'this week', 'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december',
        'am', 'pm', 'morning', 'afternoon', 'evening', 'night'
    ]

    has_treatment = any(treatment in message_lower for treatment in treatment_keywords)
    has_date_time = any(dt in message_lower for dt in date_time_keywords) or re.search(r'\d{1,2}[:\s]*(am|pm)', message_lower) or re.search(r'\d{1,2}[-/]\d{1,2}', message)

    # If message mentions a treatment AND a date/time, it's likely booking intent
    if has_treatment and has_date_time:
        return True

    # Check for "want" or "need" + treatment (e.g., "I want a massage")
    # This catches "I want a foot massage on..." even without explicit date
    intent_verbs = ['want', 'need', 'would like', 'looking for', 'get']
    has_intent_verb = any(verb in message_lower for verb in intent_verbs)

    if has_intent_verb and has_treatment:
        return True

    return False

def is_booking_details_message(message):
    """
    Check if a message contains booking details or booking intent.
    Detects labeled format, natural language, or generic line-by-line format.

    Args:
        message: The message text to check

    Returns:
        bool: True if message likely contains booking details or intent
    """
    # Method 1: Check for labeled booking fields
    labeled_fields = ['Name:', 'Outlet:', 'Preferred Date:', 'Preferred Time:', 'No. of Pax:', 'Treatment Type:', 'Duration']
    field_count = sum(1 for field in labeled_fields if field in message)

    # If at least 5 out of 7 required fields are present, it's a booking form
    if field_count >= 5:
        return True

    # Method 2: Check for booking intent (simple keywords)
    message_lower = message.lower()
    if has_booking_intent(message):
        return True

    # Method 3: Check for generic booking data patterns
    lines = [line.strip() for line in message.split('\n') if line.strip()]

    # Pattern 1: Multiple lines with potential booking data
    if len(lines) >= 3:
        has_outlet = any(
            word in message_lower
            for word in ['soma', 'kd', 'kota damansara', 'puchong', 'cheras', 'setapak', 'sunway', 'velocity', 'pj']
        )
        has_date = any(
            word in message_lower
            for word in ['tomorrow', 'today', 'tmr']
        ) or re.search(r'\d{1,2}[-/]\d{1,2}[-/]\d{2,4}', message)

        has_time = re.search(r'\d{1,2}\s*(am|pm|AM|PM)', message) or re.search(r'\d{1,2}:\d{2}', message)

        has_phone = re.search(r'\b0\d{9,10}\b', message)

        # If message has outlet/location + (date or time or phone), likely booking data
        if has_outlet and (has_date or has_time or has_phone):
            frappe.log_error(
                "Booking Detection Debug",
                f"Detected booking data (generic format)\nhas_outlet: {has_outlet}\nhas_date: {has_date}\nhas_time: {has_time}\nhas_phone: {has_phone}"
            )
            return True

        # If message has multiple booking indicators
        booking_indicators = sum([has_outlet, has_date, has_time, has_phone])
        if booking_indicators >= 2:
            frappe.log_error(
                "Booking Detection Debug",
                f"Detected booking data (multiple indicators: {booking_indicators})"
            )
            return True

    # Pattern 2: Natural language booking with details
    if has_booking_intent:
        # Check if message has specific details (not just "I want to book")
        has_specifics = any([
            re.search(r'\d{1,2}\s*(am|pm)', message_lower),  # time
            re.search(r'tomorrow|today', message_lower),  # date
            any(outlet in message_lower for outlet in ['kd', 'puchong', 'cheras', 'soma']),  # outlet
            re.search(r'\d+\s*(people|pax|person)', message_lower),  # pax
        ])

        if has_specifics:
            frappe.log_error(
                "Booking Detection Debug",
                f"Detected booking intent with specifics"
            )
            return True

    frappe.log_error(
        "Booking Detection Debug",
        f"NOT detected as booking message\nLines: {len(lines)}\nField count: {field_count}\nHas intent: {has_booking_intent}"
    )
    return False

def extract_from_chat_history(chat_history):
    """
    Extract booking details from entire chat history.
    Looks through all user messages for booking information.

    Args:
        chat_history: List of chat messages from get_whatsapp_messages()

    Returns:
        dict: Extracted booking details from history
    """
    combined_data = {}

    if not chat_history:
        return combined_data

    # Combine all user (incoming) messages
    user_messages = []
    for msg in chat_history:
        if msg.get('type') == 'Incoming':
            user_messages.append(msg.get('message', ''))

    # Join all user messages and extract from them
    combined_text = '\n'.join(user_messages)

    if not combined_text:
        return combined_data

    # Extract from combined history
    # Try labeled format first
    name_match = re.search(r'(?:name is|i am|i\'m|my name is|name:)\s+([A-Za-z\s]+?)(?:\.|,|$|\n|\s+\d)', combined_text, re.IGNORECASE)
    if name_match:
        name = name_match.group(1).strip()
        if not any(keyword in name.lower() for keyword in ['book', 'want', 'make', 'appointment', 'massage', 'outlet']):
            combined_data['customer_name'] = name

    # Extract phone from history
    phone_match = re.search(r'\b(\+?6?0?1[0-9]{8,9})\b', combined_text)
    if phone_match:
        combined_data['phone'] = re.sub(r'[^\d+]', '', phone_match.group(1))

    # Extract pax from history
    pax_patterns = [
        r'\b(\d+)\s+(?:people|person|pax|guest|guests)\b',
        r'\bfor\s+(\d+)\b',
        r'\bpax[:\s]+(\d+)\b',
    ]
    for pattern in pax_patterns:
        pax_match = re.search(pattern, combined_text.lower())
        if pax_match:
            pax = int(pax_match.group(1))
            if 1 <= pax <= 10:
                combined_data['pax'] = pax
                break

    # Extract treatment type from history
    treatment_keywords = {
        'thai massage': 'Thai Massage',
        'thai': 'Thai Massage',
        'oil massage': 'Oil Massage',
        'oil': 'Oil Massage',
        'aromatherapy': 'Aromatherapy',
        'aroma': 'Aromatherapy',
        'foot massage': 'Foot Massage',
        'foot': 'Foot Massage',
        'thera-p': 'Thera-P',
        'therap': 'Thera-P',
        'massage': 'Massage',
    }
    for keyword, treatment in treatment_keywords.items():
        if keyword in combined_text.lower():
            combined_data['treatment_type'] = treatment
            break

    # Extract duration from history
    duration_patterns = [
        r'\b(60|90|120)\s*(?:min|minute|minutes)?\b',
        r'\b(1|2)\s*(?:hour|hours|hr|hrs)\b',
    ]
    for pattern in duration_patterns:
        duration_match = re.search(pattern, combined_text.lower())
        if duration_match:
            value = duration_match.group(1)
            if value in ['60', '90', '120']:
                combined_data['session'] = int(value)
                break
            elif value == '1':
                combined_data['session'] = 60
                break
            elif value == '2':
                combined_data['session'] = 120
                break

    # Extract masseur preference from history
    if re.search(r'\b(male|man|men)\b', combined_text.lower()):
        combined_data['preferred_masseur'] = 'Male'
    elif re.search(r'\b(female|woman|women|lady)\b', combined_text.lower()):
        combined_data['preferred_masseur'] = 'Female'

    # Extract outlet from history
    outlet_mapping = {
        'kd': 'SOMA KD',
        'kota damansara': 'SOMA KD',
        'damansara': 'SOMA KD',
        'puchong': 'SOMA Puchong',
        'pj': 'SOMA PJ',
        'petaling jaya': 'SOMA PJ',
        'cheras': 'SOMA Cheras',
        'setapak': 'SOMA Setapak',
        'sunway': 'SOMA Sunway',
        'velocity': 'SOMA Velocity',
    }

    for keyword, outlet_name in outlet_mapping.items():
        if re.search(r'\b' + re.escape(keyword) + r'\b', combined_text.lower()):
            combined_data['outlet'] = outlet_name
            break

    return combined_data

def extract_booking_with_llm(chat_history, current_message, existing_data=None):
    """
    Use LLM to intelligently extract booking details from chat history and current message.
    More robust than regex-based extraction, can understand context and conversational cues.

    Args:
        chat_history: List of chat messages from get_whatsapp_messages()
        current_message: The latest message from the customer
        existing_data: Previously extracted/pending data to merge with

    Returns:
        dict: Extracted booking details
    """
    if not LANGCHAIN_AVAILABLE:
        frappe.log_error("LangChain not available, falling back to regex extraction", "Booking LLM Extraction")
        return {}

    try:
        # Get WhatsApp Settings for API key
        whatsapp_settings = frappe.get_single("WhatsApp Settings")
        openai_key = whatsapp_settings.get_password("openai_api_key")

        if not openai_key:
            frappe.log_error("OpenAI API key not configured", "Booking LLM Extraction")
            return {}

        # Format chat history
        formatted_history = format_chat_history(chat_history) if chat_history else "No previous conversation."

        # Create LLM instance
        llm = ChatOpenAI(
            model="gpt-5-chat-latest",
            temperature=0,
            openai_api_key=openai_key,
            timeout=30,
            max_retries=2
        )

        # Create extraction prompt
        extraction_prompt = f"""You are an AI assistant helping to extract booking information from a WhatsApp conversation.

CONVERSATION HISTORY:
{formatted_history}

CURRENT MESSAGE:
{current_message}

EXISTING EXTRACTED DATA (may be incomplete):
{json.dumps(existing_data or {}, indent=2, default=str)}

Your task is to extract booking details from the conversation. Look through the ENTIRE conversation history and current message to find the following information:

1. customer_name - Customer's full name
2. phone - Phone number (format: Malaysian numbers starting with 01 or +601)
3. outlet - Outlet name (e.g., SOMA KD, SOMA Puchong, SOMA PJ, SOMA Cheras, SOMA Setapak, SOMA Sunway, SOMA Velocity)
4. booking_date - Preferred date in YYYY-MM-DD format (convert relative dates like "tomorrow" to actual dates, today is {datetime.now().strftime('%Y-%m-%d')})
5. timeslot - Preferred time in HH:MM:SS format (convert times like "2pm" to "14:00:00")
6. pax - Number of people (1-10)
7. treatment_type - Type of treatment (e.g., Thai Massage, Oil Massage, Aromatherapy, Foot Massage, Thera-P, Massage)
8. session - Duration in minutes (60, 90, or 120)
9. preferred_masseur - Gender preference (Male or Female)
10. third_party_voucher - Using 3rd party voucher? (yes or no)
11. using_package - Using package? (yes or no)

IMPORTANT INSTRUCTIONS:
- Look through the ENTIRE conversation history, not just the current message
- If a field is mentioned anywhere in the conversation and not in existing data, extract it
- If existing data already has a value for a field, keep it unless the customer explicitly changes it in the current message
- For outlet names, map common variations (e.g., "KD" → "SOMA KD", "Puchong" → "SOMA Puchong")
- For dates, convert relative dates (today, tomorrow, day after tomorrow) to YYYY-MM-DD format
- For times, convert to 24-hour HH:MM:SS format (e.g., "2pm" → "14:00:00", "2:30pm" → "14:30:00")
- For duration, extract only the number of minutes (60, 90, or 120)
- Only extract information that is explicitly mentioned or clearly implied
- Return ONLY a valid JSON object with the extracted fields

OUTPUT FORMAT (JSON only, no other text):
{{
  "customer_name": "extracted name or null",
  "phone": "extracted phone or null",
  "outlet": "extracted outlet or null",
  "booking_date": "YYYY-MM-DD or null",
  "timeslot": "HH:MM:SS or null",
  "pax": number or null,
  "treatment_type": "extracted treatment or null",
  "session": number (60/90/120) or null,
  "preferred_masseur": "Male/Female or null",
  "third_party_voucher": "yes/no or null",
  "using_package": "yes/no or null"
}}"""

        # Call LLM
        response = llm.invoke(extraction_prompt)
        response_text = response.content.strip()

        # Parse JSON response
        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            response_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', response_text, flags=re.MULTILINE)

        extracted_data = json.loads(response_text)

        # Clean up null values
        cleaned_data = {k: v for k, v in extracted_data.items() if v is not None}

        frappe.log_error(
            "LLM Extraction Success",
            f"LLM Extraction Result:\n{json.dumps(cleaned_data, indent=2, default=str)}"
        )

        return cleaned_data

    except Exception as e:
        frappe.log_error(
            "LLM Extraction Error",
            f"Error in LLM extraction: {str(e)}\n{frappe.get_traceback()}"
        )
        return {}

def extract_booking_details(message, existing_data=None, chat_history=None):
    """
    Extract booking information from conversation using LLM.
    The LLM analyzes the entire chat history to intelligently extract booking details.

    Args:
        message: The current message text
        existing_data: Previously extracted/pending data to merge with
        chat_history: Full chat history to extract from (required for LLM extraction)

    Returns:
        dict: Extracted booking details with 'data' and 'missing_fields' keys
    """
    try:
        # Start with existing data as base
        booking_info = existing_data.copy() if existing_data else {}

        # PRIMARY METHOD: LLM-based extraction from conversation
        # This scans the entire conversation history and intelligently extracts all fields
        if chat_history and LANGCHAIN_AVAILABLE:
            try:
                llm_data = extract_booking_with_llm(chat_history, message, existing_data)
                frappe.log_error(
                    "LLM Extraction Debug",
                    f"LLM extracted from conversation:\n{json.dumps(llm_data, indent=2, default=str)}"
                )

                # Use LLM data as the primary source
                for key, value in llm_data.items():
                    if value and value != "null":  # Only use non-null LLM values
                        booking_info[key] = value

                frappe.log_error(
                    "LLM Extraction Result",
                    f"Final booking data after LLM:\n{json.dumps(booking_info, indent=2, default=str)}"
                )
            except Exception as e:
                frappe.log_error(
                    "LLM Extraction Error",
                    f"LLM extraction failed: {str(e)}\n{frappe.get_traceback()}"
                )

                # FALLBACK: If LLM fails, try basic regex extraction from current message only
                frappe.log_error("Extraction Fallback", "Falling back to basic regex extraction")
                fallback_data = extract_natural_language_booking(message)
                for key, value in fallback_data.items():
                    if key not in booking_info and value:
                        booking_info[key] = value
        else:
            # No chat history or LLM not available - use regex fallback
            frappe.log_error(
                "Extraction Fallback",
                f"LLM not available (chat_history={bool(chat_history)}, LANGCHAIN={LANGCHAIN_AVAILABLE})"
            )
            fallback_data = extract_natural_language_booking(message)
            for key, value in fallback_data.items():
                if key not in booking_info and value:
                    booking_info[key] = value

        # Define required fields
        required_fields = {
            'outlet': 'Outlet',
            'booking_date': 'Preferred Date',
            'session': 'Duration (60min / 90min / 120min)',
            'pax': 'No. of Pax',
            'timeslot': 'Preferred Time',
            'customer_name': 'Name',
            'phone': 'Phone Number (linked to package)',
        }

        # required_fields = {
        #     'outlet': 'Outlet',
        #     'booking_date': 'Preferred Date',
        #     'session': 'Duration (60min / 90min / 120min)',
        #     'pax': 'No. of Pax',
        #     'timeslot': 'Preferred Time',
        #     'customer_name': 'Name',
        #     'phone': 'Phone Number (linked to package)',
        #     'treatment_type': 'Treatment Type',
        #     'preferred_masseur': 'Preferred Masseur (Male / Female)',
        #     'third_party_voucher': 'Using any 3rd party voucher? (Yes / No)',
        #     'using_package': 'Using any package? (Yes / No)'
        # }

        # Check for missing fields
        missing = [label for field, label in required_fields.items() if field not in booking_info]

        return {
            'data': booking_info,
            'missing_fields': missing,
            'is_complete': len(missing) == 0
        }

    except Exception as e:
        frappe.log_error("Booking Extraction Error", f"Error extracting booking details: {str(e)}\n{frappe.get_traceback()}")
        return {
            'data': existing_data or {},
            'missing_fields': [],
            'is_complete': False
        }


def detect_update_intent_with_llm(chat_history, current_message, existing_booking=None):
    """
    Use LLM to detect if the user wants to update an existing booking.
    Analyzes conversation context to distinguish between new booking and update requests.

    Args:
        chat_history: List of chat messages from get_whatsapp_messages()
        current_message: The latest message from the customer
        existing_booking: Previously confirmed booking data

    Returns:
        dict: {
            'is_update': bool,
            'updated_fields': dict,
            'update_type': str (e.g., 'reschedule', 'modify_details', 'general_update')
        }
    """
    if not LANGCHAIN_AVAILABLE:
        frappe.log_error("LangChain not available, cannot detect update intent", "Update Intent Detection")
        return {'is_update': False, 'updated_fields': {}, 'update_type': None}

    try:
        # Get WhatsApp Settings for API key
        whatsapp_settings = frappe.get_single("WhatsApp Settings")
        openai_key = whatsapp_settings.get_password("openai_api_key")

        if not openai_key:
            frappe.log_error("OpenAI API key not configured", "Update Intent Detection")
            return {'is_update': False, 'updated_fields': {}, 'update_type': None}

        # Format chat history
        formatted_history = format_chat_history(chat_history) if chat_history else "No previous conversation."

        # Create LLM instance
        llm = ChatOpenAI(
            model="gpt-5-chat-latest",
            temperature=0,
            openai_api_key=openai_key,
            timeout=30,
            max_retries=2
        )

        # Create detection prompt
        detection_prompt = f"""You are an AI assistant helping to detect if a customer wants to UPDATE an existing booking or make a NEW booking.

CONVERSATION HISTORY:
{formatted_history}

CURRENT MESSAGE:
{current_message}

EXISTING BOOKING (if any):
{json.dumps(existing_booking or {}, indent=2, default=str)}

Your task is to analyze the conversation and determine:
1. Is the customer trying to UPDATE/MODIFY/CHANGE an existing booking? OR are they making a completely NEW booking?
2. If it's an update, what fields do they want to change?
3. What type of update is it? (reschedule, modify_details, or general_update)

KEYWORDS FOR UPDATE INTENT:
- "update", "modify", "change my booking", "reschedule", "move my booking"
- "change the time", "change the date", "different time", "different date"
- "update my appointment", "modify my appointment", "change my appointment"
- Mentioning changes to existing confirmed bookings

KEYWORDS FOR NEW BOOKING:
- "want to book", "make a booking", "book appointment", "new booking"
- Initial booking requests without prior confirmed booking

IMPORTANT INSTRUCTIONS:
- If there's NO existing booking and the customer uses update/change language, they likely mean to correct their current NEW booking details (treat as new booking, not update)
- If there IS an existing confirmed booking and customer says "change", "update", "reschedule" → it's an UPDATE
- Look for context clues: "I already booked...", "my booking for...", "the appointment I made..."
- Extract ONLY the fields the customer wants to change in their update request
- For dates: convert relative dates to YYYY-MM-DD format (today is {datetime.now().strftime('%Y-%m-%d')})
- For times: convert to 24-hour HH:MM:SS format

OUTPUT FORMAT (JSON only, no other text):
{{
  "is_update": true/false,
  "update_type": "reschedule"/"modify_details"/"general_update"/null,
  "updated_fields": {{
    "field_name": "new_value",
    ...
  }},
  "reasoning": "brief explanation of why this is/isn't an update"
}}

UPDATE TYPES:
- "reschedule": Only changing date/time
- "modify_details": Changing treatment, pax, preferences, etc.
- "general_update": Multiple field changes"""

        # Call LLM
        response = llm.invoke(detection_prompt)
        response_text = response.content.strip()

        # Parse JSON response
        if response_text.startswith("```"):
            response_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', response_text, flags=re.MULTILINE)

        result = json.loads(response_text)

        frappe.log_error(
            "Update Intent Detection",
            f"LLM Analysis:\n{json.dumps(result, indent=2, default=str)}"
        )

        return {
            'is_update': result.get('is_update', False),
            'updated_fields': result.get('updated_fields', {}),
            'update_type': result.get('update_type'),
            'reasoning': result.get('reasoning', '')
        }

    except Exception as e:
        frappe.log_error(
            "Update Intent Detection Error",
            f"Error detecting update intent: {str(e)}\n{frappe.get_traceback()}"
        )
        return {'is_update': False, 'updated_fields': {}, 'update_type': None}


def has_cancel_intent(message):
    """
    Check if a message expresses intent to cancel an existing booking.
    Uses keyword matching for straightforward detection.

    Args:
        message: The message text to check

    Returns:
        bool: True if message expresses cancel intent
    """
    cancel_intent_keywords = [
        'cancel', 'cancel booking', 'cancel appointment', 'cancel my booking',
        'cancel my appointment', 'want to cancel', 'need to cancel',
        'would like to cancel', 'have to cancel', 'must cancel',
        'delete booking', 'remove booking', 'delete appointment',
        'can\'t make it', 'cannot make it', 'unable to make it',
        'won\'t be able', 'will not make it', 'have to skip',
        'batalkan', 'batal booking', 'batal appointment'  # Malay
    ]

    message_lower = message.lower()
    return any(keyword in message_lower for keyword in cancel_intent_keywords)


def validate_booking_timeslot(timeslot):
    """
    Validate that the booking time is within operating hours (11:00 AM - 11:30 PM).

    Args:
        timeslot: Time string in HH:MM:SS format

    Returns:
        dict: {
            'valid': bool,
            'message': str (error message if invalid, empty if valid)
        }
    """
    if not timeslot:
        return {'valid': False, 'message': 'Please provide a preferred time for your booking.'}

    try:
        # Parse the time
        time_obj = datetime.strptime(timeslot, '%H:%M:%S').time()

        # Define operating hours: 11:00 AM to 11:30 PM
        opening_time = datetime.strptime('11:00:00', '%H:%M:%S').time()
        closing_time = datetime.strptime('23:30:00', '%H:%M:%S').time()

        # Check if time is within range
        if time_obj < opening_time or time_obj > closing_time:
            # Format time for display
            time_12hr = datetime.strptime(timeslot, '%H:%M:%S').strftime('%I:%M %p')

            # Check if this is an AM time that could be PM instead (1am-10am)
            hour = time_obj.hour
            if 1 <= hour <= 10:  # AM times that might be meant as PM
                # Calculate PM equivalent (add 12 hours)
                pm_hour = hour + 12
                pm_timeslot = f"{pm_hour:02d}:{time_obj.minute:02d}:{time_obj.second:02d}"
                pm_time_obj = datetime.strptime(pm_timeslot, '%H:%M:%S').time()

                # Check if PM version would be valid
                if opening_time <= pm_time_obj <= closing_time:
                    pm_time_12hr = datetime.strptime(pm_timeslot, '%H:%M:%S').strftime('%I:%M %p')

                    return {
                        'valid': False,
                        'message': f"""We're closed at {time_12hr}. ⏰

Did you mean {pm_time_12hr}?

Our operating hours are:
🕐 11:00 AM - 11:30 PM daily

Please confirm or choose a different time. 😊"""
                    }

            # Default message for other invalid times
            return {
                'valid': False,
                'message': f"""Sorry, we're closed at {time_12hr}. ⏰

Our operating hours are:
🕐 11:00 AM - 11:30 PM daily

Please choose a time within our operating hours. What time would work better for you? 😊"""
            }

        return {'valid': True, 'message': ''}

    except ValueError:
        return {'valid': False, 'message': 'Invalid time format. Please provide time in a valid format (e.g., "2pm", "14:00").'}


def get_pending_booking_data(crm_lead_doc):
    """
    Get pending booking data from CRM Lead.
    Falls back to cache if custom field doesn't exist.

    Args:
        crm_lead_doc: CRM Lead document

    Returns:
        dict: Pending booking data or empty dict
    """
    try:
        # Try to get from custom field first
        if hasattr(crm_lead_doc, 'pending_booking_data') and crm_lead_doc.pending_booking_data:
            return json.loads(crm_lead_doc.pending_booking_data)
    except Exception as e:
        frappe.log_error("Booking Data Read Error", f"Error reading pending_booking_data field: {str(e)}")

    # Fallback to cache if custom field doesn't exist
    try:
        cache_key = f"pending_booking_{crm_lead_doc.name}"
        cached_data = frappe.cache().get_value(cache_key)
        if cached_data:
            return json.loads(cached_data)
    except Exception as e:
        frappe.log_error("Booking Cache Read Error", f"Error reading from cache: {str(e)}")

    return {}

def save_pending_booking_data(crm_lead_doc, booking_data):
    """
    Save pending booking data to CRM Lead.
    Falls back to cache if custom field doesn't exist.

    Args:
        crm_lead_doc: CRM Lead document
        booking_data: Booking data dictionary to save
    """
    data_json = json.dumps(booking_data, default=str)

    # Try to save to custom field first
    try:
        if hasattr(crm_lead_doc, 'pending_booking_data'):
            frappe.db.set_value('CRM Lead', crm_lead_doc.name, 'pending_booking_data', data_json)
            frappe.db.commit()
            frappe.log_error("Booking Data Debug", f"Saved pending booking data to field for {crm_lead_doc.name}")
            return
    except Exception as e:
        frappe.log_error("Booking Data Save Error", f"Error saving to pending_booking_data field: {str(e)}")

    # Fallback to cache (expires in 24 hours)
    try:
        cache_key = f"pending_booking_{crm_lead_doc.name}"
        frappe.cache().set_value(cache_key, data_json, expires_in_sec=86400)  # 24 hours
        frappe.log_error("Booking Data Debug", f"Saved pending booking data to CACHE for {crm_lead_doc.name}")
    except Exception as e:
        frappe.log_error("Booking Cache Save Error", f"Error saving to cache: {str(e)}")

def clear_pending_booking_data(crm_lead_doc):
    """
    Clear pending booking data from CRM Lead.
    Clears both custom field and cache.

    Args:
        crm_lead_doc: CRM Lead document
    """
    # Clear custom field
    try:
        if hasattr(crm_lead_doc, 'pending_booking_data'):
            frappe.db.set_value('CRM Lead', crm_lead_doc.name, 'pending_booking_data', None)
            frappe.db.commit()
    except Exception as e:
        frappe.log_error("Booking Data Clear Error", f"Error clearing pending_booking_data field: {str(e)}")

    # Clear cache
    try:
        cache_key = f"pending_booking_{crm_lead_doc.name}"
        frappe.cache().delete_value(cache_key)
    except Exception as e:
        frappe.log_error("Booking Cache Clear Error", f"Error clearing cache: {str(e)}")

def format_missing_fields_message(missing_fields):
    """
    Format a friendly message asking for missing booking fields.

    Args:
        missing_fields: List of missing field labels

    Returns:
        str: Formatted message
    """
    if not missing_fields:
        return ""

    fields_list = "\n".join([f"- {field}" for field in missing_fields])

    return f"""Thank you for providing your booking details!

We still need the following information to complete your booking:

{fields_list}

Please provide the missing information so we can process your booking. 🙏"""

def generate_smart_missing_fields_prompt(chat_history, current_message, extracted_data, missing_fields):
    """
    Use LLM to generate an intelligent, conversational prompt for missing booking fields.
    Analyzes conversation context to ask for missing fields naturally.

    Args:
        chat_history: List of chat messages from get_whatsapp_messages()
        current_message: The latest message from the customer
        extracted_data: Data already extracted so far
        missing_fields: List of missing field labels

    Returns:
        str: Intelligent prompt message asking for missing fields
    """
    if not missing_fields:
        return ""

    # If LLM is not available, fall back to standard message
    if not LANGCHAIN_AVAILABLE:
        return format_missing_fields_message(missing_fields)

    try:
        # Get WhatsApp Settings for API key
        whatsapp_settings = frappe.get_single("WhatsApp Settings")
        openai_key = whatsapp_settings.get_password("openai_api_key")

        if not openai_key:
            return format_missing_fields_message(missing_fields)

        # Format chat history
        formatted_history = format_chat_history(chat_history) if chat_history else "No previous conversation."

        # Create LLM instance
        llm = ChatOpenAI(
            model="gpt-5-chat-latest",
            temperature=0.3,  # Slightly higher for more natural responses
            openai_api_key=openai_key,
            timeout=30,
            max_retries=2
        )

        # Create prompt for generating the missing fields message
        generation_prompt = f"""You are a friendly customer service assistant for HealthLand helping to complete a booking.

CONVERSATION HISTORY:
{formatted_history}

CURRENT CUSTOMER MESSAGE:
{current_message}

BOOKING INFORMATION COLLECTED SO FAR:
{json.dumps(extracted_data, indent=2, default=str)}

MISSING REQUIRED FIELDS:
{', '.join(missing_fields)}

Your task is to generate a friendly, conversational WhatsApp message that:
1. Acknowledges what the customer has already provided
2. Asks for the missing information in a natural, conversational way
3. Is concise and suitable for WhatsApp (not too long)
4. Maintains SOMA Wellness's friendly, relaxing brand tone
5. Uses appropriate emojis sparingly

GUIDELINES:
- Don't just list the missing fields - weave them into a natural conversation
- If the customer seems in a hurry, be brief and direct
- If they're chatty, match their energy
- Group related fields together (e.g., "When would you like to come in? Please share your preferred date and time")
- Make it feel personal and helpful, not robotic
- End with encouragement about looking forward to their visit

OUTPUT:
Generate ONLY the message text (no quotes, no formatting tags, just the raw message).
"""

        # Call LLM
        response = llm.invoke(generation_prompt)
        generated_message = response.content.strip()

        # Remove any quotes if the LLM added them
        generated_message = generated_message.strip('"\'')

        frappe.log_error(
            "Smart Missing Fields Prompt",
            f"Generated smart prompt for missing fields:\n{generated_message}"
        )

        return generated_message

    except Exception as e:
        frappe.log_error(
            "Smart Prompt Error",
            f"Error generating smart prompt: {str(e)}\n{frappe.get_traceback()}"
        )
        # Fallback to standard message
        return format_missing_fields_message(missing_fields)

def get_rag_chain(crm_lead_doc_name):
    """
    Initialize and return the RAG chain for AI-powered WhatsApp responses.
    Retrieves API keys from site config. Uses caching to avoid recreating chain.

    Returns:
        tuple: (retrieval_chain, formatted_chat_history)
    """
    global _rag_chain_cache

    # Get chat history for this specific CRM lead
    print(f"Fetching chat history for {crm_lead_doc_name}", "WhatsApp AI Debug")
    chat_history = get_whatsapp_messages("CRM Lead", crm_lead_doc_name)

    print('Current chat history: ')
    print(json.dumps(chat_history, indent=2, default=str))

    # Format chat history for use in prompt
    formatted_history = format_chat_history(chat_history)
    print('\nFormatted chat history:')
    print(formatted_history)

    # Return cached chain with current chat history if available
    if _rag_chain_cache is not None:
        frappe.log_error("Using cached RAG chain", "WhatsApp AI Debug")
        return _rag_chain_cache, formatted_history

    if not LANGCHAIN_AVAILABLE:
        frappe.throw("LangChain packages are not installed. Please install required dependencies.")

    frappe.log_error("Starting RAG chain initialization", "WhatsApp AI Debug")

    # Get API keys from site config
    frappe.log_error("Fetching WhatsApp Settings", "WhatsApp AI Debug")
    whatsapp_settings = frappe.get_single("WhatsApp Settings")
    openai_key = whatsapp_settings.get_password("openai_api_key")
    pinecone_key = whatsapp_settings.get_password("pinecone_api_key")
    index_name = "healthland-docs"

    if not openai_key or not pinecone_key:
        frappe.throw("OpenAI and Pinecone API keys must be configured in site_config.json")

    frappe.log_error("API keys retrieved successfully", "WhatsApp AI Debug")

    # Initialize embeddings and vector store with timeout
    frappe.log_error("Initializing OpenAI embeddings", "WhatsApp AI Debug")
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-large",
        openai_api_key=openai_key
    )

    frappe.log_error("Initializing Pinecone vector store (this may take time)", "WhatsApp AI Debug")
    try:
        vectorstore = PineconeVectorStore(
            index_name=index_name,
            embedding=embeddings,
            pinecone_api_key=pinecone_key
        )
        frappe.log_error("Pinecone vector store initialized", "WhatsApp AI Debug")
    except Exception as e:
        frappe.log_error(f"Pinecone initialization failed: {str(e)}", "WhatsApp AI Error")
        raise

    # Create retriever
    frappe.log_error("Creating retriever", "WhatsApp AI Debug")
    retriever = vectorstore.as_retriever(search_kwargs={"k": 5})

    # Create LLM
    frappe.log_error("Creating ChatOpenAI LLM", "WhatsApp AI Debug")
    llm = ChatOpenAI(
        model="gpt-5-chat-latest",
        temperature=0,
        openai_api_key=openai_key,
        timeout=30,  # Add 30 second timeout
        max_retries=2
    )

    # Create prompt template with conversation history
    frappe.log_error("Creating prompt template", "WhatsApp AI Debug")
    prompt = ChatPromptTemplate.from_template("""
    
You are a customer service representative from HealthLand, speaking on behalf of the brand.

YOUR IDENTITY & PERSPECTIVE:
- You work for HealthLand and represent our brand professionally
- Use "we", "our", "us" when referring to HealthLand (e.g., "We offer...", "Our most popular treatment...")
- You are knowledgeable, friendly, and genuinely care about helping customers find the right wellness treatment
- Your goal is to provide excellent service while representing HealthLand values: relaxation, wellness, and customer satisfaction

YOUR ROLE:
Assist customers with questions about:
- Our Services (Thai Massage, Oil Massage, Thera-P, Foot Massage, Foot Reflexology, etc.)
- Pricing and Packages
- Membership and Rewards Programs
- Our Outlet Locations and Operating Hours
- Booking and Reservations
- General wellness advice and treatment recommendations

HANDLING COMPETITOR COMPARISONS:
When customers ask about other wellness brands or compare us with competitors:
- Stay NEUTRAL and PROFESSIONAL - never speak negatively about competitors
- Focus on HealthLand strengths without putting others down
- Help customers make INFORMED DECISIONS by providing factual information about our services
- If you don't know about competitor offerings, acknowledge it honestly
- Emphasize what makes HealthLand unique (our treatments, customer experience, quality)

EXAMPLES - Competitor Comparisons:
Customer: "How are you different from SOMA Wellness?"
You: "I'd be happy to tell you about what we offer at HealthLand! We specialize in authentic Thai treatments and Foot Massage, which are our most popular services. We pride ourselves on our skilled therapists and relaxing atmosphere. Each wellness center has its own strengths, so I'd recommend considering what type of treatment experience you're looking for. What's most important to you - specific treatment type, location, or pricing?"

Customer: "Is HealthLand better than [competitor]?"
You: "I appreciate you considering HealthLand! While I can't make direct comparisons with other centers, I can tell you what makes us special: our focus on authentic Thai techniques, highly trained therapists, and particularly our signature Foot Massage that many customers love. I'd recommend visiting us to experience the HealthLand difference firsthand. What type of treatment are you interested in? I can help you find the perfect option at our center."

Customer: "Why should I choose HealthLand over others?"
You: "Great question! At HealthLand, we're known for our authentic Thai treatments and particularly our Foot Massage, which customers say is one of the best they've experienced. We focus on creating a truly relaxing atmosphere with skilled therapists who are trained in traditional techniques. Many of our customers appreciate our professional service and the quality of our treatments. Ultimately, the best choice depends on what you're looking for. What matters most to you in a wellness experience?"

CONVERSATION HISTORY:
{chat_history}

IMPORTANT - BOOKING/RESERVATION DETECTION:
If the customer's message is about making a booking, reservation, or appointment, analyze their message carefully:

**CASE 1: Generic booking request with NO specific details**
If they say things like "I want to make a booking", "Can I book?", "I want to book an appointment" WITHOUT mentioning specific treatment, date, time, or outlet, respond with this template:

👋 Hi there! Thank you for contacting HealthLand.

To help us serve you faster, please provide the following details:

Name:
Phone Number (linked to package):
Outlet:
Preferred Date:
Preferred Time:
No. of Pax:
Treatment Type:
Duration (60min / 90min / 120min):
Preferred Masseur (Male / Female):
Using any 3rd party voucher? (Yes / No):
Using any package? (Yes / No):

💆‍♀️ **Suggestion:** Our Foot Massage is a popular choice for relaxation and stress relief. Feel free to choose any treatment that suits your needs best!

📌 Please note: Slots are limited, and our masseurs are on rotation — book early to avoid disappointment!

We'll get back to you as soon as possible. 🙏
Thank you for your patience and continued support! 💚

**CASE 2: Booking request WITH specific details**
If they mention ANY specific details (treatment type, date, time, outlet, pax, duration), DO NOT send the full template. Instead, acknowledge what they've shared and conversationally ask for the missing information.

Examples:
- "I want a foot massage on 1st January" → "Great! I'd be happy to help you book a Foot Massage for January 1st. To complete your reservation, could you please share your name, phone number, preferred outlet location, what time you'd like to come in, number of people, duration (60/90/120 minutes), and masseur preference?"

- "Book for tomorrow at 2pm at KD outlet" → "Perfect! I'll help you book for tomorrow at 2pm at our KD outlet. I just need a few more details: your name, phone number, number of people (pax), which treatment you'd like, duration (60/90/120 min), and masseur preference (male/female)."

Note: The booking extraction system will handle the actual data collection. Just be conversational and helpful.

SOFT SELL APPROACH - RECOMMENDATIONS:
When customers ask about treatments or services, be consultative and helpful rather than pushy:

TREATMENT OPTIONS TO RECOMMEND (based on customer needs):
1. **Foot Massage** - Popular choice for stress relief and tired feet
2. **Traditional Thai Massage** - Full-body stretching and pressure point therapy
3. **Thainess Oil Treatment** - Gentle, relaxing aromatherapy massage
4. **Foot Reflexology** - Pressure point therapy for overall wellness

SOFT SELL GUIDELINES:
- **Listen first** - Understand what the customer needs before recommending
- **Be consultative** - Ask questions to understand their preferences (stress relief, muscle tension, relaxation, etc.)
- **Offer options** - Present 2-3 suitable treatments and let them choose
- **Share benefits naturally** - Explain what each treatment offers without being pushy
- **Respect their choice** - If they've decided on a treatment, support it enthusiastically
- **No pressure** - Never make customers feel they MUST choose a specific treatment
- **Mention popularity gently** - "Many customers enjoy..." instead of "You MUST try..."
- **Check conversation history** - Don't repeat recommendations already mentioned

HOW TO RECOMMEND:
❌ BAD (Hard Sell): "Our Foot Massage is the BEST! You definitely need to try it!"
✅ GOOD (Soft Sell): "Based on what you mentioned, I'd recommend either our Foot Massage or Traditional Thai Massage. The Foot Massage is great for stress relief, while Traditional Thai is excellent for full-body muscle tension. What sounds more appealing to you?"

❌ BAD (Pushy): "Everyone gets Foot Massage, it's our specialty!"
✅ GOOD (Consultative): "What are you hoping to address today - stress, muscle tension, or general relaxation? That will help me suggest the best treatment for you."

EXAMPLES OF SOFT SELL RECOMMENDATIONS (Speaking as HealthLand Staff):
Customer: "What services do you offer?"
You: "We offer several treatments at HealthLand to suit different needs! We have Foot Massage and Foot Reflexology for stress relief and tired feet, Traditional Thai Massage for full-body treatment, and Thainess Oil Treatment for gentle relaxation. What type of experience are you looking for today?"

Customer: "I'm feeling stressed"
You: "I understand, stress can really take a toll. For stress relief, we have a few options that might help. Our Foot Massage is very relaxing and helps release tension, or if you prefer full-body treatment, our Traditional Thai Massage or Thainess Oil Treatment are both excellent choices. Would you like me to tell you more about any of these?"

Customer: "Do you have full-body massage?"
You: "Yes, we do! Our Traditional Thai Massage is perfect for full-body treatment - it combines stretching with pressure point therapy. We also offer Thainess Oil Treatment if you prefer something more gentle and relaxing. Both are available in 60, 90, or 120-minute sessions. Which style appeals to you more?"

Customer: "What do you recommend?"
You: "I'd be happy to help! It depends on what you're looking to address. Are you looking for relief from muscle tension, stress relief, or general relaxation? That way I can suggest the best treatment for your needs."

Customer: "I want to relax"
You: "Perfect! For relaxation, I'd suggest either our Thainess Oil Treatment which uses aromatherapy oils for a soothing experience, or our Foot Massage which many customers find incredibly calming. Both are great options. What sounds better to you?"

COMMUNICATION GUIDELINES:
1. **Review conversation history** - Understand the context of previous interactions to provide personalized responses
2. **Use provided context accurately** - Answer based on factual information from our knowledge base
3. **Speak as HealthLand staff** - Always use "we", "our", "us" when referring to HealthLand
4. **Stay in character** - You are a friendly, professional customer service representative from HealthLand
5. **Be consultative, not salesy** - Ask questions to understand customer needs before recommending
6. **Soft sell approach** - Suggest treatments gently, offer options, let customers decide - NEVER be pushy
7. **Respect customer choices** - If they've decided on a treatment, support it enthusiastically without pushing alternatives
8. **Competitor comparisons** - Stay neutral, focus on our strengths, help customers make informed decisions
9. **Handle uncertainty gracefully** - If you don't know something, acknowledge it honestly and suggest contacting our outlet directly
10. **Maintain brand tone** - Polite, relaxing, warm, and professional - suitable for a wellness brand
11. **Keep it concise** - Responses should be friendly but brief for WhatsApp messaging
12. **Never make up information** - Only share accurate information from the context provided

RESPONSE STYLE EXAMPLES:
✅ GOOD (Soft Sell): "We have several options that might help with stress. Would you like to hear about our Foot Massage or Traditional Thai Massage?"
❌ BAD (Hard Sell): "You definitely need our Foot Massage! It's the BEST for stress!"

✅ GOOD (Consultative): "What are you hoping to address today - muscle tension, stress, or general relaxation? That will help me suggest the right treatment."
❌ BAD (Pushy): "Everyone loves our Foot Massage! You should book it now!"

✅ GOOD (Respectful): "Great choice! Our Traditional Thai Massage is excellent. Would you like to book a 60, 90, or 120-minute session?"
❌ BAD (Pushing alternatives): "Traditional Thai is good, but have you considered our Foot Massage instead? It's more popular!"

✅ GOOD (Staff voice): "We offer Traditional Thai Massage which focuses on stretching and pressure points."
❌ BAD (Third person): "HealthLand offers Traditional Thai Massage..."

✅ GOOD (Human): "I'd be happy to help you find the right treatment. What matters most to you?"
❌ BAD (AI-like): "The system recommends..."

<context>
{context}
</context>

Question: {input}
Answer:""")

    # Create chains
    frappe.log_error("Creating document and retrieval chains", "WhatsApp AI Debug")
    document_chain = create_stuff_documents_chain(llm, prompt)
    retrieval_chain = create_retrieval_chain(retriever, document_chain)

    # Cache the chain for reuse
    _rag_chain_cache = retrieval_chain
    frappe.log_error("RAG chain initialization completed and cached", "WhatsApp AI Debug")

    return retrieval_chain, formatted_history


BOOKING_ENPOINT = "/api/method/soma_wellness.api.make_bookings"

def handle_booking_api_mock(crm_lead_doc, whatsapp_id, booking_details):
    """
    Mock function to simulate creating a new booking (for POC/testing purposes).

    Args:
        crm_lead_doc: CRM Lead document
        whatsapp_id: WhatsApp ID of the customer
        booking_details: Dictionary containing booking information

    Returns:
        dict: Mock response simulating successful booking creation
    """
    # Generate a mock booking reference
    booking_reference = f"BKG{frappe.utils.now_datetime().strftime('%Y%m%d%H%M%S')}"

    # Log the booking for debugging
    frappe.log_error(
        title="Mock Booking Creation",
        message=f"""
        Booking Reference: {booking_reference}
        WhatsApp ID: {whatsapp_id}
        CRM Lead: {crm_lead_doc.name if crm_lead_doc else 'N/A'}
        Booking Details: {json.dumps(booking_details, indent=2, default=str)}
        """
    )

    # Simulate successful API response
    mock_response = {
        "status": "success",
        "message": "Your booking has been confirmed! We'll send you a confirmation SMS shortly.",
        "data": {
            "booking_reference": booking_reference,
            "outlet": booking_details.get("outlet"),
            "booking_date": booking_details.get("booking_date"),
            "session": booking_details.get("session"),
            "pax": booking_details.get("pax"),
            "timeslot": booking_details.get("timeslot"),
            "customer_name": booking_details.get("customer_name"),
            "phone": booking_details.get("phone"),
            "treatment_type": booking_details.get("treatment_type"),
            "preferred_masseur": booking_details.get("preferred_masseur"),
            "third_party_voucher": booking_details.get("third_party_voucher"),
            "using_package": booking_details.get("using_package"),
            "created_at": frappe.utils.now_datetime().isoformat(),
            "status": "confirmed"
        }
    }

    return mock_response


def handle_booking_api(crm_lead_doc, whatsapp_id, booking_details):
    """
    Handle booking API call with the provided booking details.

    Args:
        crm_lead_doc: CRM Lead document
        whatsapp_id: WhatsApp ID of the customer
        booking_details: Dictionary containing booking information with keys:
            - booking_date: Date in YYYY-MM-DD format
            - session: Duration in minutes (60, 90, or 120)
            - pax: Number of people
            - timeslot: Time in HH:MM:SS format
    """
    integration_settings = frappe.db.get_all("Integration Settings", filters={"active": 1}, pluck="name")
    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc("Integration Settings", integration_setting)
        url = integration_settings_doc.site_url + BOOKING_ENPOINT

        headers = {
            "Authorization": "Basic {0}".format(integration_settings_doc.get_password("access_token")),
            "Content-Type": "application/json"
        }

        request_body = {
            "outlet": booking_details.get("outlet"),
            "booking_date": booking_details.get("booking_date"),
            "session": booking_details.get("session"),
            "pax": booking_details.get("pax"),
            "timeslot": booking_details.get("timeslot"),
            "customer_name": booking_details.get("customer_name"),
            "mobile": whatsapp_id,
            "phone": booking_details.get("phone"),
            "treatment_type": booking_details.get("treatment_type"),
            "preferred_masseur": booking_details.get("preferred_masseur"),
            "third_party_voucher": booking_details.get("third_party_voucher"),
            "using_package": booking_details.get("using_package")
        }

        try:
            response = requests.post(url, data=json.dumps(request_body, default=str), headers=headers, timeout=30)
            response.raise_for_status()
            response_data = response.json()
            return response_data
        except requests.Timeout:
            frappe.throw("Request timed out after 30 seconds")
        except requests.RequestException as e:
            frappe.throw(f"An error occurred: {e}")


def handle_update_booking_api_mock(crm_lead_doc, whatsapp_id, booking_details, booking_reference=None):
    """
    Mock function to simulate updating a booking (for POC/testing purposes).

    Args:
        crm_lead_doc: CRM Lead document
        whatsapp_id: WhatsApp ID of the customer
        booking_details: Dictionary containing updated booking information
        booking_reference: Optional booking reference ID

    Returns:
        dict: Mock response simulating successful update
    """
    # Generate a mock booking reference if not provided
    if not booking_reference:
        booking_reference = f"BKG{frappe.utils.now_datetime().strftime('%Y%m%d%H%M%S')}"

    # Log the update for debugging
    frappe.log_error(
        title="Mock Booking Update",
        message=f"""
        Booking Reference: {booking_reference}
        WhatsApp ID: {whatsapp_id}
        CRM Lead: {crm_lead_doc.name if crm_lead_doc else 'N/A'}
        Updated Details: {json.dumps(booking_details, indent=2, default=str)}
        """
    )

    # Simulate successful API response
    mock_response = {
        "status": "success",
        "message": "Booking updated successfully",
        "data": {
            "booking_reference": booking_reference,
            "outlet": booking_details.get("outlet"),
            "booking_date": booking_details.get("booking_date"),
            "session": booking_details.get("session"),
            "pax": booking_details.get("pax"),
            "timeslot": booking_details.get("timeslot"),
            "customer_name": booking_details.get("customer_name"),
            "phone": booking_details.get("phone"),
            "treatment_type": booking_details.get("treatment_type"),
            "preferred_masseur": booking_details.get("preferred_masseur"),
            "third_party_voucher": booking_details.get("third_party_voucher"),
            "using_package": booking_details.get("using_package"),
            "updated_at": frappe.utils.now_datetime().isoformat()
        }
    }

    return mock_response


def handle_cancel_booking_api_mock(crm_lead_doc, whatsapp_id, booking_reference=None):
    """
    Mock function to simulate canceling a booking (for POC/testing purposes).

    Args:
        crm_lead_doc: CRM Lead document
        whatsapp_id: WhatsApp ID of the customer
        booking_reference: Optional booking reference ID

    Returns:
        dict: Mock response simulating successful cancellation
    """
    # Generate a mock booking reference if not provided
    if not booking_reference:
        booking_reference = f"BKG{frappe.utils.now_datetime().strftime('%Y%m%d%H%M%S')}"

    # Log the cancellation for debugging
    frappe.log_error(
        title="Mock Booking Cancellation",
        message=f"""
        Booking Reference: {booking_reference}
        WhatsApp ID: {whatsapp_id}
        CRM Lead: {crm_lead_doc.name if crm_lead_doc else 'N/A'}
        Cancelled At: {frappe.utils.now_datetime().isoformat()}
        """
    )

    # Simulate successful cancellation response
    mock_response = {
        "status": "success",
        "message": "Booking cancelled successfully",
        "data": {
            "booking_reference": booking_reference,
            "cancelled_at": frappe.utils.now_datetime().isoformat(),
            "refund_status": "pending",  # Mock refund status
            "cancellation_fee": 0  # Mock cancellation fee
        }
    }

    return mock_response


