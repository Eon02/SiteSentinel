"""Phone number handling. Numbers are stored and sent in E.164 form, e.g. +12055550123."""

from typing import Annotated

import phonenumbers
from pydantic import AfterValidator

DEFAULT_REGION = "US"  # numbers without a +country code are read as US numbers


def normalize_phone(value: str) -> str:
    """Parse a phone number in any common format and return it as E.164.

    Raises ValueError for numbers that can't be valid.
    """
    try:
        number = phonenumbers.parse(value, DEFAULT_REGION)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(f"Not a phone number: {value!r}") from exc
    if not phonenumbers.is_valid_number(number):
        raise ValueError(f"Not a valid phone number: {value!r}")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


# Use as a field type to accept "(205) 555-0123", "205-555-0123", "+44 20 7946 0958", ...
Phone = Annotated[str, AfterValidator(normalize_phone)]
