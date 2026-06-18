from sumbot.summary_validation import validate_summary_output


def test_validate_summary_output_accepts_plain_summary():
    result = validate_summary_output(
        "User_1 сообщил о сбое Redis, а User_2 предложил перезапуск.",
        "User_1: Redis упал\nUser_2: перезапусти Redis",
    )

    assert result.is_valid is True
    assert result.reasons == ()


def test_validate_summary_output_rejects_markdown_and_role_tags():
    result = validate_summary_output(
        "# Итог\nUser_1 [советует]: **перезапустить Redis**",
        "User_1: перезапусти Redis",
    )

    assert result.is_valid is False
    assert result.reasons == ("markup", "role_tag")


def test_validate_summary_output_rejects_unattributed_political_stance():
    result = validate_summary_output(
        "Путин здесь однозначно прав, его решение справедливое.",
        "User_1: обсуждает Путина и политику",
    )

    assert result.is_valid is False
    assert result.reasons == ("political_stance",)


def test_validate_summary_output_accepts_attributed_political_opinion():
    result = validate_summary_output(
        "User_1 написал, что Путин прав, а User_2 с этим не согласился.",
        "User_1: Путин прав\nUser_2: не согласен",
    )

    assert result.is_valid is True


def test_validate_summary_output_ignores_nonpolitical_evaluation():
    result = validate_summary_output(
        "User_1 правильно перезапустил Redis и заслуженно закрыл инцидент.",
        "User_1: перезапустил Redis",
    )

    assert result.is_valid is True


def test_validate_summary_output_does_not_mix_political_and_game_sentences():
    result = validate_summary_output(
        "User_1 упомянул выборы. Потом команда решила, что надо победить босса.",
        "User_1: выборы\nUser_2: надо победить босса",
    )

    assert result.is_valid is True
