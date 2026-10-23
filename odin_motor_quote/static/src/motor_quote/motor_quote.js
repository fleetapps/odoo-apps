import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { MedicalQuote, amount } from "@odin_medical_quote/medical_quote/medical_quote";
import { quoteTypes } from "@odin_medical_quote/quote_types/quote_types";

const PERIODS = { 12: _t("1 year"), 6: _t("6 months"), 1: _t("1 month") };

function emptyVehicle() {
    return {
        class_id: "",
        cover: "comprehensive",
        value: "",
        year: "",
        seats: "",
        tonnage: "",
        period: 12,
        registration: "",
        make_model: "",
    };
}

/**
 * Add Motor Quote: the medical quote screen with a vehicle instead of a
 * family. The insurers' prices come back as plans with optional extras, so
 * picking, comparing, discounting, saving and accepting work the same.
 */
export class MotorQuote extends MedicalQuote {
    static model = "odin.motor.quote";
    static planKey = "rate_id";

    get title() {
        return _t("Add Motor Quote");
    }

    get detailsTemplate() {
        return "odin_motor_quote.MotorQuote.Details";
    }

    get optionsTitle() {
        return _t("Insurers and premiums");
    }

    get optionsHelp() {
        return _t("Each insurer's price for this vehicle. Select an insurer to compare it, and the extras to add.");
    }

    get nothingFound() {
        return _t("No insurer quotes this vehicle on these details. Check the type, the cover and the value.");
    }

    get pickHint() {
        return _t("Select an insurer, or any of its extras.");
    }

    get unavailableText() {
        const n = this.unavailablePlans.length;
        return n === 1
            ? _t("1 insurer cannot quote this vehicle")
            : _t("%s insurers cannot quote this vehicle", n);
    }

    get steps() {
        return [
            { n: 1, label: _t("Client and vehicle") },
            { n: 2, label: _t("Insurers") },
            { n: 3, label: _t("Compare") },
            { n: 4, label: _t("Save and share") },
        ];
    }

    emptyCover() {
        return emptyVehicle();
    }

    get vehicleClass() {
        const id = Number(this.state.cover.class_id);
        return (this.state.setup?.classes || []).find((c) => c.id === id);
    }

    get coverChoices() {
        const covers = this.vehicleClass?.covers || ["comprehensive", "tpo"];
        return [
            { key: "comprehensive", label: _t("Comprehensive") },
            { key: "tpo", label: _t("Third party only") },
        ].filter((c) => covers.includes(c.key));
    }

    get periodChoices() {
        const periods = this.state.cover.cover === "tpo" ? this.vehicleClass?.periods || [12] : [12];
        return periods.map((months) => ({ months, label: PERIODS[months] || _t("%s months", months) }));
    }

    pickClass(ev) {
        this.state.cover.class_id = Number(ev.target.value) || "";
        const covers = this.vehicleClass?.covers || [];
        if (covers.length && !covers.includes(this.state.cover.cover)) {
            this.setCover(covers[0]);
        }
        this.onCoverChange();
    }

    setCover(kind) {
        this.state.cover.cover = kind;
        if (kind === "comprehensive") {
            this.state.cover.period = 12;
        }
        this.onCoverChange();
    }

    coverProblem() {
        const cover = this.state.cover;
        if (!this.vehicleClass) {
            return _t("Choose the type of vehicle.");
        }
        if (cover.cover === "comprehensive" && !(Number(cover.value) > 0)) {
            return _t("Enter the vehicle's value: comprehensive cover is priced on it.");
        }
        if (this.vehicleClass.ask_seats && !(Number(cover.seats) > 0)) {
            return _t("Enter how many passengers the vehicle carries.");
        }
        if (this.vehicleClass.ask_tonnage && cover.cover === "tpo" && !(Number(cover.tonnage) > 0)) {
            return _t("Enter the vehicle's tonnage.");
        }
        return "";
    }

    get coverSummary() {
        const vehicle = this.state.results?.vehicle;
        if (!vehicle) {
            return "";
        }
        const parts = [];
        if (vehicle.text && vehicle.text !== vehicle.class) {
            parts.push(vehicle.text);
        }
        parts.push(vehicle.class, vehicle.cover);
        if (vehicle.value) {
            parts.push(_t("value %s", amount(vehicle.value)));
        }
        if (vehicle.age !== null && vehicle.age !== undefined) {
            parts.push(vehicle.age === 1 ? _t("1 year old") : _t("%s years old", vehicle.age));
        }
        return parts.join(" · ");
    }
}

registry.category("actions").add("odin_motor_quote", MotorQuote);

quoteTypes.add("motor", {
    sequence: 20,
    name: _t("Motor"),
    description: _t("Private cars, commercial vehicles, PSVs and motorcycles"),
    icon: "fa-car",
    action: "odin_motor_quote.action_motor_quote",
    tag: "odin_motor_quote",
});
