"""Judge.me web review submissions for genuine, authorized customer feedback."""
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class JudgeMeResult:
    status_code: int
    data: Any
    retry_after: str | None = None


class JudgeMeError(Exception):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


class JudgeMeBadRequest(JudgeMeError):
    pass


class JudgeMeAuthenticationError(JudgeMeError):
    pass


class JudgeMeRateLimitError(JudgeMeError):
    pass


class JudgeMeAPIError(JudgeMeError):
    pass


class JudgeMeOutcomeUnknown(JudgeMeError):
    """The server may have received the request; check before resubmitting."""


class JudgeMeClient:
    ENDPOINT = "https://judge.me/api/v1/reviews"

    def __init__(self, shop_domain, api_token, *, platform="shopify", timeout=30, session=None):
        if not isinstance(shop_domain, str) or not shop_domain.strip():
            raise ValueError("shop_domain 不能为空")
        if not isinstance(api_token, str) or not api_token.strip():
            raise ValueError("api_token 不能为空")
        if platform != "shopify":
            raise ValueError("platform 必须是 shopify")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout 必须大于零")
        self.shop_domain = shop_domain.strip()
        self._api_token = api_token.strip()
        self.timeout = timeout
        self.session = session or requests

    def create_review(self, id, name, email, rating, title, body):
        """Submit one genuine review. A 2xx response does not confirm creation."""
        if isinstance(id, bool) or not str(id).isdigit() or int(id) <= 0:
            raise ValueError("id 必须是正整数 Shopify 商品 ID，不是 GraphQL GID")
        for field, value in (("name", name), ("email", email), ("body", body)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} 不能为空")
        if not isinstance(title, str):
            raise ValueError("title 必须是字符串")
        if type(rating) is not int or rating not in range(1, 6):
            raise ValueError("rating 必须是 1 至 5 的整数")
        payload = {"shop_domain": self.shop_domain, "platform": "shopify", "id": int(id),
                   "name": name, "email": email, "rating": rating, "title": title, "body": body}
        try:
            response = self.session.post(self.ENDPOINT, json=payload,
                headers={"X-Api-Token": self._api_token, "Accept": "application/json"},
                timeout=self.timeout, allow_redirects=False)
        except requests.RequestException as exc:
            raise JudgeMeOutcomeUnknown("请求失败，提交结果未知；核实后再重试") from exc
        try:
            data = response.json()
        except ValueError:
            data = response.text
        result = JudgeMeResult(response.status_code, data, response.headers.get("Retry-After"))
        if 200 <= response.status_code < 300:
            return result
        if response.status_code == 400 or response.status_code == 422:
            raise JudgeMeBadRequest("Judge.me 拒绝了请求参数", result)
        if response.status_code in (401, 403):
            raise JudgeMeAuthenticationError("Judge.me 鉴权失败或无权访问", result)
        if response.status_code == 429:
            raise JudgeMeRateLimitError("Judge.me 限流，请参考 Retry-After", result)
        if response.status_code == 408 or response.status_code >= 500:
            raise JudgeMeOutcomeUnknown("Judge.me 返回超时或服务端错误，提交结果可能未知；核实后再重试", result)
        raise JudgeMeAPIError(f"Judge.me 返回 HTTP {response.status_code}", result)
