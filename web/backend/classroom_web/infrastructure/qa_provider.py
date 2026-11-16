"""Explicitly configured, single-send OpenAI-compatible QA transport."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re
import threading
from urllib.parse import urlsplit

import httpx

from ..domain.billing import Usage
from ..domain.qa import (ProviderFailure, ProviderReply, ProviderRequest,
                        input_token_bound, messages_for)
from ..domain.tasks import validate_json_output


def _check_request(request: ProviderRequest, *, context_limit: int, output_limit: int):
    entries = request.selection.entries
    if (not isinstance(request.question, str) or not request.question.strip()
            or len(request.question) > 12_000 or not 1 <= len(entries) <= 64
            or type(request.max_output_tokens) is not int
            or not 1 <= request.max_output_tokens <= output_limit
            or request.selection.token_bound != input_token_bound(request.question, entries)
            or request.selection.token_bound + request.max_output_tokens > context_limit):
        raise ProviderFailure('INVALID_REQUEST', retryable=False, may_have_charged=False)
    classrooms = {entry.source.class_session_id for entry in entries}
    if len(classrooms) != 1:
        raise ProviderFailure('INVALID_REQUEST', retryable=False, may_have_charged=False)
    for index, entry in enumerate(entries, 1):
        if (entry.alias != f'S{index}' or entry.source.owner_id != request.owner_id
                or not 0 <= entry.excerpt_start < entry.excerpt_end <= len(entry.source.text)
                or entry.excerpt != entry.source.text[entry.excerpt_start:entry.excerpt_end]
                or entry.token_bound != len(entry.excerpt.encode('utf-8'))):
            raise ProviderFailure('INVALID_REQUEST', retryable=False, may_have_charged=False)
    if request.cancel_event is not None and request.cancel_event.is_set():
        raise ProviderFailure('CANCELLED', retryable=False, may_have_charged=False)


class FakeProvider:
    """Deterministic fixture provider with explicit usage and no external I/O."""
    simulation_only = True
    provider = 'fake'
    model = 'qa-deterministic-v1'

    def __init__(self, *, output: str | None = None, missing_usage: bool = False):
        self.output = output
        self.missing_usage = missing_usage
        self.calls = 0

    def generate(self, request: ProviderRequest) -> ProviderReply:
        _check_request(request, context_limit=278_528, output_limit=16_384)
        self.calls += 1
        # The fixed answer prevents fixture source instructions from becoming
        # behavior. Citation resolution is exercised independently by the worker.
        output = self.output
        if output is None:
            output = json.dumps(dict(answer_markdown='合成课堂回答。',
                                     source_ids=[entry.alias for entry in request.selection.entries]),
                                ensure_ascii=False, separators=(',', ':'))
        usage = None
        if not self.missing_usage:
            usage = Usage(provider_request_id='fake-' + str(request.attempt_id), meter='tokens',
                          revision=1, quantities=dict(input_tokens=request.selection.token_bound,
                                                      output_tokens=min(32, request.max_output_tokens)),
                          final=True)
        return ProviderReply(raw_output=output, usage=usage)


@dataclass(frozen=True, kw_only=True)
class OpenAIConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    provider: str = 'openai-compatible'
    enabled: bool = False
    connect_timeout: float = 5.0
    read_timeout: float = 20.0
    request_timeout: float = 30.0
    max_output_tokens: int = 4096
    context_token_limit: int = 16_384
    max_concurrency: int = 2
    max_response_bytes: int = 262_144

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or not self.model or len(self.model) > 128
                or not self.provider or len(self.provider) > 64
                or not isinstance(self.api_key, str) or not self.api_key
                or '\r' in self.api_key or '\n' in self.api_key
                or type(self.enabled) is not bool):
            raise ValueError('invalid explicit provider configuration')
        deadlines = (self.connect_timeout, self.read_timeout, self.request_timeout)
        if any(type(value) not in (int, float) or not 0 < value <= 120 for value in deadlines):
            raise ValueError('provider deadlines must be positive and bounded')
        integers = (self.max_output_tokens, self.context_token_limit,
                    self.max_concurrency, self.max_response_bytes)
        if (any(type(value) is not int for value in integers)
                or not 1 <= self.max_output_tokens <= 16_384
                or not self.max_output_tokens < self.context_token_limit <= 278_528
                or not 1 <= self.max_concurrency <= 16
                or not 1024 <= self.max_response_bytes <= 1_048_576):
            raise ValueError('invalid provider resource bounds')


class OpenAIProvider:
    """Candidate real adapter. Application wiring remains simulation-only.

    The synchronous worker entry runs an asynchronous HTTP transport so the
    overall deadline can cancel and close an in-flight response. There are no
    implicit SDK, HTTP, or application retries and no model-driven tool path.
    """
    simulation_only = False

    def __init__(self, config: OpenAIConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.provider = config.provider
        self.model = config.model
        self._transport = transport
        self._slots = threading.BoundedSemaphore(config.max_concurrency)

    def generate(self, request: ProviderRequest) -> ProviderReply:
        if not self.config.enabled:
            raise ProviderFailure('PROVIDER_DISABLED', retryable=False, may_have_charged=False)
        _check_request(request, context_limit=self.config.context_token_limit,
                       output_limit=self.config.max_output_tokens)
        if request.permit is None:
            raise ProviderFailure('PERMIT_REQUIRED', retryable=False, may_have_charged=False)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ProviderFailure('SYNC_WORKER_REQUIRED', retryable=False, may_have_charged=False)
        if not self._slots.acquire(blocking=False):
            raise ProviderFailure('PROVIDER_BUSY', retryable=True, may_have_charged=False)
        try:
            request.permit.consume(request, self.provider, self.model)
            return asyncio.run(self._bounded_request(request))
        finally:
            self._slots.release()

    async def _bounded_request(self, request: ProviderRequest) -> ProviderReply:
        try:
            async with asyncio.timeout(self.config.request_timeout):
                if request.cancel_event is None:
                    return await self._send(request)
                sending = asyncio.create_task(self._send(request))
                cancellation = asyncio.create_task(self._wait_cancel(request))
                try:
                    done, _ = await asyncio.wait((sending, cancellation),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if cancellation in done:
                        raise ProviderFailure('CANCELLED', retryable=False, may_have_charged=True)
                    return await sending
                finally:
                    for task in (sending, cancellation):
                        task.cancel()
                    await asyncio.gather(sending, cancellation, return_exceptions=True)
        except (TimeoutError, httpx.TimeoutException) as error:
            unsubmitted = isinstance(error, (httpx.ConnectTimeout, httpx.PoolTimeout))
            raise ProviderFailure('PROVIDER_TIMEOUT', retryable=True,
                                  may_have_charged=not unsubmitted) from None
        except httpx.ConnectError:
            raise ProviderFailure('PROVIDER_CONNECT_FAILED', retryable=True,
                                  may_have_charged=False) from None
        except httpx.HTTPError:
            raise ProviderFailure('PROVIDER_TRANSPORT_FAILED', retryable=True,
                                  may_have_charged=True) from None

    async def _wait_cancel(self, request: ProviderRequest):
        while not request.cancel_event.is_set():
            await asyncio.sleep(0.01)

    async def _send(self, request: ProviderRequest) -> ProviderReply:
        config = self.config
        timeout = httpx.Timeout(connect=min(config.connect_timeout, config.request_timeout),
                                read=min(config.read_timeout, config.request_timeout),
                                write=min(config.read_timeout, config.request_timeout),
                                pool=min(config.connect_timeout, config.request_timeout))
        transport = self._transport
        if transport is None:
            transport = httpx.AsyncHTTPTransport(retries=0)
        payload = dict(model=config.model, messages=messages_for(request.question, request.selection.entries),
                       max_tokens=request.max_output_tokens, stream=False,
                       response_format=dict(type='json_object'), tool_choice='none')
        headers = {'Authorization': 'Bearer ' + config.api_key,
                   'Content-Type': 'application/json', 'Accept': 'application/json'}
        async with httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False,
                                     follow_redirects=False) as client:
            async with client.stream('POST', config.base_url.rstrip('/') + '/chat/completions',
                                     headers=headers, json=payload) as response:
                if response.status_code != 200:
                    retryable = response.status_code in (408, 429) or response.status_code >= 500
                    raise ProviderFailure('PROVIDER_REJECTED', retryable=retryable, may_have_charged=True)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > config.max_response_bytes:
                        raise ProviderFailure('PROVIDER_RESPONSE_LIMIT', retryable=False,
                                              may_have_charged=True)
                    data.extend(chunk)
        return self._parse_response(bytes(data), request)

    def _parse_response(self, data: bytes, request: ProviderRequest) -> ProviderReply:
        try:
            body = validate_json_output(data.decode('utf-8'))
            choices = body['choices']
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError('invalid choices')
            choice = choices[0]
            message = choice['message']
            output = message['content']
            if (choice.get('finish_reason') != 'stop' or message.get('role') != 'assistant'
                    or message.get('tool_calls') or message.get('function_call')
                    or not isinstance(output, str) or not output.strip()
                    or len(output.encode('utf-8')) > min(self.config.max_response_bytes,
                                                       request.max_output_tokens * 128)):
                raise ValueError('invalid assistant response')
            usage = self._usage(body, request)
            return ProviderReply(raw_output=output, usage=usage)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
            raise ProviderFailure('PROVIDER_RESPONSE_INVALID', retryable=False,
                                  may_have_charged=True) from None

    @staticmethod
    def _usage(body: dict, request: ProviderRequest) -> Usage | None:
        reported = body.get('usage')
        if reported is None:
            return None
        identity = body.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', identity):
            raise ValueError('invalid usage identity')
        incoming, outgoing = reported['prompt_tokens'], reported['completion_tokens']
        if (type(incoming) is not int or type(outgoing) is not int
                or not 0 <= incoming <= request.selection.token_bound
                or not 0 <= outgoing <= request.max_output_tokens):
            raise ValueError('usage exceeded reserved request bounds')
        usage = Usage(provider_request_id=identity, meter='tokens', revision=1,
                      quantities=dict(input_tokens=incoming, output_tokens=outgoing), final=True)
        usage.validate()
        return usage
