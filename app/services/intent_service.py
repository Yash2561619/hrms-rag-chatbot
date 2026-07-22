def classify_intent(message: str):

    msg = message.lower()

    # Leave Balance
    if any(k in msg for k in [
        "leave balance",
        "leaves balance",
        "leave status",
        "leave left",
        "leaves left",
        "leave remaining",
        "remaining leave",
        "how many leaves",
        "how many leave"
    ]):
        return "leave_balance"

    # Leave History
    if any(k in msg for k in [
        "leave history",
        "previous leaves",
        "past leaves",
        "leave records",
        "all leave requests"
    ]):
        return "leave_history"

    # Salary
    if any(k in msg for k in [
        "salary",
        "salary slip",
        "payslip",
        "pay slip"
    ]):
        return "salary_slip"

    # Health Insurance
    if any(k in msg for k in [
        "claim process",
        "claim health insurance",
        "approove health insurance",
        "clain health policy"
        "how to claim"
    ]):
        return "health_insurance_video"

    # Leave Application
    if "leave" in msg and any(word in msg for word in [
        "apply",
        "applay",      # common typo
        "want",
        "need",
        "take",
        "request"
    ]):
        return "apply_leave"

    return "rag"
