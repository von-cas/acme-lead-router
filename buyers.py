def map_installer_a(lead: dict, qualification: dict) -> dict:
    missing = [name for name in ("first_name", "phone_e164", "postal_code") if not lead.get(name)]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    return {
        "first_name": lead["first_name"],
        "last_name": lead["last_name"],
        "phone": lead["phone_e164"],
        "email": lead["email"],
        "zip": lead["postal_code"],
        "owns_home": lead["homeowner"],
        "avg_bill_usd": round(lead["monthly_bill"]),
        "lead_score": qualification["score"],
        "notes": qualification["summary"],
        "source": "acme-solar",
        "external_id": lead["lead_key"],
    }


BUYERS = {"installer-a": map_installer_a}
