import { DiscussContent } from "@mail/core/public_web/discuss_content";
import { WhatsappComposerLock } from "@whatsapp_connector/core/common/whatsapp_composer_lock";

DiscussContent.components = { ...DiscussContent.components, WhatsappComposerLock };
