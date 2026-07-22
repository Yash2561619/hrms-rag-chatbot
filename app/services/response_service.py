
def format_hr_response(answer: str) -> str:
    """
    Format AI responses for WhatsApp readability.
    """

    if not answer:
        return 'I could not generate an answer.'

    answer = answer.strip()

    # Split long sentences
    answer = answer.replace('. ', '.\\n')

    # Improve bullet spacing
    answer = answer.replace('•', '\\n•')

    # Remove excessive blank lines
    while '\\n\\n\\n' in answer:
        answer = answer.replace('\\n\\n\\n', '\\n\\n')

    return answer.strip()

