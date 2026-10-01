import { ChatWindow } from "@mail/core/common/chat_window";
import { Component } from "@odoo/owl";

/** Shown instead of the composer once Meta's 24-hour window is closed (SPEC.md §28, R34). */
export class WhatsappComposerLock extends Component {
    static template = "whatsapp_connector.ComposerLock";
    static props = ["thread"];
}

ChatWindow.components = { ...ChatWindow.components, WhatsappComposerLock };
