import re
from datetime import datetime

class ValidationError(Exception):
    pass

import re

def validate_phone(country_code: str, phone: str) -> str:
    """
    Validate phone number and return full WhatsApp format.
    Example:
    country_code=91
    phone=8600945888
    returns 918600945888
    """

    phone = phone.strip()

    # Must be exactly 10 digits
    if not re.fullmatch(r'\d{10}', phone):
        raise ValidationError(
            'Mobile number must contain exactly 10 digits'
        )

    # Return full international format
    return f'{country_code}{phone}'


def validate_date(date_str: str):
    try:
        parsed = datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        raise ValidationError(
            'Date must be in YYYY-MM-DD format'
        )

    if parsed.date() < datetime.now().date():
        raise ValidationError(
            'Date cannot be in the past'
        )

    return parsed

def validate_date_range(from_date: str, to_date: str):
    start = validate_date(from_date)
    end = validate_date(to_date)

    if end < start:
        raise ValidationError(
            'End date cannot be earlier than start date'
        )

    return (start, end)

def validate_leave_days(days: int):
    if days <= 0:
        raise ValidationError(
            'Leave days must be greater than 0'
        )

    if days > 365:
        raise ValidationError(
            'Leave days cannot exceed 365'
        )

    return days

