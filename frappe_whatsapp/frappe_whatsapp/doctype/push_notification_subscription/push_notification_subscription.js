// Copyright (c) 2025, Byondwave Innovations Sdn Bhd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Push Notification Subscription", {
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.add_custom_button("Send Push Notification", () => {
				let d = new frappe.ui.Dialog({
					title: "Enter details",
					fields: [
						{
							label: "Title",
							fieldname: "title",
							fieldtype: "Data",
							reqd: 1
						},
						{
							label: "Message",
							fieldname: "message",
							fieldtype: "Data",
							reqd: 1
						},
						{
							label: "URL",
							fieldname: "url",
							fieldtype: "Data"
						},
					],
					size: "medium", // small, large, extra-large 
					primary_action_label: "Submit",
					primary_action(values) {
						frappe.call({
							method: "send_push_notification",
							args: {
								title: values.title,
								message: values.message,
								url: values.url
							},
							freeze: true,
							freeze_message: "Sending push notifications...",
							callback: function (r) {
								frappe.msgprint({
									title: __("Success"),
									message: __("Successfully queued for sending push notification.")
								})
							},
							doc: frm.doc
						})
						d.hide();
					}
				});
				d.show();
			})
		}
	}
});
