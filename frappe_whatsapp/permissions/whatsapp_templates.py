import frappe
from frappe.utils.user import get_users_with_role

def whatsapp_template_query(user):
    if not user:
        user = frappe.session.user

    user_roles = frappe.get_roles()

    if "System Manager" in user_roles:
        return ""

    if "Booking Centre" in user_roles:
        users = get_users_with_role("CRM Assignee")
        return """`tabWhatsApp Templates`.owner IN ({0}) """.format(",".join(frappe.db.escape(user) for user in users[0]))

    return """`tabWhatsApp Templates`.owner = "{0}" """.format(user)