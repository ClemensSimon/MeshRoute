"""
MeshRoute GitHub Reply Proxy

Tiny HTTP server that accepts reply requests from the React frontend
and posts them to GitHub Discussions via the GraphQL API.

Runs on MQTT LXC (192.168.178.129:8099).
GitHub token fetched from Credential Gate.

Usage:
    python3 github_reply_proxy.py
"""

import json
import ssl
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 8099
REPO_OWNER = "ClemensSimon"
REPO_NAME = "MeshRoute"
CREDENTIAL_GATE = "https://192.168.178.131:8046"

def get_credential(name):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(f"{CREDENTIAL_GATE}/credentials/{name}")
        resp = urllib.request.urlopen(req, context=ctx, timeout=5)
        return json.loads(resp.read()).get("value", "")
    except Exception as e:
        print(f"[ERROR] Credential {name}: {e}")
        return None

def github_graphql(token, query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=data,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read())

def get_discussion_id(token, number):
    """Get the GraphQL node ID for a discussion by number."""
    result = github_graphql(token, """query($owner: String!, $name: String!, $num: Int!) {
        repository(owner: $owner, name: $name) {
            discussion(number: $num) { id }
        }
    }""", {"owner": REPO_OWNER, "name": REPO_NAME, "num": number})
    return result.get("data", {}).get("repository", {}).get("discussion", {}).get("id")

def post_reply(token, discussion_id, body):
    """Post a comment to a discussion."""
    result = github_graphql(token, """mutation($id: ID!, $body: String!) {
        addDiscussionComment(input: {discussionId: $id, body: $body}) {
            comment { url }
        }
    }""", {"id": discussion_id, "body": body})
    errors = result.get("errors")
    if errors:
        return None, errors
    url = result.get("data", {}).get("addDiscussionComment", {}).get("comment", {}).get("url", "")
    return url, None

class ReplyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/github/reply":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length > 0 else {}

        discussion_number = body.get("discussion_number")
        reply_body = body.get("body", "").strip()

        if not discussion_number or not reply_body:
            self._json_response(400, {"error": "Missing discussion_number or body"})
            return

        token = get_credential("GITHUB_TOKEN")
        if not token:
            self._json_response(503, {"error": "GitHub token not available"})
            return

        disc_id = get_discussion_id(token, discussion_number)
        if not disc_id:
            self._json_response(404, {"error": f"Discussion #{discussion_number} not found"})
            return

        url, errors = post_reply(token, disc_id, reply_body)
        if errors:
            self._json_response(500, {"error": str(errors)})
        else:
            print(f"[REPLY] Posted to #{discussion_number}: {reply_body[:50]}... -> {url}")
            self._json_response(200, {"url": url, "ok": True})

    def do_OPTIONS(self):
        # CORS preflight
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, code, data):
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        print(f"[HTTP] {args[0]}")

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), ReplyHandler)
    print(f"GitHub Reply Proxy running on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
