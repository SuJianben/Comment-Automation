import time
import requests

QUERY = """query Products($cursor: String, $filter: String!) {
  products(first: 100, after: $cursor, query: $filter, sortKey: UPDATED_AT) {
    nodes { id title descriptionHtml tags status updatedAt }
    pageInfo { hasNextPage endCursor }
  }
}"""


class ShopifyClient:
    def __init__(self, domain, token, version, session=None):
        self.url = f"https://{domain}/admin/api/{version}/graphql.json"
        self.session = session or requests.Session()
        self.headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    def products(self, search):
        cursor = None
        while True:
            body = {"query": QUERY, "variables": {"cursor": cursor, "filter": search}}
            for attempt in range(5):
                try:
                    response = self.session.post(self.url, headers=self.headers, json=body, timeout=30)
                    if response.status_code == 429 or 500 <= response.status_code < 600:
                        response.raise_for_status()
                    response.raise_for_status()
                    result = response.json()
                    errors = result.get("errors", [])
                    if errors:
                        if all(e.get("extensions", {}).get("code") in ("THROTTLED", "INTERNAL_SERVER_ERROR") for e in errors) and attempt < 4:
                            time.sleep(2 ** attempt)
                            continue
                        raise RuntimeError("Shopify GraphQL 错误: " + "; ".join(e.get("message", "unknown") for e in errors))
                    page = result["data"]["products"]
                    break
                except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                    retryable = not isinstance(exc, requests.HTTPError) or exc.response.status_code == 429 or exc.response.status_code >= 500
                    if not retryable or attempt == 4:
                        raise
                    time.sleep(2 ** attempt)
            yield from page["nodes"]
            info = page["pageInfo"]
            if not info["hasNextPage"]:
                return
            next_cursor = info["endCursor"]
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("Shopify 分页游标无效")
            cursor = next_cursor
