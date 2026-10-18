// Phone numbers are stored in E.164 form (+12055550123), matching the backend's site_workers.
// Accepts common US formats ("(205) 555-0123", "205.555.0123", "1-205-555-0123") and
// international numbers written with a leading +. Returns null for anything else.
export function normalizePhone(input: string): string | null {
  const trimmed = input.trim();
  const digits = trimmed.replace(/\D/g, '');
  if (trimmed.startsWith('+')) {
    if (digits.startsWith('1')) return validUs(digits.slice(1));
    return digits.length >= 8 && digits.length <= 15 ? `+${digits}` : null;
  }
  if (digits.length === 10) return validUs(digits);
  if (digits.length === 11 && digits.startsWith('1')) return validUs(digits.slice(1));
  return null;
}

// NANP: 10 digits; area code and exchange can't start with 0 or 1.
function validUs(ten: string): string | null {
  return /^[2-9]\d{2}[2-9]\d{6}$/.test(ten) ? `+1${ten}` : null;
}

// +12055550123 -> (205) 555-0123; other countries stay in international form.
export function formatPhone(e164: string): string {
  const us = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164);
  return us ? `(${us[1]}) ${us[2]}-${us[3]}` : e164;
}
