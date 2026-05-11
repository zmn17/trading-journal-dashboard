"""
get_token.py - OAuth 2.0 token flow for cTrader Open API

Run this once to get your access_token and refresh_token.
It starts a local web server, opens the cTrader auth page in your browser,
catches the redirect callback, and exchanges the code for tokens.

Usage:
    python get_token.py
"""

import os
import sys
import json
import webbrowser
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:5000/callback")

AUTH_URL = "https://openapi.ctrader.com/apps/auth"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"


class CallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect from cTrader."""

    auth_code = None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/callback":
            params = parse_qs(parsed.query)
            if "code" in params:
                CallbackHandler.auth_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Authorised! You can close this tab.</h2></body></html>"
                )
            else:
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                error = params.get("error", ["unknown"])[0]
                self.wfile.write(
                    f"<html><body><h2>Error: {error}</h2></body></html>".encode()
                )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress request logs


def get_auth_code():
    """Open browser for user to authorise, wait for callback."""
    parsed = urlparse(REDIRECT_URI)
    port = parsed.port or 5000

    server = HTTPServer(("localhost", port), CallbackHandler)

    auth_uri = (
        f"{AUTH_URL}?"
        f"client_id={CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"scope=trading"
    )

    print(f"\n  Opening browser for cTrader authorisation...")
    print(f"  If it doesn't open, go to:\n  {auth_uri}\n")
    webbrowser.open(auth_uri)

    print("  Waiting for callback...")
    while CallbackHandler.auth_code is None:
        server.handle_request()

    server.server_close()
    return CallbackHandler.auth_code


def exchange_code_for_token(code: str) -> dict:
    """Exchange the auth code for access + refresh tokens."""
    resp = requests.get(
        TOKEN_URL,
        params={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    """Refresh an expired access token."""
    resp = requests.post(
        TOKEN_URL,
        params={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    resp.raise_for_status()
    return resp.json()


def save_tokens(token_data: dict):
    """Save tokens to a local file for reuse."""
    with open("tokens.json", "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"\n  Tokens saved to tokens.json")
    print(f"  Access Token:  {token_data['accessToken'][:20]}...")
    print(f"  Refresh Token: {token_data['refreshToken'][:20]}...")
    print(f"  Expires In:    {token_data['expiresIn']} seconds (~30 days)")


def main():
    if not CLIENT_ID or CLIENT_ID == "your_client_id_here":
        print("ERROR: Set CLIENT_ID and CLIENT_SECRET in .env first")
        sys.exit(1)

    # Check if we already have tokens and just need to refresh
    if os.path.exists("tokens.json"):
        with open("tokens.json") as f:
            existing = json.load(f)
        choice = input("  Existing tokens found. Refresh them? (y/n): ").strip().lower()
        if choice == "y":
            print("  Refreshing token...")
            token_data = refresh_access_token(existing["refreshToken"])
            save_tokens(token_data)
            return

    # Full OAuth flow
    code = get_auth_code()
    print(f"  Got auth code: {code[:10]}...")

    token_data = exchange_code_for_token(code)
    save_tokens(token_data)


if __name__ == "__main__":
    print("\n=== cTrader Open API Token Setup ===\n")
    main()
