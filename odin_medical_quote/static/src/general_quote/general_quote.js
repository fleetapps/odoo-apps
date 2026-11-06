import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { MedicalQuote, amount } from "@odin_medical_quote/medical_quote/medical_quote";

/**
 * The standard quote screen: a class the office added itself (Accident,
 * Fire, Travel...) quoted like motor -- the medical screen with the class's
 * own questions (an amount, the people covered, a line of details) instead
 * of a family. Every insurer's product in the class comes back as a plan
 * with optional extras, so picking, comparing, discounting, saving and
 * accepting work the same.
 */
export class GeneralQuote extends MedicalQuote {
    static model = "odin.general.quote";
    static planKey = "product_id";

    get classId() {
        return Number(this.params.class_id) || false;
    }

    get insuranceClass() {
        return this.state.setup?.class || {};
    }

    get title() {
        if (this.insuranceClass.name) {
            return _t("Add %s Quote", this.insuranceClass.name);
        }
        return this.props.action?.name || _t("Add Quote");
    }

    get detailsTemplate() {
        return "odin_medical_quote.GeneralQuote.Details";
    }

    get optionsTitle() {
        return _t("Insurers and premiums");
    }

    get optionsHelp() {
        return _t("Each insurer's price for this cover. Select an insurer to compare it, and the extras to add.");
    }

    get nothingFound() {
        if (!this.state.setup?.products) {
            return _t("There are no products in this class yet. Add each insurer's product in Cover Quotes ▸ Products.");
        }
        return _t("No insurer quotes these details. Check the amount.");
    }

    get pickHint() {
        return _t("Select an insurer, or any of its extras.");
    }

    get unavailableText() {
        const n = this.unavailablePlans.length;
        return n === 1
            ? _t("1 insurer cannot quote these details")
            : _t("%s insurers cannot quote these details", n);
    }

    countText(n) {
        return n === 1 ? _t("1 insurer") : _t("%s insurers", n);
    }

    get planColumn() {
        return _t("Insurer");
    }

    get compareTitle() {
        return _t("Compare insurers");
    }

    get compareHelp() {
        return _t("Adjust each insurer's price: take off extras, give a discount, add a policy charge. Totals include taxes, levies and stamp duty.");
    }

    get steps() {
        return [
            { n: 1, label: _t("Client and cover") },
            { n: 2, label: _t("Insurers") },
            { n: 3, label: _t("Compare") },
            { n: 4, label: _t("Save and share") },
        ];
    }

    setupKwargs() {
        return { ...super.setupKwargs(), class_id: this.classId };
    }

    emptyCover() {
        return { class_id: this.classId, amount: "", people: 1, details: "" };
    }

    coverProblem() {
        const cover = this.state.cover;
        const klass = this.insuranceClass;
        if (klass.ask_amount && !(Number(cover.amount) > 0)) {
            return _t("Enter the %s.", (klass.amount_label || _t("sum insured")).toLowerCase());
        }
        if (klass.ask_people && !(Number(cover.people) >= 1)) {
            return _t("Enter how many people to cover.");
        }
        return "";
    }

    get coverSummary() {
        const risk = this.state.results?.risk;
        if (!risk) {
            return "";
        }
        const parts = [risk.class];
        if (risk.amount) {
            parts.push(`${risk.amount_label} ${amount(risk.amount)}`);
        }
        if (risk.people) {
            parts.push(risk.people === 1 ? _t("1 person") : _t("%s people", risk.people));
        }
        if (risk.details) {
            parts.push(risk.details);
        }
        return parts.join(" · ");
    }
}

registry.category("actions").add("odin_general_quote", GeneralQuote);
