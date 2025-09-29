#!/usr/bin/env python3
import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests


def build_headers(api_token: str, use_bearer: bool = False) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if use_bearer:
        headers["Authorization"] = f"Bearer {api_token}"
    else:
        headers["X-AUTH-YW-API-TOKEN"] = api_token
    return headers


def request_json(method: str, url: str, headers: Dict[str, str], verify_tls: bool) -> Tuple[int, Any, str]:
    try:
        response = requests.request(method=method, url=url, headers=headers, timeout=30, verify=verify_tls)
        text = response.text
        try:
            data = response.json()
        except ValueError:
            data = None
        return response.status_code, data, text
    except requests.RequestException as exc:
        raise SystemExit(f"HTTP request failed: {exc}")


def discover_customer_uuid(base_url: str, headers: Dict[str, str], verify_tls: bool) -> Optional[str]:
    # Try v2 me endpoint (if available)
    status, data, _ = request_json("GET", f"{base_url}/api/v2/me", headers, verify_tls)
    if status == 200 and isinstance(data, dict):
        # Some deployments include customer UUID here
        for key in ("customerUUID", "customerId", "customer_id", "customerUuid"):
            if key in data and isinstance(data[key], str) and data[key]:
                return data[key]

    # Fallback: list customers v1
    status, data, _ = request_json("GET", f"{base_url}/api/v1/customers", headers, verify_tls)
    if status == 200 and isinstance(data, list) and data:
        # Choose the first customer by default
        candidate = data[0]
        for key in ("uuid", "customerUUID", "id"):
            if isinstance(candidate, dict) and key in candidate and isinstance(candidate[key], str):
                return candidate[key]
    return None


def fetch_universes(base_url: str, customer_uuid: str, headers: Dict[str, str], verify_tls: bool) -> List[Dict[str, Any]]:
    # Prefer v2 if available
    candidates = [
        f"{base_url}/api/v2/customers/{customer_uuid}/universes",
        f"{base_url}/api/v1/customers/{customer_uuid}/universes",
    ]

    last_error_text: Optional[str] = None
    for url in candidates:
        status, data, text = request_json("GET", url, headers, verify_tls)
        if status == 200 and isinstance(data, list):
            return data
        last_error_text = f"{status}: {text}"

    raise SystemExit(f"Failed to fetch universes. Last error: {last_error_text}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch universes from YugabyteDB Anywhere (YBA) and print JSON",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base-url", default=os.getenv("YBA_BASE_URL"), help="Base URL to YBA, e.g. https://yba.example.com")
    parser.add_argument("--api-token", default=os.getenv("YBA_API_TOKEN"), help="API token for YBA (env YBA_API_TOKEN)")
    parser.add_argument("--customer-uuid", default=os.getenv("YBA_CUSTOMER_UUID"), help="Customer UUID; if omitted will be discovered")
    parser.add_argument("--bearer", action="store_true", help="Use Authorization: Bearer header instead of X-AUTH-YW-API-TOKEN")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    if not args.base_url:
        raise SystemExit("--base-url or YBA_BASE_URL is required")
    if not args.api_token:
        raise SystemExit("--api-token or YBA_API_TOKEN is required")

    # Normalize base URL (strip trailing slash)
    base_url: str = args.base_url.rstrip("/")
    headers = build_headers(args.api_token, use_bearer=args.bearer)
    verify_tls: bool = not args.insecure

    customer_uuid: Optional[str] = args.customer_uuid
    if not customer_uuid:
        customer_uuid = discover_customer_uuid(base_url, headers, verify_tls)
        if not customer_uuid:
            raise SystemExit(
                "Unable to discover customer UUID. Provide --customer-uuid or set YBA_CUSTOMER_UUID"
            )

    universes = fetch_universes(base_url, customer_uuid, headers, verify_tls)

    if args.pretty:
        print(json.dumps(universes, indent=2, sort_keys=False))
    else:
        print(json.dumps(universes, separators=(",", ":")))


if __name__ == "__main__":
    main()

