from __future__ import annotations

from shared.message_sanitize import messages_for_anthropic_api


def test_messages_for_anthropic_api_strips_questionnaire_ui_blocks_but_keeps_text() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "先回答几个问题。"},
                {
                    "type": "questionnaire",
                    "id": "qq_test",
                    "questions": [
                        {
                            "id": "q1",
                            "text": "信号差持续多久？",
                            "type": "single_choice",
                            "options": [
                                {"id": "days", "label": "几天"},
                                {"id": "months", "label": "几个月"},
                            ],
                        }
                    ],
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "问卷回答：\n- 信号差持续多久？: 几个月"},
                {
                    "type": "questionnaire_response",
                    "questionnaire_id": "qq_test",
                    "answers": [
                        {
                            "question_id": "q1",
                            "option_ids": ["months"],
                            "labels": ["几个月"],
                        }
                    ],
                },
            ],
        },
    ]

    cleaned = messages_for_anthropic_api(messages)

    assert cleaned == [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "先回答几个问题。"}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "问卷回答：\n- 信号差持续多久？: 几个月"}],
        },
    ]
