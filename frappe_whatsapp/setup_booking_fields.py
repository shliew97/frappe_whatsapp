"""
Setup script to add pending_booking_data custom field to CRM Lead.

Run this from bench console:
bench --site your-site-name execute frappe_whatsapp.setup_booking_fields.setup_custom_fields
"""

import frappe


def setup_custom_fields():
    """Create custom field for storing pending booking data in CRM Lead."""

    # Check if custom field already exists
    if frappe.db.exists("Custom Field", {"dt": "CRM Lead", "fieldname": "pending_booking_data"}):
        print("Custom field 'pending_booking_data' already exists in CRM Lead")
        return

    # Create the custom field
    custom_field = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "CRM Lead",
        "label": "Pending Booking Data",
        "fieldname": "pending_booking_data",
        "fieldtype": "Long Text",
        "insert_after": "custom_latest_whatsapp_message_templates",
        "hidden": 1,
        "read_only": 1,
        "description": "Stores partial booking information while collecting required fields from customer"
    })

    custom_field.insert(ignore_permissions=True)
    frappe.db.commit()

    print("✅ Successfully created 'pending_booking_data' custom field in CRM Lead")
    print("The booking collection system is now ready to use!")


if __name__ == "__main__":
    setup_custom_fields()
