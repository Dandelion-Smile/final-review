"""Structured answers shared by formal quizzes and mistake practice."""

from decimal import ROUND_HALF_UP, Decimal
from unicodedata import normalize

OBJECTIVE_TYPES = {"choice", "multiple_choice", "true_false", "fill_blank"}


def text_key(value: str) -> str:
    return " ".join(normalize("NFKC", value).split()).casefold()


def validate_extended_key(question: dict):
    kind = question["question_type"]
    correct = question.get("correct_options", [])
    blanks = question.get("blank_answers", [])
    if kind == "multiple_choice":
        options = question.get("options", [])
        if (
            len({text_key(option) for option in options}) != len(options)
            or len(correct) < 2
            or len(set(correct)) != len(correct)
            or any(not 0 <= index < len(options) for index in correct)
            or question.get("correct_option") is not None
        ):
            raise ValueError("多选题需要不同的选项及至少两个有效正确选项")
    elif correct:
        raise ValueError("只有多选题可以设置多个正确选项")
    if blanks and (kind != "fill_blank" or question.get("accepted_answers")):
        raise ValueError("多空填空与单空答案须分别配置")
    if any(not values or not all(text_key(value) for value in values) for values in blanks):
        raise ValueError("每个空至少需要一个可接受答案")


def objective_fraction(question: dict, answer) -> Decimal:
    if answer is None:
        return Decimal(0)
    kind = question["question_type"]
    if kind == "choice":
        return Decimal(int(type(answer) is int and answer == question["correct_option"]))
    if kind == "multiple_choice":
        correct = set(question["correct_options"])
        selected = set(answer) if isinstance(answer, list) else set()
        return Decimal(len(selected)) / len(correct) if selected <= correct else Decimal(0)
    if kind == "true_false":
        return Decimal(int(type(answer) is bool and answer is question["boolean_answer"]))
    if kind == "fill_blank":
        blanks = question.get("blank_answers")
        if blanks:
            if not isinstance(answer, list) or len(answer) != len(blanks):
                return Decimal(0)
            matched = sum(
                bool(text_key(value)) and text_key(value) in {text_key(key) for key in keys}
                for value, keys in zip(answer, blanks, strict=True)
            )
            return Decimal(matched) / len(blanks)
        return Decimal(
            int(
                isinstance(answer, str)
                and text_key(answer) in {text_key(value) for value in question["accepted_answers"]}
            )
        )
    raise ValueError("主观题不能用客观题规则评分")


def objective_score(question: dict, fraction: Decimal) -> float:
    maximum = Decimal(str(question["score"]))
    if fraction in {0, 1}:
        return float(maximum * fraction)
    earned = (maximum * fraction).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(min(maximum, earned))
