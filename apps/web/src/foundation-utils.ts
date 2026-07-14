export const parseCommaList = (value: FormDataEntryValue | null) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
