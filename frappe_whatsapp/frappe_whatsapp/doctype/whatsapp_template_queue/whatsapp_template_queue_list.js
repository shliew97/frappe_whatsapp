frappe.listview_settings["WhatsApp Template Queue"] = {
    onload: function (listview) {
        listview.page.add_action_item(__("Enqueue"), () => {
            enqueue(listview);
        });
    }
};

function enqueue(listview) {
    let checked_items = listview.get_checked_items();
    const whatsapp_template_queues = [];
    checked_items.forEach((item) => {
        if (item.status == "Pending") {
            whatsapp_template_queues.push(item.name);
        }
    });
    if (whatsapp_template_queues.length > 0) {
        frappe.prompt({
            label: "WhatsApp Message Templates",
            fieldname: "whatsapp_message_template",
            fieldtype: "Link",
            options: "WhatsApp Message Templates",
            reqd: 1
        }, (values) => {
            frappe.call({
                method: "frappe_whatsapp.api.enqueue_send_whatsapp_template",
                args: {
                    whatsapp_message_template: values.whatsapp_message_template,
                    whatsapp_template_queues: whatsapp_template_queues
                },
                freeze: true,
                callback: (r) => {
                    listview.refresh();
                }
            })
        })
    }
    else {
        frappe.msgprint(__("Selected document must be in Pending status."));
    }
}