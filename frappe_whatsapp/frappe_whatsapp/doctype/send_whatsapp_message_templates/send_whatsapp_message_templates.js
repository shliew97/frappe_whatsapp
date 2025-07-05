// Copyright (c) 2025, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on("Send WhatsApp Message Templates", {
	refresh(frm) {
		frm.trigger("hide_and_set_primary_action");
		frm.set_df_property("whatsapp_message_templates", "only_select", true);
		frm.set_query("whatsapp_message_templates", () => {
			return {
				filters: {
					"allow_trigger": 1
				}
			}
		})
	},
	hide_and_set_primary_action(frm) {
		frm.page.clear_primary_action();
		frm.page.set_primary_action("Send WhatsApp Message Template", () => {
			if (!frm.doc.mobile_no || !frm.doc.whatsapp_message_templates) {
				frappe.throw(__("Please key in Mobile No and select WhatsApp Message Template."))
			}
			frappe.call({
				method: "send_whatsapp_message_template",
				doc: frm.doc,
				callback: function (r) {
					if (r.success) {
						frappe.msgprint("Successfully queued to send WhatsApp Message Template.")
					}
				},
			});
		})
	},
	mobile_no(frm) {
		frm.trigger("hide_and_set_primary_action");
	},
	whatsapp_message_templates(frm) {
		frm.trigger("hide_and_set_primary_action");
	}
});
