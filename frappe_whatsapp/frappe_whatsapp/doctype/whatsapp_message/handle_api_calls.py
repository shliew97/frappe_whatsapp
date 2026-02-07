import frappe, requests, json, re, os

BOOKING_ENPOINT = "/api/method/soma_wellness.api.make_bookings"
REGISTER_STAFF_FACE_ENDPOINT = "/api/method/healthland_pos.api.register_staff_face"
CREATE_AL_APPLICATION_ENDPOINT = "/api/method/healthland_pos.api.create_al_application"


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
        "message": "Your booking has been confirmed!",
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


def handle_register_staff_face_api(mobile_no, selfie_image):
    """
    Handle register staff face API call for clock in registration.

    Args:
        mobile_no: Staff member's mobile number
        selfie_image: Base64 encoded selfie image

    Returns:
        dict: API response
    """
    integration_settings = frappe.db.get_all("Integration Settings", filters={"active": 1}, pluck="name")
    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc("Integration Settings", integration_setting)
        url = integration_settings_doc.site_url + REGISTER_STAFF_FACE_ENDPOINT

        headers = {
            "Authorization": "Basic {0}".format(integration_settings_doc.get_password("access_token")),
            "Content-Type": "application/json"
        }

        request_body = {
            "mobile_no": mobile_no,
            "selfie_image": selfie_image
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


def handle_leave_application_api(whatsapp_id, leave_date, reason):
    """
    Handle leave application API call to create an annual leave application.

    Args:
        whatsapp_id: Staff member's WhatsApp ID / phone number
        leave_date: Date for the leave in YYYY-MM-DD format
        reason: Reason for the leave application

    Returns:
        dict: API response
    """
    integration_settings = frappe.db.get_all("Integration Settings", filters={"active": 1}, pluck="name")
    for integration_setting in integration_settings:
        integration_settings_doc = frappe.get_doc("Integration Settings", integration_setting)
        url = integration_settings_doc.site_url + CREATE_AL_APPLICATION_ENDPOINT

        headers = {
            "Authorization": "Basic {0}".format(integration_settings_doc.get_password("access_token")),
            "Content-Type": "application/json"
        }

        request_body = {
            "phone_number": whatsapp_id,
            "date": leave_date,
            "reason": reason
        }

        try:
            response = requests.post(url, data=json.dumps(request_body, default=str), headers=headers, timeout=30)
            response.raise_for_status()
            response_data = response.json()
            return response_data
        except requests.Timeout:
            frappe.log_error("Leave Application API Timeout", f"Request timed out for {whatsapp_id}")
            return {"success": False, "message": "Request timed out. Please try again."}
        except requests.RequestException as e:
            frappe.log_error("Leave Application API Error", f"Error for {whatsapp_id}: {str(e)}")
            return {"success": False, "message": f"An error occurred: {str(e)}"}

    return {"success": False, "message": "No active integration settings found"}
