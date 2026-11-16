"""Synthetic review output over the shared bounded provider request and usage path."""
from ..domain.qa import ProviderFailure, ProviderRequest
from ..domain.review import CARD_TYPES, REVIEW_PURPOSES
from ..domain.tasks import validate_json_output
from .qa_provider import FakeProvider


class FakeReviewProvider(FakeProvider):
    model = 'review-deterministic-v1'

    def default_output(self, request: ProviderRequest) -> dict:
        if request.purpose not in REVIEW_PURPOSES:
            raise ProviderFailure('INVALID_REQUEST', retryable=False, may_have_charged=False)
        try:
            instructions = validate_json_output(request.question)
        except (ValueError, TypeError, RecursionError):
            instructions = {}
        card_types = instructions.get('card_types', ['concept'])
        english = instructions.get('english_answer', False)
        max_cards = instructions.get('max_cards', 40)
        max_notes = instructions.get('max_notes', 80)
        if (not isinstance(card_types, list) or len(card_types) > 4
                or any(not isinstance(value, str) or value not in CARD_TYPES for value in card_types)
                or len(set(card_types)) != len(card_types) or type(english) is not bool
                or type(max_cards) is not int or not 0 <= max_cards <= 40
                or type(max_notes) is not int or not 1 <= max_notes <= 80
                or (request.purpose == 'review_cards' and not card_types)):
            raise ProviderFailure('INVALID_REQUEST', retryable=False, may_have_charged=False)
        citations = [entry.alias for entry in request.selection.entries]
        batch = citations[0]
        cards = []
        if card_types and max_cards:
            cards.append(dict(type=card_types[0], title='合成复习卡',
                front=f'来源 {batch} 中的合成课堂核心概念是什么？',
                back='这是用于验证生成与引用链路的合成答案。', supplement='',
                english='Synthetic classroom concept.' if english else '',
                english_meaning='合成课堂概念。' if english else '', source_ids=citations, missing=[]))
        return dict(schema_version=1, cards=cards,
            notes=[dict(section='structure', title='合成课堂总结',
                        body=f'来源 {batch} 用于验证结构化总结与版本流程的合成内容。',
                        supplement='', source_ids=citations, missing=[])])
